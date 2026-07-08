"""Observability side-car sinks for audit events.

These map :class:`~leos_agent.audit.AuditEvent` records to standard telemetry
as *optional subscribers* attached via ``AuditLog(on_event=...)``. Recording
behavior is unchanged: the hook fires after an event is appended, and the
audit log suppresses sink exceptions, so telemetry can never gate, mutate, or
lose an audit record.

Optional dependencies (``structlog``, ``opentelemetry``) are imported lazily
at sink construction and surface a typed :class:`ObservabilitySinkUnavailable`
when absent; ready objects (logger, tracer) may also be injected directly.
``PrometheusMetrics`` is dependency-free and renders the standard exposition
format itself.
"""

from __future__ import annotations

import importlib
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

from .audit import AuditEvent
from .errors import LeosError


class ObservabilitySinkUnavailable(LeosError):
    """Raised when an optional observability dependency is missing."""


AuditSink = Callable[[AuditEvent], None]


def compose_sinks(*sinks: AuditSink) -> AuditSink:
    """Fan one ``on_event`` hook out to several sinks.

    Each sink is isolated: one failing sink never stops the others (and the
    audit log itself additionally suppresses anything raised here).
    """

    def fan_out(event: AuditEvent) -> None:
        for sink in sinks:
            with suppress(Exception):
                sink(event)

    return fan_out


# Mapping from audit event types to counter names. Unmapped events still count
# toward ``leos_audit_events_total``.
_COUNTER_FOR_EVENT = {
    "approval.used": "leos_approvals_approved_total",
    "approval.rejected": "leos_approvals_denied_total",
    "rollback_attempted": "leos_rollbacks_attempted_total",
    "rollback_succeeded": "leos_rollbacks_succeeded_total",
    "rollback_failed": "leos_rollbacks_failed_total",
    "egress.blocked": "leos_egress_blocked_total",
    "rollback.egress_blocked": "leos_egress_blocked_total",
    "step.verified": "leos_steps_verified_total",
    "step.blocked": "leos_steps_blocked_total",
    "step.execution_failed": "leos_steps_failed_total",
    "step.dry_run_failed": "leos_steps_failed_total",
    "step.verification_failed": "leos_steps_failed_total",
}

_GOAL_COUNTER_FOR_STATUS = {
    "succeeded": "leos_goals_succeeded_total",
    "failed": "leos_goals_failed_total",
}


class PrometheusMetrics:
    """Dependency-free counters over audit events, in Prometheus text format.

    Attach with ``AuditLog(on_event=metrics)`` and expose ``render_text()``
    from a ``/metrics`` endpoint. Counters cover the safety-relevant signals:
    approvals approved/denied, rollbacks, egress blocks, step outcomes, and
    goal success/failure.
    """

    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()

    def __call__(self, event: AuditEvent) -> None:
        self._counters["leos_audit_events_total"] += 1
        counter = _COUNTER_FOR_EVENT.get(event.event_type)
        if counter is not None:
            self._counters[counter] += 1
        if event.event_type == "goal.status_changed":
            goal_counter = _GOAL_COUNTER_FOR_STATUS.get(str(event.payload.get("to_status", "")))
            if goal_counter is not None:
                self._counters[goal_counter] += 1

    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    def render_text(self) -> str:
        lines = []
        for name in sorted(self._counters):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {self._counters[name]}")
        return "\n".join(lines) + "\n"


class StructLogger(Protocol):
    """Minimal structured-logger surface (matched by ``structlog`` loggers)."""

    def info(self, event: str, **kwargs: Any) -> None: ...


class StructlogAuditSink:
    """Emit each audit event as a structured log line.

    Pass a ready ``logger`` (anything with ``info(event, **fields)``), or let
    the sink import the optional ``structlog`` package lazily.
    """

    def __init__(self, *, logger: StructLogger | None = None) -> None:
        self._logger = logger if logger is not None else _load_structlog_logger()

    def __call__(self, event: AuditEvent) -> None:
        self._logger.info(
            event.event_type,
            message=event.message,
            sequence=event.sequence,
            event_id=event.event_id,
            created_at=event.created_at,
            payload=event.payload,
        )


class Span(Protocol):
    def set_attribute(self, key: str, value: Any) -> None: ...

    def __enter__(self) -> Span: ...

    def __exit__(self, *exc: object) -> None: ...


class Tracer(Protocol):
    def start_as_current_span(self, name: str) -> Span: ...


class OTelAuditSink:
    """Emit one OpenTelemetry span per audit event.

    Transaction-step events (``step.*``) therefore appear as one span per
    pipeline stage. Pass a ready ``tracer``, or let the sink import the
    optional ``opentelemetry`` API lazily.
    """

    def __init__(self, *, tracer: Tracer | None = None, tracer_name: str = "leos-agent") -> None:
        self._tracer = tracer if tracer is not None else _load_otel_tracer(tracer_name)

    def __call__(self, event: AuditEvent) -> None:
        with self._tracer.start_as_current_span(event.event_type) as span:
            span.set_attribute("leos.event_type", event.event_type)
            span.set_attribute("leos.message", event.message)
            span.set_attribute("leos.sequence", event.sequence)
            span.set_attribute("leos.event_id", event.event_id)
            for key, value in event.payload.items():
                if isinstance(value, (str, int, float, bool)):
                    span.set_attribute(f"leos.payload.{key}", value)
                else:
                    span.set_attribute(f"leos.payload.{key}", str(value))


def _load_structlog_logger() -> StructLogger:
    try:
        structlog = importlib.import_module("structlog")
    except ImportError as exc:
        raise ObservabilitySinkUnavailable("StructlogAuditSink requires the optional 'structlog' package") from exc
    logger: StructLogger = structlog.get_logger("leos_agent.audit")  # pragma: no cover - needs structlog installed
    return logger  # pragma: no cover - needs structlog installed


def _load_otel_tracer(tracer_name: str) -> Tracer:
    try:
        trace = importlib.import_module("opentelemetry.trace")
    except ImportError as exc:
        raise ObservabilitySinkUnavailable("OTelAuditSink requires the optional 'opentelemetry-api' package") from exc
    tracer: Tracer = trace.get_tracer(tracer_name)  # pragma: no cover - needs opentelemetry installed
    return tracer  # pragma: no cover - needs opentelemetry installed
