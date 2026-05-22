"""Goal model."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .enums import GoalStatus, RiskLevel
from .errors import InvalidGoalTransition


@dataclass(frozen=True)
class ResourceBudget:
    """Execution limits for bounded autonomy."""

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_runtime_seconds: float | None = None
    max_tool_calls: int | None = None
    max_retries: int | None = None
    max_network_requests: int | None = None
    max_file_writes: int | None = None
    max_risk_level: RiskLevel = RiskLevel.CRITICAL

    def __post_init__(self) -> None:
        for name in (
            "max_tokens",
            "max_cost_usd",
            "max_runtime_seconds",
            "max_tool_calls",
            "max_retries",
            "max_network_requests",
            "max_file_writes",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        object.__setattr__(self, "max_risk_level", RiskLevel(self.max_risk_level))


@dataclass
class GoalProgress:
    total_steps: int = 0
    verified_steps: int = 0
    blocked_steps: int = 0
    failed_steps: int = 0
    rolled_back_steps: int = 0

    @property
    def completed_steps(self) -> int:
        return self.verified_steps

    @property
    def pending_steps(self) -> int:
        return self.total_steps - self.verified_steps - self.blocked_steps - self.failed_steps - self.rolled_back_steps

    @property
    def phase(self) -> str:
        if self.blocked_steps > 0:
            return "blocked"
        if self.failed_steps > 0 or self.rolled_back_steps > 0:
            return "failed"
        if self.verified_steps == self.total_steps and self.total_steps > 0:
            return "complete"
        if self.verified_steps > 0:
            return "partial"
        return "pending"


@dataclass
class RuntimeMetrics:
    """Runtime counters for dynamic budget enforcement (tokens, cost, time)."""

    tokens_used: int = 0
    cost_usd: float = 0.0
    elapsed_seconds: float = 0.0
    retries_used: int = 0


@dataclass(frozen=True)
class GoalCriterion:
    """Typed success criterion evaluated against observed world state."""

    key: str
    op: str
    value: Any = None
    source: str | None = None
    required: bool = True

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("GoalCriterion key must be non-empty")
        if self.op not in {
            "equals",
            "not_equals",
            "in",
            "not_in",
            "exists",
            "missing",
            "contains",
            "greater_than",
            "less_than",
        }:
            raise ValueError(f"Unsupported GoalCriterion op: {self.op}")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> GoalCriterion:
        return cls(
            key=str(data["key"]),
            op=str(data["op"]),
            value=data.get("value"),
            source=str(data["source"]) if data.get("source") is not None else None,
            required=bool(data.get("required", True)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "op": self.op,
            "value": self.value,
            "source": self.source,
            "required": self.required,
        }


@dataclass(frozen=True)
class Goal:
    """A user or system goal with explicit success and stop conditions."""

    description: str
    success_criteria: Sequence[str]
    criteria: Sequence[GoalCriterion | Mapping[str, Any]] = ()
    constraints: Sequence[str] = ()
    stop_conditions: Sequence[str] = ()
    priority: int = 5
    goal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    owner: str | None = None
    deadline: float | None = None
    budget: ResourceBudget = field(default_factory=ResourceBudget)
    status: GoalStatus = GoalStatus.CREATED

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", GoalStatus(self.status))
        object.__setattr__(
            self,
            "criteria",
            tuple(
                criterion if isinstance(criterion, GoalCriterion) else GoalCriterion.from_mapping(criterion)
                for criterion in self.criteria
            ),
        )

    def transition(self, status: GoalStatus) -> Goal:
        status = GoalStatus(status)
        if status not in _ALLOWED_GOAL_TRANSITIONS[self.status]:
            raise InvalidGoalTransition(f"Cannot transition goal from {self.status.value} to {status.value}")
        return replace(self, status=status)


_ALLOWED_GOAL_TRANSITIONS = {
    GoalStatus.CREATED: {
        GoalStatus.CLARIFYING,
        GoalStatus.PLANNING,
        GoalStatus.CANCELLED,
        GoalStatus.ARCHIVED,
    },
    GoalStatus.CLARIFYING: {
        GoalStatus.PLANNING,
        GoalStatus.BLOCKED,
        GoalStatus.CANCELLED,
    },
    GoalStatus.PLANNING: {
        GoalStatus.AWAITING_APPROVAL,
        GoalStatus.RUNNING,
        GoalStatus.BLOCKED,
        GoalStatus.CANCELLED,
    },
    GoalStatus.AWAITING_APPROVAL: {
        GoalStatus.RUNNING,
        GoalStatus.BLOCKED,
        GoalStatus.CANCELLED,
    },
    GoalStatus.RUNNING: {
        GoalStatus.PAUSED,
        GoalStatus.BLOCKED,
        GoalStatus.FAILED,
        GoalStatus.PARTIALLY_DONE,
        GoalStatus.SUCCEEDED,
        GoalStatus.CANCELLED,
    },
    GoalStatus.PAUSED: {
        GoalStatus.RUNNING,
        GoalStatus.BLOCKED,
        GoalStatus.CANCELLED,
    },
    GoalStatus.BLOCKED: {
        GoalStatus.PLANNING,
        GoalStatus.RUNNING,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
        GoalStatus.ARCHIVED,
    },
    GoalStatus.FAILED: {
        GoalStatus.PLANNING,
        GoalStatus.ARCHIVED,
    },
    GoalStatus.PARTIALLY_DONE: {
        GoalStatus.PLANNING,
        GoalStatus.RUNNING,
        GoalStatus.FAILED,
        GoalStatus.SUCCEEDED,
        GoalStatus.ARCHIVED,
    },
    GoalStatus.SUCCEEDED: {
        GoalStatus.ARCHIVED,
    },
    GoalStatus.CANCELLED: {
        GoalStatus.ARCHIVED,
    },
    GoalStatus.ARCHIVED: set(),
}
