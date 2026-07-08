# Task Queue — Leos Agent Runtime

## Modes

- **In-memory**: `TaskQueue()` — tasks live in process memory. Lost on restart.
- **SQLite**: `TaskQueue(path=Path("tasks.db"))` — tasks persist across restarts.
- **PostgreSQL**: `PostgresTaskQueue(dsn)` — multi-worker safe; the database is
  authoritative and every state change is a single conditional SQL statement.
  Requires the optional `psycopg` dependency (`pip install "leos-agent[postgres]"`).

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

## Multi-worker mode (PostgresTaskQueue)

`PostgresTaskQueue` (`task_queue_backends.py`) is duck-type compatible with
`TaskQueue` — `TaskRunner` works against either — but is safe for concurrent
workers across processes and hosts:

- **Atomic claim**: `claim` is one `UPDATE … WHERE task_id = (SELECT … FOR
  UPDATE SKIP LOCKED LIMIT 1) RETURNING …` statement. Racing workers each
  receive a distinct task; a task can never be claimed twice.
- **Leases**: a claimed task holds a lease (`lease_seconds`, default 60).
  `heartbeat` renews it. `reap_expired_leases()` reclaims RUNNING tasks whose
  lease expired: re-queued if `attempts < max_attempts`, otherwise TIMED_OUT.
  Run it from a supervisor loop in place of the in-process `Watchdog`.
- **Idempotent enqueue**: a unique index on `idempotency_key` makes dedupe
  race-safe (`ON CONFLICT DO NOTHING`); concurrent enqueues of the same key
  produce exactly one task.
- **Idempotent completion**: `complete`/`fail`/`retry` are conditional on
  `status = RUNNING AND locked_by = worker`. A stale worker whose lease was
  reclaimed cannot finish the task a second time (`PermissionError`) — 
  redelivery produces no second side effect.
- Plans pass the shared secret guard (`assert_no_secrets`) before write;
  backend errors are wrapped in `RuntimeStoreError`.

Environment: real-server tests are gated on `LEOS_TEST_POSTGRES_DSN`.

## Persistence Limitations

- SQLite mode: local durability only — not distributed consensus; no
  concurrent writer support beyond SQLite's built-in locking; no WAL
  checkpoint management beyond `PRAGMA journal_mode=WAL`.
- Postgres mode: exactly-once *claim*, at-least-once *execution* — a worker
  that dies after side effects but before `complete` leads to redelivery, so
  steps still rely on step-level `idempotency_key` and the kernel's
  dry-run/verify/rollback cycle for effect-level safety.
- Plan serialization handles full ActionStep/Goal/StateCondition roundtrip.
