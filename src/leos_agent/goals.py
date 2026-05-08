"""Goal model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

from .enums import GoalStatus, RiskLevel
from .errors import InvalidGoalTransition


@dataclass(frozen=True)
class ResourceBudget:
    """Execution limits for bounded autonomy."""

    max_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    max_runtime_seconds: Optional[float] = None
    max_tool_calls: Optional[int] = None
    max_retries: Optional[int] = None
    max_network_requests: Optional[int] = None
    max_file_writes: Optional[int] = None
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


@dataclass(frozen=True)
class Goal:
    """A user or system goal with explicit success and stop conditions."""

    description: str
    success_criteria: Sequence[str]
    constraints: Sequence[str] = ()
    stop_conditions: Sequence[str] = ()
    priority: int = 5
    goal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    owner: Optional[str] = None
    deadline: Optional[float] = None
    budget: ResourceBudget = field(default_factory=ResourceBudget)
    status: GoalStatus = GoalStatus.CREATED

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", GoalStatus(self.status))

    def transition(self, status: GoalStatus) -> "Goal":
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
