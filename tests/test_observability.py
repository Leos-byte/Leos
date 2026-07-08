"""Tests for observability side-car sinks and the additive AuditLog hook.

The ``on_event`` hook is the only kernel touch: optional, default ``None``,
invoked after an event is appended, and wrapped so a sink exception can never
affect recording. Audit output must be byte-identical with and without a sink.
"""

from __future__ import annotations

import functools
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from leos_agent.audit import AuditEvent, AuditLog
from leos_agent.observability import (
    ObservabilitySinkUnavailable,
    OTelAuditSink,
    PrometheusMetrics,
    StructlogAuditSink,
    compose_sinks,
)


def _event(event_type: str, **payload: Any) -> AuditEvent:
    return AuditEvent(event_type=event_type, message="m", payload=payload)


class AuditLogHookTests(unittest.TestCase):
    def test_hook_receives_each_appended_event(self) -> None:
        seen: list[AuditEvent] = []
        log = AuditLog(on_event=seen.append)
        log.record("step.verified", "ok", step_id="s1")
        log.record("step.blocked", "no", step_id="s2")
        self.assertEqual([e.event_type for e in seen], ["step.verified", "step.blocked"])
        self.assertEqual(seen[0].payload["step_id"], "s1")

    def test_hook_exception_never_affects_recording(self) -> None:
        def broken(event: AuditEvent) -> None:
            raise RuntimeError("sink down")

        log = AuditLog(on_event=broken)
        event = log.record("step.verified", "ok")
        self.assertEqual(event.event_type, "step.verified")
        self.assertEqual(len(log.events), 1)
        self.assertTrue(log.verify_integrity().ok)

    def test_audit_file_output_is_byte_identical_with_and_without_sink(self) -> None:
        fixed_event = functools.partial(AuditEvent, created_at=1000.0, event_id="fixed-id")

        def record_fixed(path: Path, log_kwargs: dict[str, Any]) -> bytes:
            with mock.patch("leos_agent.audit.AuditEvent", fixed_event):
                log = AuditLog(path=path, **log_kwargs)
                log.record("step.verified", "ok", step_id="s1")
            return path.read_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            plain = record_fixed(Path(tmp) / "plain.jsonl", {})
            sunk = record_fixed(Path(tmp) / "sink.jsonl", {"on_event": lambda event: None})
        self.assertEqual(plain, sunk)

    def test_default_is_none_and_unchanged_behavior(self) -> None:
        log = AuditLog()
        self.assertIsNone(log.on_event)
        log.record("step.verified", "ok")
        self.assertTrue(log.verify_integrity().ok)


class PrometheusMetricsTests(unittest.TestCase):
    def test_counts_approvals_rollbacks_egress_steps_and_goals(self) -> None:
        metrics = PrometheusMetrics()
        for event in (
            _event("approval.used"),
            _event("approval.used"),
            _event("approval.rejected"),
            _event("rollback_attempted"),
            _event("rollback_succeeded"),
            _event("rollback_failed"),
            _event("egress.blocked"),
            _event("rollback.egress_blocked"),
            _event("step.verified"),
            _event("step.blocked"),
            _event("step.execution_failed"),
            _event("goal.status_changed", to_status="succeeded"),
            _event("goal.status_changed", to_status="failed"),
            _event("goal.status_changed", to_status="running"),
        ):
            metrics(event)
        counters = metrics.counters()
        self.assertEqual(counters["leos_approvals_approved_total"], 2)
        self.assertEqual(counters["leos_approvals_denied_total"], 1)
        self.assertEqual(counters["leos_rollbacks_attempted_total"], 1)
        self.assertEqual(counters["leos_rollbacks_succeeded_total"], 1)
        self.assertEqual(counters["leos_rollbacks_failed_total"], 1)
        self.assertEqual(counters["leos_egress_blocked_total"], 2)
        self.assertEqual(counters["leos_steps_verified_total"], 1)
        self.assertEqual(counters["leos_steps_blocked_total"], 1)
        self.assertEqual(counters["leos_steps_failed_total"], 1)
        self.assertEqual(counters["leos_goals_succeeded_total"], 1)
        self.assertEqual(counters["leos_goals_failed_total"], 1)
        self.assertEqual(counters["leos_audit_events_total"], 14)

    def test_render_text_uses_prometheus_exposition_format(self) -> None:
        metrics = PrometheusMetrics()
        metrics(_event("step.verified"))
        text = metrics.render_text()
        self.assertIn("# TYPE leos_steps_verified_total counter", text)
        self.assertIn("leos_steps_verified_total 1", text)
        self.assertIn("leos_audit_events_total 1", text)

    def test_unmapped_events_only_count_toward_total(self) -> None:
        metrics = PrometheusMetrics()
        metrics(_event("loop.started"))
        counters = metrics.counters()
        self.assertEqual(counters["leos_audit_events_total"], 1)
        self.assertEqual(counters.get("leos_steps_verified_total", 0), 0)

    def test_works_as_audit_log_hook(self) -> None:
        metrics = PrometheusMetrics()
        log = AuditLog(on_event=metrics)
        log.record("step.verified", "ok")
        self.assertEqual(metrics.counters()["leos_steps_verified_total"], 1)


