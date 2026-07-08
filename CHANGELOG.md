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
- Added observability side-car sinks (`observability.py`): `PrometheusMetrics`
  (dependency-free counters + exposition-format rendering),
  `StructlogAuditSink` (optional `structlog`), `OTelAuditSink` (optional
  `opentelemetry-api`), and `compose_sinks`. Enabled by one additive optional
  hook on `AuditLog` — `on_event`, default `None`, invoked after append with
  sink exceptions suppressed — so audit recording is unchanged and output is
  byte-identical with or without a sink (tested). New optional extra
  `observability`. No kernel gating semantics changed.
- Added usage ergonomics: `leos_agent.recipes` one-call templates for the
  validated GitHub single-file-change path (prepare/approve/apply over the
  existing gate pipeline, no new gating logic); `leos policy init` CLI wizard
  (`policy_wizard.py`) generating deny-by-default profiles that pass
  `validate_policy_config`; and a web approval inbox on the service layer
  (`create_app(inbox_dir=...)`) that lists pending packets, renders them, and
  emits HMAC-signed decisions by reusing `approval_exchange` signing. No
  kernel gating semantics changed.

## 0.1.0

- Initial package metadata and CLI entry point.
