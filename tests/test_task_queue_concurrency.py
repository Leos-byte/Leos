"""Tests for the Postgres-backed task queue with atomic multi-worker claim.

The queue's SQL is exercised in CI against a thread-safe SQLite connection that
translates the Postgres dialect (``%s`` params, ``FOR UPDATE SKIP LOCKED``,
``DOUBLE PRECISION``) so the concurrency semantics run without a server. Each
statement is atomic in SQLite, so racing workers still prove the exactly-once
claim property of the conditional ``UPDATE ... RETURNING``. A real-server round
trip runs only when ``LEOS_TEST_POSTGRES_DSN`` is set.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path
from typing import Any
from unittest import mock

from leos_agent.enums import GoalStatus, TaskStatus
from leos_agent.goals import Goal
from leos_agent.plans import ActionStep, TransactionPlan
from leos_agent.runtime_store import RuntimeStoreError
from leos_agent.task_queue import RetryPolicy, TaskRunner, TimeoutPolicy
from leos_agent.task_queue_backends import PostgresTaskQueue


def _echo_plan(goal_desc: str = "test") -> TransactionPlan:
    goal = Goal(description=goal_desc, success_criteria=["ok"], stop_conditions=["done"])
    return TransactionPlan(goal=goal, steps=[ActionStep("echo", {"message": "hi"}, "test")])


def _translate(sql: str) -> str:
    return sql.replace("FOR UPDATE SKIP LOCKED", "").replace("%s", "?").replace("DOUBLE PRECISION", "REAL")


class _PgLikeCursor:
    """Cursor adapter translating Postgres SQL to SQLite for tests."""

    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock) -> None:
        self._cursor = cursor
        self._lock = lock

    def __enter__(self) -> _PgLikeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        self._cursor.close()
        self._lock.release()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _PgLikeCursor:
        self._cursor.execute(_translate(sql), params)
        return self

    def _as_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {desc[0]: row[idx] for idx, desc in enumerate(self._cursor.description)}

    def fetchone(self) -> dict[str, Any] | None:
        return self._as_dict(self._cursor.fetchone())

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._cursor.fetchall()
        return [d for d in (self._as_dict(r) for r in rows) if d is not None]


class _PgLikeConnection:
    """Thread-safe Postgres-shaped connection backed by a real SQLite file.

    A re-entrant lock serializes statements across worker threads; each
    translated statement stays atomic exactly as it would be under Postgres
    row locking, so racing claims exercise the conditional-update semantics.
    """

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.RLock()

    def cursor(self) -> _PgLikeCursor:
        self._lock.acquire()
        return _PgLikeCursor(self._conn.cursor(), self._lock)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _QueueTestBase(unittest.TestCase):
    def make_queue(self, **kwargs: Any) -> PostgresTaskQueue:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._db = Path(self._dir.name) / "queue.sqlite"
        return PostgresTaskQueue(connection=_PgLikeConnection(self._db), **kwargs)

    def reopen(self, queue: PostgresTaskQueue, **kwargs: Any) -> PostgresTaskQueue:
        queue.close()
        return PostgresTaskQueue(connection=_PgLikeConnection(self._db), **kwargs)


class PostgresTaskQueueBasicTests(_QueueTestBase):
    def test_enqueue_claim_complete_round_trip(self) -> None:
        queue = self.make_queue()
        task = queue.enqueue(_echo_plan())
        self.assertEqual(task.status, TaskStatus.QUEUED)
        claimed = queue.claim("w1", now=100.0)
        assert claimed is not None
        self.assertEqual(claimed.task_id, task.task_id)
        self.assertEqual(claimed.status, TaskStatus.RUNNING)
        self.assertEqual(claimed.locked_by, "w1")
        self.assertEqual(claimed.attempts, 1)
        done = queue.complete(claimed.task_id, "w1", now=101.0)
        self.assertEqual(done.status, TaskStatus.SUCCEEDED)
        self.assertIsNone(done.locked_by)

    def test_claim_empty_queue_returns_none(self) -> None:
        queue = self.make_queue()
        self.assertIsNone(queue.claim("w1"))

    def test_claim_order_is_fifo(self) -> None:
        queue = self.make_queue()
        ids = [queue.enqueue(_echo_plan(f"t{i}")).task_id for i in range(3)]
        claimed = [queue.claim("w1", now=float(i)) for i in range(3)]
        self.assertEqual([t.task_id for t in claimed if t is not None], ids)

    def test_state_survives_reopen(self) -> None:
        queue = self.make_queue()
        task = queue.enqueue(_echo_plan())
        queue.claim("w1", now=100.0)
        queue2 = self.reopen(queue)
        loaded = queue2.get(task.task_id)
        self.assertEqual(loaded.status, TaskStatus.RUNNING)
        self.assertEqual(loaded.locked_by, "w1")

    def test_get_unknown_task_raises_key_error(self) -> None:
        queue = self.make_queue()
        with self.assertRaises(KeyError):
            queue.get("missing")

    def test_tasks_lists_in_enqueue_order(self) -> None:
        queue = self.make_queue()
        ids = [queue.enqueue(_echo_plan(f"t{i}")).task_id for i in range(3)]
        self.assertEqual([t.task_id for t in queue.tasks()], ids)

    def test_fail_records_reason(self) -> None:
        queue = self.make_queue()
        queue.enqueue(_echo_plan())
        claimed = queue.claim("w1")
        assert claimed is not None
        failed = queue.fail(claimed.task_id, "w1", "boom")
        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(failed.failure_reason, "boom")

    def test_retry_requeues_and_keeps_attempts(self) -> None:
        queue = self.make_queue()
        queue.enqueue(_echo_plan(), retry_policy=RetryPolicy(max_attempts=3))
        claimed = queue.claim("w1")
        assert claimed is not None
        retried = queue.retry(claimed.task_id, "w1", "transient")
        self.assertEqual(retried.status, TaskStatus.QUEUED)
        self.assertEqual(retried.attempts, 1)
        self.assertIsNone(retried.locked_by)

    def test_cancel_releases_lock(self) -> None:
        queue = self.make_queue()
        task = queue.enqueue(_echo_plan())
        cancelled = queue.cancel(task.task_id, reason="not needed")
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        self.assertIsNone(queue.claim("w1"))

    def test_heartbeat_requires_lock_owner(self) -> None:
        queue = self.make_queue()
        queue.enqueue(_echo_plan())
        claimed = queue.claim("w1")
        assert claimed is not None
        with self.assertRaises(PermissionError):
            queue.heartbeat(claimed.task_id, "w2")

    def test_complete_requires_lock_owner(self) -> None:
        queue = self.make_queue()
        queue.enqueue(_echo_plan())
        claimed = queue.claim("w1")
        assert claimed is not None
        with self.assertRaises(PermissionError):
            queue.complete(claimed.task_id, "w2")

    def test_complete_unknown_task_raises_key_error(self) -> None:
        queue = self.make_queue()
        with self.assertRaises(KeyError):
            queue.complete("missing", "w1")


class PostgresTaskQueueIdempotencyTests(_QueueTestBase):
    def test_enqueue_dedupe_returns_existing_task(self) -> None:
        queue = self.make_queue()
        first = queue.enqueue(_echo_plan(), idempotency_key="once")
        second = queue.enqueue(_echo_plan(), idempotency_key="once")
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(len(queue.tasks()), 1)

    def test_enqueue_dedupe_survives_reopen(self) -> None:
        queue = self.make_queue()
        first = queue.enqueue(_echo_plan(), idempotency_key="once")
        queue2 = self.reopen(queue)
        second = queue2.enqueue(_echo_plan(), idempotency_key="once")
        self.assertEqual(first.task_id, second.task_id)

    def test_concurrent_enqueue_same_key_creates_one_task(self) -> None:
        queue = self.make_queue()
        results: list[str] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                results.append(queue.enqueue(_echo_plan(), idempotency_key="race").task_id)
            except Exception as exc:  # noqa: BLE001 - collected for assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(queue.tasks()), 1)

    def test_stale_worker_cannot_complete_after_redelivery(self) -> None:
        """Redelivery must not allow a second completion side effect."""
        queue = self.make_queue(lease_seconds=5.0)
        queue.enqueue(_echo_plan(), retry_policy=RetryPolicy(max_attempts=3))
        stale = queue.claim("w1", now=100.0)
        assert stale is not None
        # Lease expires; the task is reclaimed and completed by another worker.
        queue.reap_expired_leases(now=200.0)
        fresh = queue.claim("w2", now=201.0)
        assert fresh is not None
        queue.complete(fresh.task_id, "w2", now=202.0)
        # The stale worker's completion is refused: no second side effect.
        with self.assertRaises(PermissionError):
            queue.complete(stale.task_id, "w1", now=203.0)
        self.assertEqual(queue.get(stale.task_id).status, TaskStatus.SUCCEEDED)


class PostgresTaskQueueConcurrencyTests(_QueueTestBase):
    def test_racing_workers_each_task_claimed_exactly_once(self) -> None:
        queue = self.make_queue()
        task_ids = [queue.enqueue(_echo_plan(f"t{i}")).task_id for i in range(12)]
        claims: list[str] = []
        claims_lock = threading.Lock()

        def worker(worker_id: str) -> None:
            while True:
                task = queue.claim(worker_id)
                if task is None:
                    return
                with claims_lock:
                    claims.append(task.task_id)
                queue.complete(task.task_id, worker_id)

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(Counter(claims), Counter(task_ids))
        for task_id in task_ids:
            self.assertEqual(queue.get(task_id).status, TaskStatus.SUCCEEDED)

    def test_second_claim_does_not_return_running_task(self) -> None:
        queue = self.make_queue()
        queue.enqueue(_echo_plan())
        first = queue.claim("w1")
        self.assertIsNotNone(first)
        self.assertIsNone(queue.claim("w2"))


class PostgresTaskQueueLeaseTests(_QueueTestBase):
    def test_expired_lease_is_requeued_when_attempts_remain(self) -> None:
        queue = self.make_queue(lease_seconds=10.0)
        queue.enqueue(_echo_plan(), retry_policy=RetryPolicy(max_attempts=2))
        claimed = queue.claim("w1", now=100.0)
        assert claimed is not None
        reaped = queue.reap_expired_leases(now=200.0)
        self.assertEqual([t.task_id for t in reaped], [claimed.task_id])
        loaded = queue.get(claimed.task_id)
        self.assertEqual(loaded.status, TaskStatus.QUEUED)
        self.assertIsNone(loaded.locked_by)
        self.assertEqual(loaded.attempts, 1)

    def test_expired_lease_times_out_when_attempts_exhausted(self) -> None:
        queue = self.make_queue(lease_seconds=10.0)
        queue.enqueue(_echo_plan(), retry_policy=RetryPolicy(max_attempts=1))
        claimed = queue.claim("w1", now=100.0)
        assert claimed is not None
        reaped = queue.reap_expired_leases(now=200.0)
        self.assertEqual(len(reaped), 1)
        loaded = queue.get(claimed.task_id)
        self.assertEqual(loaded.status, TaskStatus.TIMED_OUT)
        self.assertIsNone(loaded.locked_by)
        self.assertIsNotNone(loaded.failure_reason)

    def test_heartbeat_renews_lease(self) -> None:
        queue = self.make_queue(lease_seconds=10.0)
        queue.enqueue(_echo_plan())
        claimed = queue.claim("w1", now=100.0)
        assert claimed is not None
        queue.heartbeat(claimed.task_id, "w1", now=108.0)
        # Lease was renewed at t=108, so nothing is expired at t=112.
        self.assertEqual(queue.reap_expired_leases(now=112.0), [])
        # Without further heartbeats the lease expires.
        self.assertEqual(len(queue.reap_expired_leases(now=200.0)), 1)

    def test_reclaimed_task_can_be_claimed_by_new_worker(self) -> None:
        queue = self.make_queue(lease_seconds=5.0)
        queue.enqueue(_echo_plan(), retry_policy=RetryPolicy(max_attempts=3))
        queue.claim("w1", now=100.0)
        queue.reap_expired_leases(now=200.0)
        fresh = queue.claim("w2", now=201.0)
        assert fresh is not None
        self.assertEqual(fresh.locked_by, "w2")
        self.assertEqual(fresh.attempts, 2)

    def test_active_lease_is_not_reaped(self) -> None:
        queue = self.make_queue(lease_seconds=60.0)
        queue.enqueue(_echo_plan())
        queue.claim("w1", now=100.0)
        self.assertEqual(queue.reap_expired_leases(now=110.0), [])


class PostgresTaskQueueRunnerTests(_QueueTestBase):
    """The queue is duck-type compatible with TaskRunner."""

    class _Kernel:
        def __init__(self, status: GoalStatus) -> None:
            self._status = status

        def run(self, plan: TransactionPlan) -> None:
            object.__setattr__(plan.goal, "status", self._status)  # Goal is frozen

    def test_runner_completes_successful_goal(self) -> None:
        queue = self.make_queue()
        queue.enqueue(_echo_plan())
        runner = TaskRunner(queue, self._Kernel(GoalStatus.SUCCEEDED), worker_id="w1")
        task = runner.run_next(now=100.0)
        assert task is not None
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)

    def test_runner_retries_then_fails_per_retry_policy(self) -> None:
        queue = self.make_queue()
        queue.enqueue(_echo_plan(), retry_policy=RetryPolicy(max_attempts=2))
        runner = TaskRunner(queue, self._Kernel(GoalStatus.FAILED), worker_id="w1")
        first = runner.run_next(now=100.0)
        assert first is not None
        self.assertEqual(first.status, TaskStatus.QUEUED)  # retried
        second = runner.run_next(now=101.0)
        assert second is not None
        self.assertEqual(second.status, TaskStatus.FAILED)  # attempts exhausted
        self.assertEqual(queue.get(first.task_id).attempts, 2)


class PostgresTaskQueueGuardTests(_QueueTestBase):
    def test_missing_psycopg_raises_runtime_store_error(self) -> None:
        real_import = __import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "psycopg":
                raise ImportError("no psycopg")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=fake_import),
            self.assertRaises(RuntimeStoreError),
        ):
            PostgresTaskQueue(dsn="postgresql://localhost/none")

    def test_close_is_idempotent(self) -> None:
        queue = self.make_queue()
        queue.close()
        queue.close()

    def test_write_error_wrapped_as_runtime_store_error(self) -> None:
        queue = self.make_queue()
        queue.close()
        with self.assertRaises(RuntimeStoreError):
            queue.enqueue(_echo_plan())

    def test_pause_and_resume(self) -> None:
        queue = self.make_queue()
        queue.enqueue(_echo_plan())
        claimed = queue.claim("w1")
        assert claimed is not None
        paused = queue.pause(claimed.task_id, "w1")
        self.assertEqual(paused.status, TaskStatus.PAUSED)
        resumed = queue.resume(claimed.task_id)
        self.assertEqual(resumed.status, TaskStatus.QUEUED)

    def test_resume_non_paused_task_raises(self) -> None:
        queue = self.make_queue()
        task = queue.enqueue(_echo_plan())
        with self.assertRaises(ValueError):
            queue.resume(task.task_id)

    def test_timeout_policy_round_trips(self) -> None:
        queue = self.make_queue()
        task = queue.enqueue(_echo_plan(), timeout_policy=TimeoutPolicy(heartbeat_timeout_seconds=7.0))
        loaded = queue.get(task.task_id)
        self.assertEqual(loaded.timeout_policy.heartbeat_timeout_seconds, 7.0)


@unittest.skipUnless(os.environ.get("LEOS_TEST_POSTGRES_DSN"), "requires a live PostgreSQL server")
class PostgresTaskQueueRealServerTests(unittest.TestCase):
    def test_enqueue_claim_complete_round_trip(self) -> None:
        queue = PostgresTaskQueue(os.environ["LEOS_TEST_POSTGRES_DSN"])
        self.addCleanup(queue.close)
        task = queue.enqueue(_echo_plan())
        claimed = queue.claim("w1")
        assert claimed is not None
        self.assertEqual(claimed.task_id, task.task_id)
        done = queue.complete(claimed.task_id, "w1")
        self.assertEqual(done.status, TaskStatus.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
