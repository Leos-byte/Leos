# Approval UX

Human approval in Leos is represented as an `ApprovalRequest`, not as a free-form model claim.

Each request contains:

- goal
- action
- impact
- risk
- reversibility
- evidence
- alternatives
- minimal permissions

This summary is intended for CLI or future UI approval cards. It exposes an auditable decision summary without exposing private model reasoning.

The interactive CLI gate denies by default when there is no TTY or when the user does not explicitly approve before the timeout.

## Web approval inbox

The service layer (`docs/SERVICE.md`) can host a web approval inbox over a
`FileApprovalGate`-compatible exchange directory — pass `inbox_dir=` to
`create_app` (`packets/` and `decisions/` inside it):

- `GET /inbox` — pending packets (packets without a decision file), with
  approval_id, tool, risk, action summary, expiry, and profile.
- `GET /inbox/{approval_id}` — the full packet rendered as HTML
  (`render_approval_packet_html`): dry-run summary, causal contract, rollback
  scope, diff, permissions.
- `POST /inbox/{approval_id}/decide` — emit an approve/deny decision. The
  decision is built with `build_decision_for_packet` and HMAC-signed with
  `sign_approval_decision` (requires `LEOS_APPROVAL_HMAC_SECRET` in the
  server environment) — the inbox **reuses** `approval_exchange` signing, it
  never reimplements it. A packet accepts exactly one decision (409 on a
  second attempt); a running `FileApprovalGate` consumes the decision file
  through its existing signature/allowlist/permission checks.

The inbox emits decisions only. Execution still happens on the kernel path:
an inbox decision has no effect unless a gate holding the matching packet,
step hash, and signature secret accepts it.
