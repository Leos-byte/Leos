# Changelog

All notable changes to Leos Agent should be recorded here.

This project follows semantic versioning once public releases begin.

## Unreleased

- Added a safety-first runtime kernel with policy gates, causal verification,
  rollback, replay, memory, task queue, sandbox, and CLI inspection utilities.
- Added red-team tests, benchmark cases, coverage threshold, security checks,
  mutation smoke checks, and fuzz smoke checks.
- Added production sandbox isolation backends (`sandbox_backends.py`):
  `GvisorSandboxRunner` (gVisor `runsc`), `RootlessPodmanSandboxRunner`
  (user-namespace remap + optional seccomp), and `FirecrackerSandboxRunner`
  (microVM target, fail-closed), plus `resolve_sandbox_runner` which never
  downgrades a container/microVM policy to the workspace runner. No kernel
  gating semantics changed.
- Added production persistence and managed-credential backends:
  `PostgresRuntimeStore` (`postgres_store.py`, optional `psycopg`) implementing
  the full `RuntimeStore` protocol, and `EnvFileCredentialVault`,
  `KeyringCredentialVault` (optional `keyring`), `VaultCredentialVault`
  (HashiCorp Vault via optional `hvac`) in `credential_backends.py`. Added a
  reusable `RuntimeStore` contract mixin (`tests/store_contract.py`). Optional
  extras `postgres`, `keyring`, `vault`; core runtime dependency remains only
  `jsonschema`. No kernel gating semantics changed.
- Added `PostgresTaskQueue` (`task_queue_backends.py`, optional `psycopg`) for
  safe multi-worker task consumption: DB-atomic claim via
  `FOR UPDATE SKIP LOCKED`, lease TTL with heartbeat renewal and
  `reap_expired_leases`, race-safe idempotency dedupe at enqueue, and
  conditional completion so a stale worker cannot produce a second side effect
  after redelivery. Duck-type compatible with `TaskQueue`/`TaskRunner`. The
  SQLite `TaskQueue` remains the single-process default. No kernel gating
  semantics changed.
- Added a thin HTTP service layer (`leos_agent.server`, optional `fastapi`
  via the `server` extra): `create_app` exposes plan drafting/validation,
  approval packet and signed-decision emission, `/apply` delegating verbatim
  to `apply_operator_plan`, and audit/trace read endpoints. Boundary API-key
  auth only — every write still requires a signed, unexpired, consume-once
  approval decision on the existing gate path. Fails closed without an API
  key; secrets come from the server environment, never request bodies. No
  kernel gating semantics changed.

## 0.1.0

- Initial package metadata and CLI entry point.
