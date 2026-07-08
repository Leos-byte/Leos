# Observability — Leos Agent Runtime

Telemetry in Leos is a **side-car, never a gate**. Audit recording behavior is
unchanged: `AuditLog` gained one additive optional hook —
`on_event: Callable[[AuditEvent], None] | None = None` — invoked *after* an
event is appended, with sink exceptions suppressed so telemetry can never
gate, mutate, or lose an audit record. Audit output is byte-identical with and
without a sink attached (tested).

## Attaching sinks

```python
from leos_agent.audit import AuditLog
from leos_agent.observability import (
    OTelAuditSink, PrometheusMetrics, StructlogAuditSink, compose_sinks,
)

metrics = PrometheusMetrics()
audit = AuditLog(on_event=compose_sinks(metrics, StructlogAuditSink()))
```

`compose_sinks` fans one hook out to several sinks and isolates each — one
failing sink never stops the others.

## Sinks

### PrometheusMetrics (dependency-free)

Counts the safety-relevant signals and renders the standard Prometheus
exposition format from `render_text()` (serve it from a `/metrics` endpoint):

| Counter | Source events |
|---|---|
| `leos_approvals_approved_total` | `approval.used` |
| `leos_approvals_denied_total` | `approval.rejected` |
| `leos_rollbacks_attempted/succeeded/failed_total` | `rollback_*` |
| `leos_egress_blocked_total` | `egress.blocked`, `rollback.egress_blocked` |
| `leos_steps_verified/blocked/failed_total` | `step.verified`, `step.blocked`, `step.execution_failed`, `step.dry_run_failed`, `step.verification_failed` |
| `leos_goals_succeeded/failed_total` | `goal.status_changed` (`to_status`) |
| `leos_audit_events_total` | every event |

### StructlogAuditSink (optional `structlog`)

Emits each audit event as a structured log line (`event_type` as the event,
message/sequence/event_id/payload as fields). Pass a ready `logger` (anything
with `info(event, **fields)`) or let it import `structlog` lazily.

### OTelAuditSink (optional `opentelemetry-api`)

Emits one OpenTelemetry span per audit event — transaction-step events
(`step.*`) appear as one span per pipeline stage — with `leos.*` attributes
(payload values stringified when non-primitive). Pass a ready `tracer` or let
it import the OTel API lazily.

## Optional dependencies

```bash
pip install "leos-agent[observability]"   # structlog + opentelemetry-api
```

Constructing a sink without its dependency (and without injecting a
logger/tracer) raises a typed `ObservabilitySinkUnavailable` — the same
fail-with-typed-error pattern as `SandboxUnavailable`. `PrometheusMetrics`
needs no dependency at all. The core runtime dependency remains `jsonschema`
only.

## Safety invariants

- The hook default is `None`: nothing changes unless a sink is attached.
- Sink exceptions are suppressed at the audit boundary (and again per-sink in
  `compose_sinks`): a broken exporter cannot block or corrupt recording.
- Sinks observe events **after** sanitization — secret material is already
  redacted/blocked before any sink sees the event.
