"""Auditable approval packets with anti-replay binding."""

from __future__ import annotations

import hashlib
import html
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from .plans import ActionStep, TransactionPlan
from .sanitization import safe_json_dumps
from .tools import Tool


class ApprovalDecisionValue(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    DRY_RUN_ONLY = "dry_run_only"
    NARROW_SCOPE = "narrow_scope"


@dataclass(frozen=True)
class ApprovalPacket:
    approval_id: str
    goal_id: str
    plan_id: str
    step_id: str
    step_hash: str
    tool_name: str
    action_summary: str
    risk_level: str
    required_permissions: list[str]
    causal_contract_summary: str
    dry_run_summary: str
    rollback_summary: str
    diff_summary: str | None = None
    alternatives: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    requester: str | None = None
    profile: str = "custom"
    repo: str | None = None
    branch: str | None = None
    file_paths: list[str] = field(default_factory=list)
    expected_sha: str | None = None
    expected_previous: str | None = None
    egress_host: str | None = None
    egress_methods: list[str] = field(default_factory=list)
    cleanup_description: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "goal_id": self.goal_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "step_hash": self.step_hash,
            "tool_name": self.tool_name,
            "action_summary": self.action_summary,
            "risk_level": self.risk_level,
            "required_permissions": list(self.required_permissions),
            "causal_contract_summary": self.causal_contract_summary,
            "dry_run_summary": self.dry_run_summary,
            "rollback_summary": self.rollback_summary,
            "diff_summary": self.diff_summary,
            "alternatives": list(self.alternatives),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "requester": self.requester,
            "profile": self.profile,
            "repo": self.repo,
            "branch": self.branch,
            "file_paths": list(self.file_paths),
            "expected_sha": self.expected_sha,
            "expected_previous": self.expected_previous,
            "egress_host": self.egress_host,
            "egress_methods": list(self.egress_methods),
            "cleanup_description": self.cleanup_description,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ApprovalPacket:
        return cls(
            approval_id=str(data["approval_id"]),
            goal_id=str(data["goal_id"]),
            plan_id=str(data["plan_id"]),
            step_id=str(data["step_id"]),
            step_hash=str(data["step_hash"]),
            tool_name=str(data["tool_name"]),
            action_summary=str(data.get("action_summary", "")),
            risk_level=str(data.get("risk_level", "unknown")),
            required_permissions=[str(value) for value in data.get("required_permissions", ())],
            causal_contract_summary=str(data.get("causal_contract_summary", "")),
            dry_run_summary=str(data.get("dry_run_summary", "")),
            rollback_summary=str(data.get("rollback_summary", "")),
            diff_summary=str(data["diff_summary"]) if data.get("diff_summary") is not None else None,
            alternatives=[str(value) for value in data.get("alternatives", ())],
            created_at=float(data.get("created_at", time.time())),
            expires_at=float(data["expires_at"]) if data.get("expires_at") is not None else None,
            requester=str(data["requester"]) if data.get("requester") is not None else None,
            profile=str(data.get("profile", "custom")),
            repo=str(data["repo"]) if data.get("repo") is not None else None,
            branch=str(data["branch"]) if data.get("branch") is not None else None,
            file_paths=[str(value) for value in data.get("file_paths", ())],
            expected_sha=str(data["expected_sha"]) if data.get("expected_sha") is not None else None,
            expected_previous=(str(data["expected_previous"]) if data.get("expected_previous") is not None else None),
            egress_host=str(data["egress_host"]) if data.get("egress_host") is not None else None,
            egress_methods=[str(value) for value in data.get("egress_methods", ())],
            cleanup_description=(
                str(data["cleanup_description"]) if data.get("cleanup_description") is not None else None
            ),
        )


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    step_hash: str
    decision: ApprovalDecisionValue
    decided_at: float = field(default_factory=time.time)
    approver: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", ApprovalDecisionValue(self.decision))

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "step_hash": self.step_hash,
            "decision": self.decision.value,
            "decided_at": self.decided_at,
            "approver": self.approver,
            "reason": self.reason,
        }


def build_step_hash(
    *,
    goal_id: str,
    plan_id: str,
    step: ActionStep,
    tool: Tool,
) -> str:
    contract = getattr(tool.spec, "causal_contract", None)
    payload = {
        "goal_id": goal_id,
        "plan_id": plan_id,
        "step_id": step.step_id,
        "tool_name": step.tool_name,
        "arguments": step.arguments,
        "required_permissions": [permission.value for permission in step.required_permissions],
        "risk": step.risk.value,
        "causal_contract": safe_json_dumps(contract) if contract is not None else None,
    }
    return hashlib.sha256(safe_json_dumps(payload).encode("utf-8")).hexdigest()


