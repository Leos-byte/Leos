# Task Queue — Leos Agent Runtime

## Modes

- **In-memory**: `TaskQueue()` — tasks live in process memory. Lost on restart.
- **SQLite**: `TaskQueue(path=Path("tasks.db"))` — tasks persist across restarts.

## Schema

- `tasks` table: task_id, plan_json, status, retry_policy_json,
  timeout_policy_json, idempotency_key, attempts, locked_by, enqueued_at,
  started_at, last_heartbeat_at, finished_at, failure_reason.
- `idempotency` table: idempotency_key, task_id.

## Task Lifecycle

```
QUEUED → RUNNING → SUCCEEDED
              ↓
        PAUSED → QUEUED → RUNNING
              ↓
        FAILED → QUEUED (retry)
              ↓
        CANCELLED / TIMED_OUT
```

## Locks

- `locked_by` worker ID.
- Non-lock-holding workers cannot heartbeat/complete/fail/pause.
- `PermissionError` raised on lock violation.

## Heartbeat

- `heartbeat(task_id, worker_id)` records `last_heartbeat_at`.
- Requires lock ownership.

## Watchdog

- `Watchdog.check()` scans RUNNING tasks.
- Heartbeat timeout: marks as TIMED_OUT if `last_heartbeat_at` is too old.
- Runtime timeout: marks as TIMED_OUT if `started_at` exceeds limit.
- Persists TIMED_OUT to SQLite when in persistent mode.

## Retry

- `RetryPolicy.max_attempts` controls retry count.
- `TaskRunner._handle_failure` retries if `attempts < max_attempts`.
- Retried tasks return to QUEUED.

## Idempotency

- Task-level: same `idempotency_key` returns existing task.
- Step-level: `idempotency_key` on `ActionStep` prevents re-execution.
- Both persist across reloads in SQLite mode.

## Persistence Limitations

- Local durability only — not distributed consensus.
- No WAL checkpoint management beyond `PRAGMA journal_mode=WAL`.
- No concurrent writer support beyond SQLite's built-in locking.
- Plan serialization handles full ActionStep/Goal/StateCondition roundtrip.
