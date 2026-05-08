"""Deterministic replay from audit events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Sequence

from .audit import AuditLog
from .state import TrustLevel, WorldState


@dataclass
class ReplayResult:
    ok: bool
    state: WorldState
    applied_events: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


class AuditReplayer:
    """Reconstructs runtime state from append-only audit events."""

    def replay(self, audit_log: AuditLog, *, verify_integrity: bool = True) -> ReplayResult:
        records = audit_log.records()
        return self.replay_records(records, verify_integrity=verify_integrity)

    def replay_records(self, records: Sequence[Mapping[str, Any]], *, verify_integrity: bool = True) -> ReplayResult:
        if verify_integrity:
            integrity = AuditLog.verify_event_records(records)
            if not integrity.ok:
                return ReplayResult(False, WorldState(), errors=list(integrity.data.get("issues", [])))

        state = WorldState()
        applied_events = 0
        for record in records:
            event_type = record.get("event_type")
            payload = record.get("payload", {})
            if not isinstance(payload, Mapping):
                continue
            if event_type == "step.executed":
                observed = payload.get("observed", {})
                if isinstance(observed, Mapping):
                    trust = TrustLevel(str(payload.get("observed_trust", TrustLevel.TOOL_REPORTED.value)))
                    state.observe(dict(observed), trust_level=trust)
                    applied_events += 1
            elif event_type == "step.verified":
                verified = payload.get("verified", ())
                if isinstance(verified, list):
                    trust = TrustLevel(str(payload.get("verified_trust", TrustLevel.VERIFIED.value)))
                    state.mark_trust(verified, trust)
            elif event_type == "memory.written":
                key = payload.get("key")
                value = payload.get("value")
                if isinstance(key, str):
                    state.set_fact(f"memory:{key}", value, trust_level=TrustLevel.VERIFIED)
                    applied_events += 1
        return ReplayResult(True, state, applied_events=applied_events)


def replay_audit_log(audit_log: AuditLog, *, verify_integrity: bool = True) -> ReplayResult:
    return AuditReplayer().replay(audit_log, verify_integrity=verify_integrity)
