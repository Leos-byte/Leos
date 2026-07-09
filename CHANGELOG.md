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
- Added a CI `integration` job that verifies the optional backends against
  real runtimes on every push: the Postgres store contract and task-queue
  tests run against a `postgres:16` service container
  (`LEOS_TEST_POSTGRES_DSN`), and new gated real-runtime sandbox tests
  (`tests/test_sandbox_backends_integration.py`) execute rootless-podman
  containers to observe egress denial, non-root uid, timeout kill, output
  truncation, and read-only rootfs. The job publishes a skip report so every
  remaining skip names its missing runtime. Fixed the missing-optional-package
  tests to simulate absence via `sys.modules` (they previously mocked
  `builtins.__import__`, which `importlib.import_module` bypasses once the
  package is installed), and made the real-server Postgres tests deterministic
  against a persistent database by starting from empty tables. No kernel
  gating semantics changed.
- Added commit-bound backend smoke evidence: `scripts/sandbox_smoke.py`
  (real-container isolation: egress denial, non-root uid, read-only rootfs,
  pids/memory limits configured and trigger-enforced, timeout kill, microVM
  fail-closed) and `scripts/queue_smoke.py` (multi-process exactly-once
  consumption against live Postgres: no double claims, killed-worker lease
  reap and rescue, idempotency dedupe; the DSN never enters the evidence).
  Both run in the CI `integration` job and upload exact-SHA artifacts after
  in-job validation. `check_production_readiness.py` gained opt-in
  `--require-sandbox-evidence` / `--require-queue-evidence` gates (default
  off; existing behavior unchanged) and asserts the CI wiring;
  `download_smoke_evidence.py` is parametrized by artifact prefix, filename,
  and event. No kernel gating semantics changed.

## 0.1.0

- Initial package metadata and CLI entry point.
