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

## Non-goals

Leos is NOT: an AGI agent, a chatbot, a code autopilot, a network proxy, or
a production multi-tenant runtime. It is a safety-first autonomous-agent kernel
for bounded, auditable actions.
