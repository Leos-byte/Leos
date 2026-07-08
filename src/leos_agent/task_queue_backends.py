"""PostgreSQL-backed task queue for safe multi-worker consumption.

``TaskQueue`` (``task_queue.py``) is single-process by design: it mutates
in-memory state and mirrors it to SQLite, so its ``claim`` is not atomic across
processes. ``PostgresTaskQueue`` makes the database authoritative: every state
change is a single conditional SQL statement, claims use
``FOR UPDATE SKIP LOCKED`` so racing workers each receive a distinct task, and
running tasks hold a lease that heartbeats renew and ``reap_expired_leases``
reclaims. It is duck-type compatible with ``TaskQueue`` (same method surface),
so ``TaskRunner`` and ``Watchdog``-style supervisors work unchanged.

The optional ``psycopg`` (v3) driver is imported lazily; a connection may also
be injected (tests, callers managing their own pooling). Backend errors are
wrapped in ``RuntimeStoreError`` following the ``PostgresRuntimeStore``
convention.
"""

from __future__ import annotations

import importlib
import json
import time
import uuid
from contextlib import suppress
from typing import Any

from .audit import AuditLog
from .enums import TaskStatus
from .plans import TransactionPlan
from .postgres_store import _Connection
from .runtime_store import RuntimeStoreError
from .sanitization import SanitizationError, assert_no_secrets
from .serialization import (
    deserialize_plan,
    deserialize_retry_policy,
    deserialize_timeout_policy,
    serialize_plan,
    serialize_retry_policy,
    serialize_timeout_policy,
)
from .task_queue import RetryPolicy, RuntimeTask, TimeoutPolicy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leos_tasks (
  task_id TEXT PRIMARY KEY,
  plan_json TEXT NOT NULL,
  status TEXT NOT NULL,
  retry_policy_json TEXT NOT NULL,
  timeout_policy_json TEXT NOT NULL,
  max_attempts INTEGER NOT NULL,
  idempotency_key TEXT UNIQUE,
  attempts INTEGER NOT NULL DEFAULT 0,
  locked_by TEXT,
  enqueued_at DOUBLE PRECISION NOT NULL,
  started_at DOUBLE PRECISION,
  last_heartbeat_at DOUBLE PRECISION,
  lease_expires_at DOUBLE PRECISION,
  finished_at DOUBLE PRECISION,
  failure_reason TEXT
);
"""


class PostgresTaskQueue:
    """Multi-worker task queue with DB-atomic claim and lease-based recovery."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        connection: _Connection | None = None,
        audit_log: AuditLog | None = None,
        lease_seconds: float = 60.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.audit_log = audit_log or AuditLog()
        self.lease_seconds = lease_seconds
        self._closed = False
        self._conn = connection if connection is not None else self._connect(dsn)
        try:
            self._init_schema()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres task queue schema init failed: {type(exc).__name__}") from exc

    @staticmethod
    def _connect(dsn: str | None) -> _Connection:
        try:
            psycopg = importlib.import_module("psycopg")
        except ImportError as exc:
            raise RuntimeStoreError("PostgresTaskQueue requires the optional 'psycopg' package") from exc
        try:  # pragma: no cover - needs a live PostgreSQL server
            rows = importlib.import_module("psycopg.rows")
            connection: _Connection = psycopg.connect(dsn, row_factory=rows.dict_row)
            return connection
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - needs a live server
            raise RuntimeStoreError(f"postgres task queue unavailable: {type(exc).__name__}") from exc

    # -- queue operations -------------------------------------------------------

    def enqueue(
        self,
        plan: TransactionPlan,
        *,
        idempotency_key: str | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout_policy: TimeoutPolicy | None = None,
    ) -> RuntimeTask:
        retry_policy = retry_policy or RetryPolicy()
        timeout_policy = timeout_policy or TimeoutPolicy()
        plan_json = serialize_plan(plan)
        _assert_queue_safe(json.loads(plan_json))
        task_id = str(uuid.uuid4())
        now = time.time()
        params = (
            task_id,
            plan_json,
            TaskStatus.QUEUED.value,
            serialize_retry_policy(retry_policy),
            serialize_timeout_policy(timeout_policy),
            retry_policy.max_attempts,
            idempotency_key,
            0,
            None,
            now,
        )
        if idempotency_key is None:
            row = self._mutate(
                """
                INSERT INTO leos_tasks (task_id, plan_json, status, retry_policy_json,
                  timeout_policy_json, max_attempts, idempotency_key, attempts, locked_by, enqueued_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                params,
            )
        else:
            # Atomic dedupe: the unique index on idempotency_key makes the
            # insert race-safe; a conflicting insert returns no row.
            row = self._mutate(
                """
                INSERT INTO leos_tasks (task_id, plan_json, status, retry_policy_json,
                  timeout_policy_json, max_attempts, idempotency_key, attempts, locked_by, enqueued_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING *
                """,
                params,
            )
            if row is None:
                existing = self._fetchone(
                    "SELECT * FROM leos_tasks WHERE idempotency_key = %s",
                    (idempotency_key,),
                )
                if existing is None:  # pragma: no cover - defensive; row must exist after conflict
                    raise RuntimeStoreError("idempotency conflict without an existing task row")
                task = _task_from_row(existing)
                self.audit_log.record(
                    "task.deduplicated",
                    "Task idempotency key already exists",
                    task_id=task.task_id,
                    plan_id=task.plan.plan_id,
                    idempotency_key=idempotency_key,
                    status=task.status.value,
                )
                return task
        if row is None:  # pragma: no cover - defensive; plain insert always returns
            raise RuntimeStoreError("task insert returned no row")
        task = _task_from_row(row)
        self.audit_log.record(
            "task.enqueued",
            "Task enqueued",
            task_id=task.task_id,
            plan_id=plan.plan_id,
            idempotency_key=idempotency_key,
        )
        return task

    def claim(self, worker_id: str, *, now: float | None = None) -> RuntimeTask | None:
        timestamp = time.time() if now is None else now
        row = self._mutate(
            """
            UPDATE leos_tasks
            SET status = %s, locked_by = %s, attempts = attempts + 1,
                started_at = %s, last_heartbeat_at = %s, lease_expires_at = %s
            WHERE task_id = (
              SELECT task_id FROM leos_tasks
              WHERE status = %s
              ORDER BY enqueued_at, task_id
              LIMIT 1
              FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (
                TaskStatus.RUNNING.value,
                worker_id,
                timestamp,
                timestamp,
                timestamp + self.lease_seconds,
                TaskStatus.QUEUED.value,
            ),
        )
        if row is None:
            return None
        task = _task_from_row(row)
        self.audit_log.record(
            "task.claimed",
            "Task claimed by worker",
            task_id=task.task_id,
            plan_id=task.plan.plan_id,
            worker_id=worker_id,
            attempts=task.attempts,
        )
        return task

    def heartbeat(self, task_id: str, worker_id: str, *, now: float | None = None) -> RuntimeTask:
        timestamp = time.time() if now is None else now
        task = self._locked_mutate(
            """
            UPDATE leos_tasks SET last_heartbeat_at = %s, lease_expires_at = %s
            WHERE task_id = %s AND status = %s AND locked_by = %s
            RETURNING *
            """,
            (timestamp, timestamp + self.lease_seconds, task_id, TaskStatus.RUNNING.value, worker_id),
            task_id,
        )
        self.audit_log.record("task.heartbeat", "Task heartbeat recorded", task_id=task_id, worker_id=worker_id)
        return task

    def complete(self, task_id: str, worker_id: str, *, now: float | None = None) -> RuntimeTask:
        task = self._finish(task_id, worker_id, TaskStatus.SUCCEEDED, None, now=now)
        self.audit_log.record("task.completed", "Task completed", task_id=task_id, worker_id=worker_id)
        return task

    def fail(self, task_id: str, worker_id: str, reason: str, *, now: float | None = None) -> RuntimeTask:
        task = self._finish(task_id, worker_id, TaskStatus.FAILED, reason, now=now)
        self.audit_log.record("task.failed", reason, task_id=task_id, worker_id=worker_id)
        return task

    def retry(self, task_id: str, worker_id: str, reason: str) -> RuntimeTask:
        task = self._locked_mutate(
            """
            UPDATE leos_tasks
            SET status = %s, locked_by = NULL, started_at = NULL,
                last_heartbeat_at = NULL, lease_expires_at = NULL,
                finished_at = NULL, failure_reason = %s
            WHERE task_id = %s AND status = %s AND locked_by = %s
            RETURNING *
            """,
            (TaskStatus.QUEUED.value, reason, task_id, TaskStatus.RUNNING.value, worker_id),
            task_id,
        )
        self.audit_log.record(
            "task.retry_scheduled",
            reason,
            task_id=task_id,
            worker_id=worker_id,
            attempts=task.attempts,
            max_attempts=task.retry_policy.max_attempts,
        )
        return task

    def cancel(self, task_id: str, *, reason: str = "cancelled", now: float | None = None) -> RuntimeTask:
        timestamp = time.time() if now is None else now
        row = self._mutate(
            """
            UPDATE leos_tasks
            SET status = %s, finished_at = %s, failure_reason = %s,
                locked_by = NULL, lease_expires_at = NULL
            WHERE task_id = %s
            RETURNING *
            """,
            (TaskStatus.CANCELLED.value, timestamp, reason, task_id),
        )
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        self.audit_log.record("task.cancelled", reason, task_id=task_id)
        return _task_from_row(row)

    def pause(self, task_id: str, worker_id: str) -> RuntimeTask:
        task = self._locked_mutate(
            """
            UPDATE leos_tasks SET status = %s, locked_by = NULL, lease_expires_at = NULL
            WHERE task_id = %s AND status = %s AND locked_by = %s
            RETURNING *
            """,
            (TaskStatus.PAUSED.value, task_id, TaskStatus.RUNNING.value, worker_id),
            task_id,
        )
        self.audit_log.record("task.paused", "Task paused", task_id=task_id, worker_id=worker_id)
        return task

    def resume(self, task_id: str) -> RuntimeTask:
        row = self._mutate(
            """
            UPDATE leos_tasks SET status = %s
            WHERE task_id = %s AND status = %s
            RETURNING *
            """,
            (TaskStatus.QUEUED.value, task_id, TaskStatus.PAUSED.value),
        )
        if row is None:
            self._require_exists(task_id)
            raise ValueError("Only paused tasks can be resumed")
        self.audit_log.record("task.resumed", "Task resumed", task_id=task_id)
        return _task_from_row(row)

    def reap_expired_leases(self, *, now: float | None = None) -> list[RuntimeTask]:
        """Reclaim RUNNING tasks whose lease expired.

        Tasks with attempts remaining are re-queued for another worker; tasks
        with exhausted attempts are marked TIMED_OUT. Both transitions are
        single conditional statements, so concurrent reapers are safe.
        """
        timestamp = time.time() if now is None else now
        reaped: list[RuntimeTask] = []
        requeued = self._mutate_all(
            """
            UPDATE leos_tasks
            SET status = %s, locked_by = NULL, started_at = NULL,
                last_heartbeat_at = NULL, lease_expires_at = NULL,
                failure_reason = %s
            WHERE status = %s AND lease_expires_at IS NOT NULL AND lease_expires_at < %s
              AND attempts < max_attempts
            RETURNING *
            """,
            (TaskStatus.QUEUED.value, "Task lease expired; requeued", TaskStatus.RUNNING.value, timestamp),
        )
        for row in requeued:
            task = _task_from_row(row)
            reaped.append(task)
            self.audit_log.record(
                "task.lease_reclaimed",
                "Task lease expired; requeued",
                task_id=task.task_id,
                plan_id=task.plan.plan_id,
                attempts=task.attempts,
            )
        timed_out = self._mutate_all(
            """
            UPDATE leos_tasks
            SET status = %s, locked_by = NULL, lease_expires_at = NULL,
                finished_at = %s, failure_reason = %s
            WHERE status = %s AND lease_expires_at IS NOT NULL AND lease_expires_at < %s
              AND attempts >= max_attempts
            RETURNING *
            """,
            (
                TaskStatus.TIMED_OUT.value,
                timestamp,
                "Task lease expired; attempts exhausted",
                TaskStatus.RUNNING.value,
                timestamp,
            ),
        )
        for row in timed_out:
            task = _task_from_row(row)
            reaped.append(task)
            self.audit_log.record(
                "task.timed_out",
                "Task lease expired; attempts exhausted",
                task_id=task.task_id,
                plan_id=task.plan.plan_id,
                attempts=task.attempts,
            )
        return reaped

    def get(self, task_id: str) -> RuntimeTask:
        row = self._fetchone("SELECT * FROM leos_tasks WHERE task_id = %s", (task_id,))
        if row is None:
            raise KeyError(f"Unknown task: {task_id}")
        return _task_from_row(row)

    def tasks(self) -> list[RuntimeTask]:
        rows = self._fetchall("SELECT * FROM leos_tasks ORDER BY enqueued_at, task_id", ())
        return [_task_from_row(row) for row in rows]

    # -- lifecycle ---------------------------------------------------------------

    def close(self) -> None:
        if not self._closed:
            self._conn.close()
            self._closed = True

    def __enter__(self) -> PostgresTaskQueue:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    # -- internals -----------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._conn.cursor() as cur:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    cur.execute(statement)
        self._conn.commit()

    def _finish(
        self, task_id: str, worker_id: str, status: TaskStatus, reason: str | None, *, now: float | None
    ) -> RuntimeTask:
        timestamp = time.time() if now is None else now
        return self._locked_mutate(
            """
            UPDATE leos_tasks
            SET status = %s, finished_at = %s, failure_reason = %s,
                locked_by = NULL, lease_expires_at = NULL
            WHERE task_id = %s AND status = %s AND locked_by = %s
            RETURNING *
            """,
            (status.value, timestamp, reason, task_id, TaskStatus.RUNNING.value, worker_id),
            task_id,
        )

    def _locked_mutate(self, sql: str, params: tuple[Any, ...], task_id: str) -> RuntimeTask:
        """Run a lock-guarded conditional update; distinguish missing vs. not-owned."""
        row = self._mutate(sql, params)
        if row is None:
            self._require_exists(task_id)
            raise PermissionError("Task is not locked by this worker")
        return _task_from_row(row)

    def _require_exists(self, task_id: str) -> None:
        if self._fetchone("SELECT task_id FROM leos_tasks WHERE task_id = %s", (task_id,)) is None:
            raise KeyError(f"Unknown task: {task_id}")

    def _mutate(self, sql: str, params: tuple[Any, ...]) -> Any:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            self._conn.commit()
            return row
        except RuntimeStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres task queue write failed: {type(exc).__name__}") from exc

    def _mutate_all(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                rows = list(cur.fetchall())
            self._conn.commit()
            return rows
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres task queue write failed: {type(exc).__name__}") from exc

    def _fetchone(self, sql: str, params: tuple[Any, ...]) -> Any:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres task queue read failed: {type(exc).__name__}") from exc

    def _fetchall(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        except Exception as exc:  # noqa: BLE001
            raise RuntimeStoreError(f"postgres task queue read failed: {type(exc).__name__}") from exc


def _task_from_row(row: Any) -> RuntimeTask:
    return RuntimeTask(
        plan=deserialize_plan(str(row["plan_json"])),
        task_id=str(row["task_id"]),
        status=TaskStatus(row["status"]),
        retry_policy=deserialize_retry_policy(str(row["retry_policy_json"])),
        timeout_policy=deserialize_timeout_policy(str(row["timeout_policy_json"])),
        idempotency_key=row["idempotency_key"],
        attempts=int(row["attempts"]),
        locked_by=row["locked_by"],
        enqueued_at=float(row["enqueued_at"]),
        started_at=row["started_at"],
        last_heartbeat_at=row["last_heartbeat_at"],
        finished_at=row["finished_at"],
        failure_reason=row["failure_reason"],
    )


def _assert_queue_safe(value: Any) -> None:
    try:
        assert_no_secrets(value)
    except SanitizationError as exc:
        raise RuntimeStoreError(f"PostgresTaskQueue rejected secret-like value: {exc}") from exc
