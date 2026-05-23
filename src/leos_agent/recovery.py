"""Manual recovery packets for rollback failures and blocked compensation."""

from __future__ import annotations

import html
import time
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from .sanitization import redact_secrets


@dataclass(frozen=True)
class ManualRecoveryPacket:
    recovery_id: str
    goal_id: str | None
    plan_id: str | None
    step_id: str
    tool_name: str
    reason: str
    risk_level: str
    suggested_actions: list[str]
    rollback_summary: str
    affected_resources: list[str]
    created_at: float
    profile: str
    token_redacted: bool = True

    def as_dict(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            redact_secrets(
                {
                    "recovery_id": self.recovery_id,
                    "goal_id": self.goal_id,
                    "plan_id": self.plan_id,
                    "step_id": self.step_id,
                    "tool_name": self.tool_name,
                    "reason": self.reason,
                    "risk_level": self.risk_level,
                    "suggested_actions": list(self.suggested_actions),
                    "rollback_summary": self.rollback_summary,
                    "affected_resources": list(self.affected_resources),
                    "created_at": self.created_at,
                    "profile": self.profile,
                    "token_redacted": self.token_redacted,
                }
            ),
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ManualRecoveryPacket:
        return cls(
            recovery_id=str(data["recovery_id"]),
            goal_id=str(data["goal_id"]) if data.get("goal_id") is not None else None,
            plan_id=str(data["plan_id"]) if data.get("plan_id") is not None else None,
            step_id=str(data["step_id"]),
            tool_name=str(data["tool_name"]),
            reason=str(data["reason"]),
            risk_level=str(data.get("risk_level", "unknown")),
            suggested_actions=[str(value) for value in data.get("suggested_actions", ())],
            rollback_summary=str(data.get("rollback_summary", "")),
            affected_resources=[str(value) for value in data.get("affected_resources", ())],
            created_at=float(data.get("created_at", time.time())),
            profile=str(data.get("profile", "custom")),
            token_redacted=bool(data.get("token_redacted", True)),
        )

    @classmethod
    def build(
        cls,
        *,
        step_id: str,
        tool_name: str,
        reason: str,
        risk_level: str,
        profile: str,
        goal_id: str | None = None,
        plan_id: str | None = None,
        rollback_summary: str = "Rollback could not complete automatically.",
        affected_resources: list[str] | None = None,
        suggested_actions: list[str] | None = None,
    ) -> ManualRecoveryPacket:
        return cls(
            recovery_id=str(uuid4()),
            goal_id=goal_id,
            plan_id=plan_id,
            step_id=step_id,
            tool_name=tool_name,
            reason=reason,
            risk_level=risk_level,
            suggested_actions=suggested_actions
            or [
                "Inspect the affected resource manually.",
                "Apply the compensating action outside the agent if safe.",
                "Record the final recovery state in the audit trail.",
            ],
            rollback_summary=rollback_summary,
            affected_resources=affected_resources or [],
            created_at=time.time(),
            profile=profile,
        )

    def render_markdown(self) -> str:
        data = self.as_dict()
        lines = ["# Manual Recovery Packet", ""]
        for key in (
            "recovery_id",
            "goal_id",
            "plan_id",
            "step_id",
            "tool_name",
            "reason",
            "risk_level",
            "rollback_summary",
            "affected_resources",
            "suggested_actions",
            "profile",
            "created_at",
            "token_redacted",
        ):
            lines.append(f"- **{key}**: {data[key]}")
        return "\n".join(lines) + "\n"

    def render_html(self) -> str:
        return f"<!doctype html><html><body><pre>{html.escape(self.render_markdown())}</pre></body></html>"
