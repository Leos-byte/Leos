# Storage

## RuntimeStore backends

All backends implement the same 8-method `RuntimeStore` protocol
(`save_goal`/`load_goal`, `save_plan`/`load_plan`, `append_runtime_event`/
`list_runtime_events`, `save_checkpoint`/`load_checkpoint`) with identical
semantics: upsert latest-wins for goals/plans/checkpoints, append-only ordered
runtime events, and secret rejection before any write.

- `InMemoryRuntimeStore` — tests and demos.
- `JsonlRuntimeStore` — development persistence; not for strong concurrency.
- `SQLiteRuntimeStore` — durable local persistence with restart recovery.
- `PostgresRuntimeStore` (`postgres_store.py`) — production persistence over
  PostgreSQL via the optional `psycopg` (v3) driver. A connection may be
  injected (for pooling or tests); otherwise it is created lazily from a DSN.
  Missing `psycopg` surfaces a `RuntimeStoreError`.

The shared invariants are verified by a reusable contract mixin
(`tests/store_contract.py`) applied to every backend. `PostgresRuntimeStore`'s
SQL is exercised in CI against a dialect-translating SQLite connection; a real
PostgreSQL round trip runs when `LEOS_TEST_POSTGRES_DSN` is set.

## Task queue

The runtime provides an in-memory `TaskQueue` with optional SQLite
persistence for task records and idempotency keys, plus a Postgres-backed
`PostgresTaskQueue` (`task_queue_backends.py`) for multi-worker deployments.

Implemented:

- task enqueue/reload
- idempotency key dedupe across queue instances (race-safe in Postgres mode
  via a unique index and `ON CONFLICT DO NOTHING`)
- claim, heartbeat, completion, retry, pause/resume, and watchdog timeout state
- Postgres mode: DB-atomic claim (`FOR UPDATE SKIP LOCKED`), lease TTL with
  heartbeat renewal, `reap_expired_leases`, and conditional completion that
  refuses stale workers after redelivery (see `docs/TASK_QUEUE.md`)

Not complete:

- SQLite-backed `AuditLog`
- SQLite-backed `MemoryStore`

Use the in-memory backend for short-lived tests, the SQLite task queue for
single-process local development that needs task state to survive restart, and
`PostgresTaskQueue` when multiple workers consume the same queue.