class _FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.calls.append((event, kwargs))


class StructlogSinkTests(unittest.TestCase):
    def test_maps_event_fields_to_structured_log(self) -> None:
        logger = _FakeLogger()
        sink = StructlogAuditSink(logger=logger)
        sink(_event("step.verified", step_id="s1"))
        event_name, fields = logger.calls[0]
        self.assertEqual(event_name, "step.verified")
        self.assertEqual(fields["message"], "m")
        self.assertEqual(fields["payload"], {"step_id": "s1"})
        self.assertIn("sequence", fields)

    def test_missing_structlog_raises_typed_error(self) -> None:
        real_import = __import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "structlog":
                raise ImportError("no structlog")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=fake_import),
            self.assertRaises(ObservabilitySinkUnavailable),
        ):
            StructlogAuditSink()


class _FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def __enter__(self) -> _FakeSpan:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []

    def start_as_current_span(self, name: str) -> _FakeSpan:
        span = _FakeSpan(name)
        self.spans.append(span)
        return span


class OTelSinkTests(unittest.TestCase):
    def test_emits_span_per_event_with_attributes(self) -> None:
        tracer = _FakeTracer()
        sink = OTelAuditSink(tracer=tracer)
        sink(_event("step.executed", step_id="s1", tool="echo"))
        self.assertEqual(len(tracer.spans), 1)
        span = tracer.spans[0]
        self.assertEqual(span.name, "step.executed")
        self.assertEqual(span.attributes["leos.event_type"], "step.executed")
        self.assertEqual(span.attributes["leos.payload.step_id"], "s1")
        self.assertEqual(span.attributes["leos.payload.tool"], "echo")

    def test_non_primitive_payload_values_are_stringified(self) -> None:
        tracer = _FakeTracer()
        sink = OTelAuditSink(tracer=tracer)
        sink(_event("tool.output", data={"nested": True}))
        self.assertIsInstance(tracer.spans[0].attributes["leos.payload.data"], str)

    def test_missing_opentelemetry_raises_typed_error(self) -> None:
        real_import = __import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "opentelemetry" or name.startswith("opentelemetry."):
                raise ImportError("no opentelemetry")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=fake_import),
            self.assertRaises(ObservabilitySinkUnavailable),
        ):
            OTelAuditSink()


class ComposeSinksTests(unittest.TestCase):
    def test_fans_out_to_all_sinks(self) -> None:
        first: list[str] = []
        second: list[str] = []
        combined = compose_sinks(lambda e: first.append(e.event_type), lambda e: second.append(e.event_type))
        combined(_event("step.verified"))
        self.assertEqual(first, ["step.verified"])
        self.assertEqual(second, ["step.verified"])

    def test_one_failing_sink_does_not_stop_others(self) -> None:
        seen: list[str] = []

        def broken(event: AuditEvent) -> None:
            raise RuntimeError("down")

        combined = compose_sinks(broken, lambda e: seen.append(e.event_type))
        combined(_event("step.verified"))
        self.assertEqual(seen, ["step.verified"])


if __name__ == "__main__":
    unittest.main()
