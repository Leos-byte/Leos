"""Policy and approval gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from .enums import Decision, Permission, Reversibility, RiskLevel, _risk_value
from .errors import PolicyConfigurationError
from .plans import ActionStep
from .tools import Tool


def _permissions(values: Iterable[Permission | str]) -> tuple[Permission, ...]:
    return tuple(Permission(value) for value in values)


@dataclass(frozen=True)
class PolicyRule:
    name: str
    when: Mapping[str, Any]
    decision: Decision

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", Decision(self.decision))
        if self.decision is Decision.APPROVED:
            raise PolicyConfigurationError("Policy-as-code rules cannot directly approve actions")
        if not self.when:
            raise PolicyConfigurationError(f"Policy rule {self.name} must define at least one condition")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PolicyRule":
        if "name" not in data:
            raise PolicyConfigurationError("Policy rule is missing name")
        if "when" not in data:
            raise PolicyConfigurationError(f"Policy rule {data['name']} is missing when")
        if "decision" not in data:
            raise PolicyConfigurationError(f"Policy rule {data['name']} is missing decision")
        return cls(name=str(data["name"]), when=dict(data["when"]), decision=Decision(str(data["decision"])))

    def matches(self, step: ActionStep, *, profile_name: str) -> bool:
        for key, expected in self.when.items():
            if key == "profile":
                if profile_name != str(expected):
                    return False
                continue
            if key == "tool":
                if step.tool_name != str(expected):
                    return False
                continue
            if key == "permission":
                permissions = {Permission(value) for value in _as_list(expected)}
                if not permissions.intersection(set(step.required_permissions)):
                    return False
                continue
            if key == "risk_at_least":
                if _risk_value(step.risk) < _risk_value(RiskLevel(str(expected))):
                    return False
                continue
            if key == "reversibility":
                if step.reversibility is not Reversibility(str(expected)):
                    return False
                continue
            raise PolicyConfigurationError(f"Unsupported policy rule condition: {key}")
        return True


@dataclass(frozen=True)
class PolicyProfile:
    name: str
    granted_permissions: Sequence[Permission] = ()
    max_auto_risk: RiskLevel = RiskLevel.MEDIUM
    require_human_for: Sequence[Permission] = ()
    deny_permissions: Sequence[Permission] = ()
    rules: Sequence[PolicyRule] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "granted_permissions", _permissions(self.granted_permissions))
        object.__setattr__(self, "max_auto_risk", RiskLevel(self.max_auto_risk))
        object.__setattr__(self, "require_human_for", _permissions(self.require_human_for))
        object.__setattr__(self, "deny_permissions", _permissions(self.deny_permissions))
        object.__setattr__(
            self,
            "rules",
            tuple(rule if isinstance(rule, PolicyRule) else PolicyRule.from_mapping(rule) for rule in self.rules),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PolicyProfile":
        if "name" not in data:
            raise PolicyConfigurationError("Policy profile is missing name")
        return cls(
            name=str(data["name"]),
            granted_permissions=tuple(data.get("granted_permissions", ())),
            max_auto_risk=RiskLevel(str(data.get("max_auto_risk", RiskLevel.MEDIUM.value))),
            require_human_for=tuple(data.get("require_human_for", ())),
            deny_permissions=tuple(data.get("deny_permissions", ())),
            rules=tuple(PolicyRule.from_mapping(rule) for rule in data.get("rules", ())),
        )


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
        rules: Optional[Iterable[PolicyRule]] = None,
        profile_name: str = "custom",
    ) -> None:
        self.granted_permissions = set(granted_permissions or [])
        self.max_auto_risk = max_auto_risk
        self.require_human_for = set(require_human_for or [])
        self.deny_permissions = set(deny_permissions or [])
        self.rules = tuple(rules or ())
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
            rules=profile.rules,
            profile_name=profile.name,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PolicyEngine":
        return cls.from_profile(PolicyProfile.from_mapping(data))

    def assess(self, tool: Tool, arguments: Mapping[str, Any]) -> RiskLevel:
        risk = tool.spec.default_risk
        if any(permission in tool.spec.permissions for permission in [Permission.DELETE, Permission.FINANCIAL, Permission.SYSTEM_CONFIG]):
            return RiskLevel.CRITICAL
        if arguments.get("destructive") is True:
            return RiskLevel.HIGH
        return risk

    def decide(self, step: ActionStep) -> Decision:
        configured_decision = self._decide_by_rules(step)
        if configured_decision is not None:
            return configured_decision
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

    def _decide_by_rules(self, step: ActionStep) -> Optional[Decision]:
        for rule in self.rules:
            if rule.matches(step, profile_name=self.profile_name):
                return rule.decision
        return None


class ApprovalGate:
    """Human-in-the-loop gate for risky steps."""

    def __init__(self, approver: Optional[Callable[[ActionStep], bool]] = None) -> None:
        self.approver = approver

    def request(self, step: ActionStep) -> Decision:
        if not self.approver:
            return Decision.DENIED
        return Decision.APPROVED if self.approver(step) else Decision.DENIED


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def validate_policy_config(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues = []
    try:
        PolicyProfile.from_mapping(data)
    except Exception as exc:  # noqa: BLE001 - validation should return structured issues
        issues.append({"reason": "policy_config_invalid", "message": str(exc)})
    return issues