def build_approval_packet(
    *,
    plan: TransactionPlan,
    step: ActionStep,
    tool: Tool,
    dry_run_summary: str,
    profile: str,
    requester: str | None = None,
    expires_in_seconds: float = 300.0,
) -> ApprovalPacket:
    contract = getattr(tool.spec, "causal_contract", None)
    permissions = sorted(permission.value for permission in step.required_permissions)
    rollback_summary = (
        f"{step.reversibility.value}; compensation={step.compensation_strategy.value}; "
        f"rollback_reliability={step.rollback_reliability:.2f}"
    )
    return ApprovalPacket(
        approval_id=str(uuid4()),
        goal_id=plan.goal.goal_id,
        plan_id=plan.plan_id,
        step_id=step.step_id,
        step_hash=build_step_hash(goal_id=plan.goal.goal_id, plan_id=plan.plan_id, step=step, tool=tool),
        tool_name=step.tool_name,
        action_summary=f"{step.tool_name}: {step.reason}",
        risk_level=step.risk.value,
        required_permissions=permissions,
        causal_contract_summary=type(contract).__name__ if contract is not None else "none",
        dry_run_summary=dry_run_summary,
        rollback_summary=rollback_summary,
        alternatives=["deny and leave state unchanged", "request a lower-risk or narrower step"],
        expires_at=time.time() + expires_in_seconds if expires_in_seconds > 0 else None,
        requester=requester,
        profile=profile,
        repo=str(step.arguments["repo"]) if step.arguments.get("repo") is not None else None,
        branch=_approval_branch(step),
        file_paths=[str(step.arguments["path"])] if step.arguments.get("path") is not None else [],
        expected_sha=(str(step.arguments["expected_sha"]) if step.arguments.get("expected_sha") is not None else None),
        expected_previous=(
            str(step.arguments["expected_previous"]) if step.arguments.get("expected_previous") is not None else None
        ),
        egress_host=tool.spec.egress_host,
        egress_methods=list(tool.spec.egress_methods),
        cleanup_description=rollback_summary,
    )


def validate_approval_decision(
    packet: ApprovalPacket,
    decision: ApprovalDecision,
    *,
    current_step_hash: str,
    profile: str,
    now: float | None = None,
) -> str | None:
    if decision.approval_id != packet.approval_id:
        return "approval_id mismatch"
    if decision.step_hash != packet.step_hash or decision.step_hash != current_step_hash:
        return "step_hash mismatch"
    if packet.profile != profile:
        return "profile mismatch"
    if packet.expires_at is not None and (time.time() if now is None else now) > packet.expires_at:
        return "approval expired"
    if decision.decision is ApprovalDecisionValue.DRY_RUN_ONLY:
        return "approval decision is dry_run_only"
    if decision.decision is ApprovalDecisionValue.NARROW_SCOPE:
        return "approval decision requires narrowed scope"
    if decision.decision is not ApprovalDecisionValue.APPROVE:
        return f"approval decision is {decision.decision.value}"
    return None


def render_approval_packet_markdown(packet: ApprovalPacket) -> str:
    data = packet.as_dict()
    lines = ["# Approval Packet", ""]
    for key in (
        "approval_id",
        "goal_id",
        "plan_id",
        "step_id",
        "step_hash",
        "tool_name",
        "action_summary",
        "risk_level",
        "required_permissions",
        "causal_contract_summary",
        "dry_run_summary",
        "rollback_summary",
        "diff_summary",
        "alternatives",
        "requester",
        "profile",
        "created_at",
        "expires_at",
        "repo",
        "branch",
        "file_paths",
        "expected_sha",
        "expected_previous",
        "egress_host",
        "egress_methods",
        "cleanup_description",
    ):
        lines.append(f"- **{key}**: {data[key]}")
    return "\n".join(lines) + "\n"


def render_approval_packet_html(packet: ApprovalPacket) -> str:
    body = html.escape(render_approval_packet_markdown(packet))
    return f"<!doctype html><html><body><pre>{body}</pre></body></html>"


def _approval_branch(step: ActionStep) -> str | None:
    for key in ("branch", "head", "base"):
        value = step.arguments.get(key)
        if value is not None:
            return str(value)
    return None
