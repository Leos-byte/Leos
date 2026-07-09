# Security Policy — Leos Agent Runtime

## Reporting Security Issues

Report suspected vulnerabilities to the project maintainers. Do not disclose
publicly before a fix is available.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes (development) |

## Sensitive Data Definition

Sensitive data includes: API keys, tokens, passwords, credentials, personal
identifiers, and any value wrapped in the `Secret` class.

## Secret Boundary

- `Secret` values are blocked from `secrets_allowed=False` tools.
- `Secret` values are redacted in audit log arguments.
- `SecretBoundaryViolation` is raised when raw secrets attempt memory storage.
- `SecretLeakedToUntrustedTool` blocks secrets passed to non-secret tools.

## Current Safety Guarantees

- Unknown tools cannot execute.
- Missing permissions block actions.
- Dry-run failure prevents execution.
- Workspace path escape is blocked.
- Audit hash chain detects tampering.
- Rollback is attempted for reversible steps.
- Policy-as-code cannot directly approve actions.

## Known Limitations

- WorkspaceSubprocessSandboxRunner is NOT a production isolation boundary.
- No production browser sandbox yet.
- No production network sandbox yet.
- No Docker/microVM runner enabled by default.
- No real vendor LLM integration bundled.
- SQLite queue is local durability, not distributed consensus.
- ToolResult.data secret redaction is not globally enforced; tools must not
  return secrets in their own data payloads.
- Causal model is lightweight action-consequence validation, not full
  structural causal inference.

## HTTP Service Threat Model

The optional service layer (`leos_agent.server`, see `docs/SERVICE.md` and
`docs/DEPLOYMENT.md`) adds a network-reachable surface. Its boundary controls
complement — never replace — the kernel gates in `docs/THREAT_MODEL.md`:

- **Authentication**: every non-health endpoint requires `X-Leos-Api-Key`,
  compared in constant time against every configured key. Multiple
  comma-separated keys enable zero-downtime rotation; keys shorter than 32
  characters refuse to start. Boundary auth never substitutes for the
  approval gate — an authenticated caller still cannot apply anything without
  a signed, unexpired, consume-once decision bound to the plan digest.
- **No TLS in-process**: the service must sit behind a reverse proxy that
  terminates TLS; bind it to localhost or an internal network only.
- **Abuse limits**: write endpoints (`/approvals`, `/approvals/decide`,
  `/apply`, inbox decisions) are throttled by an in-memory token bucket
  (HTTP 429), and oversized request bodies are rejected (HTTP 413). Both are
  configurable (`rate_limit_per_minute`, `max_body_bytes`).
- **Identifier hygiene**: plan and approval identifiers are validated against
  a strict character class before any filesystem access (path traversal is
  rejected with 400).
- **Secrets**: read from the server environment only; never accepted in
  request bodies, echoed in responses, or written to configuration files
  (secret-shaped TOML keys abort startup).
- **Dependency surface**: optional extras (fastapi/uvicorn/psycopg/keyring/
  hvac/structlog/opentelemetry) are audited in CI with `pip-audit`; red-team
  coverage for the inbox surface lives in `tests/redteam/test_inbox_redteam.py`.

## Non-goals

Leos is NOT: an AGI agent, a chatbot, a code autopilot, a network proxy, or
a production multi-tenant runtime. It is a safety-first autonomous-agent kernel
for bounded, auditable actions.
