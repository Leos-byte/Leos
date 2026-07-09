#!/usr/bin/env python3
"""Multi-process task-queue concurrency smoke against a real PostgreSQL server.

Spawns real worker processes consuming a shared ``PostgresTaskQueue`` and
proves the exactly-once claims end to end: no task is claimed by two workers,
every task completes exactly once, a killed worker's expired lease is reaped
and its task is finished by another worker, and duplicate idempotency keys do
not produce a second row. Evidence follows the same model as the GitHub
real-write smoke: gitignored JSON bound to the current commit, uploaded as a
CI artifact, validated by ``check_production_readiness.py``. The DSN is read
from ``LEOS_TEST_POSTGRES_DSN`` (the same variable that gates the unit tests)
and never enters the evidence.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import subprocess  # nosec B404 - fixed argv for git metadata only
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from leos_agent.enums import TaskStatus  # noqa: E402
from leos_agent.goals import Goal  # noqa: E402
from leos_agent.plans import ActionStep, TransactionPlan  # noqa: E402
from leos_agent.sanitization import assert_no_secrets  # noqa: E402
from leos_agent.task_queue import RetryPolicy  # noqa: E402
from leos_agent.task_queue_backends import PostgresTaskQueue  # noqa: E402

EVIDENCE_TYPE = "postgres_task_queue_concurrency_smoke"
DEFAULT_EVIDENCE_OUT = "docs/proofs/queue_smoke_latest.json"
DEFAULT_WORKERS = 4
DEFAULT_TASKS = 200

_FORBIDDEN_EVIDENCE_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9_]+", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9_]+", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"bearer\s", re.IGNORECASE),
    re.compile(r"hmac-sha256:[0-9a-fA-F]{32,}", re.IGNORECASE),
    re.compile(r"://[^\s\"]+:[^\s\"]+@", re.IGNORECASE),
)


class QueueSmokeError(RuntimeError):
    """A queue smoke invariant was violated."""


def initial_checks() -> dict[str, object]:
    return {
        "postgres_available": False,
        "all_tasks_completed": False,
        "exactly_once_execution": False,
        "no_double_claim": False,
        "killed_worker_lease_reaped": False,
        "reaped_task_completed_by_other_worker": False,
        "idempotency_dedupe_enforced": False,
    }


def build_evidence(*, worker_count: int, task_count: int) -> dict[str, Any]:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    return {
        "schema_version": 1,
        "evidence_type": EVIDENCE_TYPE,
        "status": "failed",
        "worker_count": worker_count,
        "task_count": task_count,
        "postgres_server_version": None,
        "leos_commit_sha": os.environ.get("GITHUB_SHA") or _git_head(),
        "workflow_run_id": run_id,
        "run_id": run_id,
        "workflow_trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "failure_type": None,
        "failure_summary": None,
        "generated_at": _utc_now(),
        "checks": initial_checks(),
    }


def _smoke_plan(index: int) -> TransactionPlan:
    goal = Goal(description=f"queue smoke {index}", success_criteria=["ok"], stop_conditions=["done"])
    return TransactionPlan(goal=goal, steps=[ActionStep("echo", {"message": f"smoke-{index}"}, "smoke")])


def _worker(dsn: str, worker_id: str, results: Any) -> None:
    """Claim-and-complete loop for one worker process."""
    queue = PostgresTaskQueue(dsn)
    claimed: list[str] = []
    idle_polls = 0
    try:
        while idle_polls < 20:
            task = queue.claim(worker_id)
            if task is None:
                idle_polls += 1
                time.sleep(0.05)
                continue
            idle_polls = 0
            claimed.append(task.task_id)
            queue.complete(task.task_id, worker_id)
    finally:
        queue.close()
        results.put((worker_id, claimed))


def _claim_and_die(dsn: str, worker_id: str, lease_seconds: float) -> None:
    """Claim one task then die without completing, heartbeating, or closing."""
    queue = PostgresTaskQueue(dsn, lease_seconds=lease_seconds)
    queue.claim(worker_id)
    os._exit(1)


def run_smoke(
    dsn: str | None = None,
    *,
    worker_count: int | None = None,
    task_count: int | None = None,
    lease_seconds: float = 2.0,
) -> dict[str, Any]:
    worker_count = worker_count or int(os.environ.get("LEOS_QUEUE_SMOKE_WORKERS", DEFAULT_WORKERS))
    task_count = task_count or int(os.environ.get("LEOS_QUEUE_SMOKE_TASKS", DEFAULT_TASKS))
    evidence = build_evidence(worker_count=worker_count, task_count=task_count)
    checks = evidence["checks"]
    dsn = dsn or os.environ.get("LEOS_TEST_POSTGRES_DSN")
    if not dsn:
        evidence["failure_type"] = "postgres_unavailable"
        evidence["failure_summary"] = "LEOS_TEST_POSTGRES_DSN is not set"
        return evidence
    try:
        try:
            queue = PostgresTaskQueue(dsn)
        except Exception as exc:  # noqa: BLE001 - report any backend failure as unavailability
            evidence["failure_type"] = "postgres_unavailable"
            evidence["failure_summary"] = type(exc).__name__
            return evidence
        checks["postgres_available"] = True
        evidence["postgres_server_version"] = _server_version(queue)
        _reset_table(queue)

        _stage_concurrent_consumption(queue, dsn, checks, worker_count=worker_count, task_count=task_count)
        _stage_killed_worker(queue, dsn, checks, lease_seconds=lease_seconds)
        _stage_idempotency(queue, checks)
        queue.close()

        failed = [name for name, value in checks.items() if value is not True]
        if failed:
            raise QueueSmokeError(f"checks did not pass: {', '.join(failed)}")
        evidence["status"] = "passed"
    except QueueSmokeError as exc:
        evidence["failure_type"] = evidence["failure_type"] or "concurrency_check_failed"
        evidence["failure_summary"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - evidence must always be writable
        # Only the exception type: backend error text can echo connection details.
        evidence["failure_type"] = "unexpected_error"
        evidence["failure_summary"] = type(exc).__name__
    return evidence


def _stage_concurrent_consumption(
    queue: PostgresTaskQueue,
    dsn: str,
    checks: dict[str, object],
    *,
    worker_count: int,
    task_count: int,
) -> None:
    enqueued = [queue.enqueue(_smoke_plan(index)).task_id for index in range(task_count)]
    context = multiprocessing.get_context("spawn")
    results: Any = context.Queue()
    workers = [context.Process(target=_worker, args=(dsn, f"worker-{index}", results)) for index in range(worker_count)]
    for process in workers:
        process.start()
    claims: Counter[str] = Counter()
    for _ in workers:
        _worker_id, claimed = results.get(timeout=120)
        claims.update(claimed)
    for process in workers:
        process.join(timeout=60)

    statuses = {task.task_id: task for task in queue.tasks() if task.task_id in set(enqueued)}
    checks["all_tasks_completed"] = all(statuses[task_id].status is TaskStatus.SUCCEEDED for task_id in enqueued)
    checks["exactly_once_execution"] = all(statuses[task_id].attempts == 1 for task_id in enqueued)
    checks["no_double_claim"] = set(claims) == set(enqueued) and all(count == 1 for count in claims.values())


def _stage_killed_worker(
    queue: PostgresTaskQueue,
    dsn: str,
    checks: dict[str, object],
    *,
    lease_seconds: float,
) -> None:
    task = queue.enqueue(_smoke_plan(-1), retry_policy=RetryPolicy(max_attempts=3))
    context = multiprocessing.get_context("spawn")
    victim = context.Process(target=_claim_and_die, args=(dsn, "victim", lease_seconds))
    victim.start()
    victim.join(timeout=60)
    if queue.get(task.task_id).status is not TaskStatus.RUNNING:
        raise QueueSmokeError("victim worker did not leave the task RUNNING")

    time.sleep(lease_seconds + 1.0)
    reaper = PostgresTaskQueue(dsn, lease_seconds=lease_seconds)
    try:
        reaped = reaper.reap_expired_leases()
    finally:
        reaper.close()
    checks["killed_worker_lease_reaped"] = any(item.task_id == task.task_id for item in reaped)

    rescued = queue.claim("rescuer")
    if rescued is None or rescued.task_id != task.task_id:
        raise QueueSmokeError("reaped task was not claimable by another worker")
    queue.complete(rescued.task_id, "rescuer")
    final = queue.get(task.task_id)
    checks["reaped_task_completed_by_other_worker"] = final.status is TaskStatus.SUCCEEDED and final.attempts == 2


def _stage_idempotency(queue: PostgresTaskQueue, checks: dict[str, object]) -> None:
    first = queue.enqueue(_smoke_plan(-2), idempotency_key="queue-smoke-idem")
    second = queue.enqueue(_smoke_plan(-2), idempotency_key="queue-smoke-idem")
    duplicates = [task for task in queue.tasks() if task.idempotency_key == "queue-smoke-idem"]
    checks["idempotency_dedupe_enforced"] = first.task_id == second.task_id and len(duplicates) == 1


def _reset_table(queue: PostgresTaskQueue) -> None:
    with queue._conn.cursor() as cur:  # noqa: SLF001 - smoke-only table reset
        cur.execute("DELETE FROM leos_tasks")
    queue._conn.commit()  # noqa: SLF001


def _server_version(queue: PostgresTaskQueue) -> str:
    with queue._conn.cursor() as cur:  # noqa: SLF001 - metadata read only
        cur.execute("SELECT current_setting('server_version')")
        row = cur.fetchone()
    queue._conn.commit()  # noqa: SLF001
    if isinstance(row, dict):
        return str(next(iter(row.values())))
    return str(row[0]) if row else "unknown"


def write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    evidence["generated_at"] = _utc_now()
    assert_no_secrets(evidence)
    serialized = json.dumps(evidence, indent=2, sort_keys=True)
    if any(pattern.search(serialized) for pattern in _FORBIDDEN_EVIDENCE_PATTERNS):
        raise QueueSmokeError("sanitized queue smoke evidence contained a forbidden marker")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized + "\n", encoding="utf-8")
    temporary.replace(path)


def _git_head() -> str:
    result = subprocess.run(  # nosec B603 - fixed argv
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    evidence_out = Path(os.environ.get("LEOS_QUEUE_SMOKE_EVIDENCE_OUT", DEFAULT_EVIDENCE_OUT))
    evidence = run_smoke()
    write_evidence(evidence_out, evidence)
    print(json.dumps({k: evidence[k] for k in ("status", "failure_summary", "checks")}, indent=2))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
