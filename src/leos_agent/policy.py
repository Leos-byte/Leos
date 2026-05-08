"""Policy and approval gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .enums import Decision, Permission, Reversibility, RiskLevel, _risk_value
from .plans import ActionStep
from .tools import Tool


@dataclass(frozen=True)
class PolicyProfile:
    name: str
    granted_permissions: Sequence[Permission] = ()
    max_auto_risk: RiskLevel = RiskLevel.MEDIUM
    require_human_for: Sequence[Permission] = ()
    deny_permissions: Sequence[Permission] = ()


BUILT_IN_POLICY_PROFILES = {
    "personal_safe": PolicyProfile(
        name="personal_safe",
        max_auto_risk=RiskLevel.LOW,
        require_human_for=(Permission.SEND_MESSAGE, Permission.FINANCIAL, Permission.DELETE, Permission.SYSTEM_CONFIG),
    ),
    "developer_local": PolicyProfile(
        name="developer_local",
        granted_permissions=(Permission.READ_FILES, Permission.WRITE_FILES, Permission.EXECUTE_CODE),
        max_auto_risk=RiskLevel.MEDIUM,
        deny_permissions=(Permission.NETWORK, Permission.FINANCIAL, Permission.DELETE, Permission.SYSTEM_CONFIG),
    ),
    "production": PolicyProfile(
        name="production",
        max_auto_risk=RiskLevel.LOW,
        require_human_for=(
            Permission.WRITE_FILES,
            Permission.SEND_MESSAGE,
            Permission.FINANCIAL,
            Permission.DELETE,
            Permission.EXECUTE_CODE,
            Permission.SYSTEM_CONFIG,
        ),
    ),
}


class PolicyEngine:
    """Capability and risk policy.

    The default rule is conservative:
    - LOW actions can run automatically.
    - MEDIUM actions require explicit permission grant.
    - HIGH/CRITICAL actions require human approval.
    - Consequential compensatable/irreversible actions require human approval.
    """

    def __init__(
        self,
        granted_permissions: Optional[Iterable[Permission]] = None,
        *,
        max_auto_risk: RiskLevel = RiskLevel.MEDIUM,
        require_human_for: Optional[Iterable[Permission]] = None,
        deny_permissions: Optional[Iterable[Permission]] = None,
        profile_name: str = "custom",
    ) -> None:
        self.granted_permissions = set(granted_permissions or [])
        self.max_auto_risk = max_auto_risk
        self.require_human_for = set(require_human_for or [])
        self.deny_permissions = set(deny_permissions or [])
        self.profile_name = profile_name

    @classmethod
    def from_profile(cls, profile: str | PolicyProfile) -> "PolicyEngine":
        if isinstance(profile, str):
            if profile not in BUILT_IN_POLICY_PROFILES:
                raise KeyError(f"Unknown policy profile: {profile}")
            profile = BUILT_IN_POLICY_PROFILES[profile]
        return cls(
            granted_permissions=profile.granted_permissions,
            max_auto_risk=profile.max_auto_risk,
            require_human_for=profile.require_human_for,
            deny_permissions=profile.deny_permissions,
            profile_name=profile.name,
        )

    def assess(self, tool: Tool, arguments: Mapping[str, Any]) -> RiskLevel:
        risk = tool.spec.default_risk
        if any(permission in tool.spec.permissions for permission in [Permission.DELETE, Permission.FINANCIAL, Permission.SYSTEM_CONFIG]):
            return RiskLevel.CRITICAL
        if arguments.get("destructive") is True:
            return RiskLevel.HIGH
        return risk

    def decide(self, step: ActionStep) -> Decision:
        required = set(step.required_permissions)
        if required & self.deny_permissions:
            return Decision.DENIED
        if required & self.require_human_for:
            return Decision.NEEDS_HUMAN
        missing = required - self.granted_permissions
        if missing:
            return Decision.NEEDS_HUMAN
        if _risk_value(step.risk) > _risk_value(self.max_auto_risk):
            return Decision.NEEDS_HUMAN
        consequential = bool(step.required_permissions) or _risk_value(step.risk) >= _risk_value(RiskLevel.MEDIUM)
        if consequential and step.reversibility in {Reversibility.COMPENSATABLE, Reversibility.IRREVERSIBLE}:
            return Decision.NEEDS_HUMAN
        return Decision.APPROVED


class ApprovalGate:
    """Human-in-the-loop gate for risky steps."""

    def __init__(self, approver: Optional[Callable[[ActionStep], bool]] = None) -> None:
        self.approver = approver

    def request(self, step: ActionStep) -> Decision:
        if not self.approver:
            return Decision.DENIED
        return Decision.APPROVED if self.approver(step) else Decision.DENIED
