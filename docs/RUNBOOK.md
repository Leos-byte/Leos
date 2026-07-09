# Operations Runbook

Day-2 operations for a deployed Leos service (`leos serve`, see
`docs/DEPLOYMENT.md` for install/TLS/backup basics).

## Health checks

| Endpoint | Meaning | On failure |
| --- | --- | --- |
| `GET /healthz` | Process is up and serving | Restart the container; check `docker logs` for the startup configuration summary — a missing `LEOS_SERVER_API_KEY` or a secret in the TOML file aborts startup by design |
| `GET /readyz` | The `production_github_only` policy profile loads; reports `writes_enabled` | A 503 means the installed package is broken — redeploy; `writes_enabled: true` outside a planned apply window is itself an alert |

Both endpoints are unauthenticated; everything else needs `X-Leos-Api-Key`.

## Metrics and alerts

Attach `PrometheusMetrics` (the `observability` extra is not required for the
counters themselves) via `AuditLog(on_event=...)` and export `render_text()`.
Suggested alerts on the built-in counters:

| Signal | Counters | Why it matters |
| --- | --- | --- |
| Approval denial spike | `leos_approvals_denied_total` vs `leos_approvals_approved_total` | Someone (or something) is repeatedly requesting actions operators refuse |
| Rollbacks occurring | `leos_rollbacks_attempted_total`, `leos_rollbacks_failed_total` | Verification failures are triggering rollback; **failed** rollbacks produce `ManualRecoveryPacket`s that need a human |
| Egress blocks | `leos_egress_blocked_total` | A tool tried to reach a host/method outside policy — treat as an incident, not noise |
| Step failures | `leos_steps_failed_total`, `leos_steps_blocked_total` | Sustained growth means plans are fighting the policy or the world changed |
| Goal failures | `leos_goals_failed_total` | End-to-end outcomes regressing |

HTTP-level: sustained 401s (bad/missing API key), 429s (write rate limit),
413s (oversized bodies) indicate abuse or a misconfigured client.

## Audit chain integrity patrol

Audit logs are append-only JSONL with hash chaining, one directory per plan
under `data_dir/audits/{plan_id}/`. Verify integrity and scan for anomalies:

```bash
leos audit inspect --path data-dir/audits/<plan_id>/apply-<ns>.jsonl
leos audit-check data-dir/audits/<plan_id>/apply-<ns>.jsonl
```

`Integrity: FAIL` means the file was modified after the fact — preserve the
file, treat it as an incident, and compare against backups. Run the patrol on
a schedule (e.g. daily over the newest apply logs).

## Stuck or orphaned queue leases

Postgres task-queue workers hold leases (`lease_expires_at`); a crashed worker
leaves its task `RUNNING` until the lease expires. Recovery is
`reap_expired_leases()` — tasks with attempts remaining are re-queued, the
rest become `TIMED_OUT`:

```python
from leos_agent import PostgresTaskQueue
with PostgresTaskQueue(dsn) as queue:
    for task in queue.reap_expired_leases():
        print(task.task_id, task.status, task.attempts)
```

Run it periodically (a cron-style loop in one worker is enough — it is
concurrency-safe). A task that keeps returning to `TIMED_OUT` has exhausted
`max_attempts`: inspect its plan, fix the cause, and re-enqueue explicitly.

## Backups and restore

Back up (see `docs/DEPLOYMENT.md` for locations): `audits/` (evidence),
`receipts/` (consume-once markers — loss allows re-use of an unexpired signed
decision), the inbox directory (pending packets/decisions), and Postgres via
`pg_dump`. Restore order: Postgres first, then the file trees; then run the
audit integrity patrol over restored logs before trusting them.

## Key and secret rotation

- **API key** (`LEOS_SERVER_API_KEY`): zero-downtime — set it to
  `old,new`, restart, migrate callers to `new`, then remove `old` and restart
  again. Keys must be 32+ characters; weak keys refuse to start.
- **Approval HMAC secret** (`LEOS_APPROVAL_HMAC_SECRET`): rotation
  **invalidates every signed decision and pending approval in flight**.
  Drain first: stop issuing new packets, let operators decide or expire the
  pending ones (check `GET /inbox`), rotate, then resume. Never run two HMAC
  secrets side by side.
- **GitHub credentials**: prefer the GitHub App flow
  (`LEOS_GITHUB_APP_*`, `docs/GETTING_STARTED_PRODUCTION_GITHUB.md`) — tokens
  are minted short-lived and refresh themselves; "rotation" is rotating the
  App private key file (600 permissions, or doctor flags it). For PATs,
  rotate in the provider and update `LEOS_GITHUB_TOKEN`; the explicit PAT
  always wins over the App configuration.
- After any credential change: `leos serve --check` validates configuration
  and prints a redacted summary; `leos doctor` reports `github_auth_mode`
  and App misconfiguration.

## Incident quick reference

| Symptom | First moves |
| --- | --- |
| `/apply` returns 403 unexpectedly | Expected fail-closed behavior. Check in order: `LEOS_ENABLE_REAL_GITHUB_WRITES`, decision already consumed (receipt exists), approval expired, plan digest changed since approval |
| Rollback failed | Find the `ManualRecoveryPacket` in the audit log; it lists the affected resources and suggested actions — this is a human task by design |
| Worker host lost | `reap_expired_leases()` re-queues its tasks; verify no task exceeded `max_attempts` |
| Audit integrity FAIL | Preserve the file, restore from backup for comparison, rotate the API key, review access to the data volume |
