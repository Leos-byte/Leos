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
persistence for task records and idempotency keys.

Implemented:

- task enqueue/reload
- idempotency key dedupe across queue instances
- claim, heartbeat, completion, retry, pause/resume, and watchdog timeout state

Not complete:

- SQLite-backed `AuditLog`
- SQLite-backed `MemoryStore`
- real concurrent worker stress testing

Use the in-memory backend for short-lived tests and the SQLite task queue for
local development scenarios that need task state to survive process restart.
