# Service Layer — Leos Operator HTTP API

`leos_agent.server` is a **thin transport shell** over the existing operator
flow. It inlines no gating decisions: every write delegates to
`apply_operator_plan` (`github_operator.py`), which enforces the full
signed-apply path — the `LEOS_ENABLE_REAL_GITHUB_WRITES` environment gate,
plan/approval/decision digest + profile + plan_id matching,
`PolicyEngine.from_profile`, runtime egress enforcement, the
`PreparedFileApprovalGate` (signed, expiring, consume-once decisions), and a
VERIFIED-only success check.

FastAPI is an **optional dependency**:

```bash
pip install "leos-agent[server]"   # fastapi + uvicorn
```

Without it, `create_app` raises a typed `ServerUnavailable` (mirroring
`SandboxUnavailable`). The core runtime dependency remains `jsonschema` only.

## Starting the service

```python
from pathlib import Path
from leos_agent.server import create_app

app = create_app(api_key="...", data_dir=Path("/var/lib/leos"))
# uvicorn module:app
```

`create_app` **fails closed**: an API key (argument or `LEOS_SERVER_API_KEY`)
is mandatory; the service refuses to start unauthenticated
(`ServerConfigurationError`). API docs endpoints are disabled.

## Authentication model

- Every endpoint except `/healthz` and `/readyz` requires the
  `X-Leos-Api-Key` header, compared in constant time.
- Boundary auth protects the *transport only*. It never substitutes for the
  `ApprovalGate`: an authenticated caller still cannot apply anything without
  a signed, unexpired, digest-matching, consume-once approval decision.
- Secrets are read from the **server environment** at request time
  (`LEOS_GITHUB_TOKEN`, `LEOS_APPROVAL_HMAC_SECRET`) — they are never accepted
  in request bodies and never echoed in responses. Audit records pass the
  standard sanitization boundary, so tokens cannot appear in `/audit` output.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness (no auth) |
| GET | `/readyz` | Policy profile loads; reports `writes_enabled` (no auth) |
| POST | `/plans` | Build a draft operator plan from `{repo, issue_number}` (read-only GitHub dry run) |
| POST | `/plans/validate` | Validate a plan: `{ready, issues}` |
| POST | `/approvals` | Emit signed approval packets for a ready plan (`build_approval_bundle`) |
| POST | `/approvals/decide` | Emit an HMAC-signed decision bundle (`build_signed_decision_bundle`; requires `LEOS_APPROVAL_HMAC_SECRET`) |
| POST | `/apply` | Delegate to `apply_operator_plan`; 403 with the gate's message on any refusal |
| GET | `/audit/{plan_id}` | Recorded audit events for the plan's applies |
| GET | `/trace/{plan_id}` | Self-contained HTML trace (`trace_viewer.render_trace_html`) |

## Refusal semantics (`/apply` → 403)

`/apply` returns `403` with the underlying gate message when:

- `LEOS_ENABLE_REAL_GITHUB_WRITES` is not `1` (writes are disabled by default),
- `LEOS_GITHUB_TOKEN` or `LEOS_APPROVAL_HMAC_SECRET` is not configured,
- the plan fails validation, or the approval/decision profile, plan_id, or
  plan digest does not match,
- a decision signature is invalid or the decision was already consumed
  (replay), or
- execution was blocked / verification failed (non-VERIFIED steps).

In all refusal cases the tests assert **no write reached GitHub**.

## Storage layout

Under `data_dir`:

- `audits/{plan_id}/apply-<ns>.jsonl` — append-only audit log per apply
- `receipts/` — consume-once approval receipts (`O_CREAT|O_EXCL`), shared
  across applies so a decision can never be replayed

Path parameters are restricted to `[A-Za-z0-9._-]`; traversal is rejected.
