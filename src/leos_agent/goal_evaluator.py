"""Deterministic goal success-criteria evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from .goals import Goal, GoalProgress
from .state import WorldState

if TYPE_CHECKING:
    from .evaluator_registry import EvaluatorRegistry


class GoalEvaluationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GoalEvaluation:
    status: GoalEvaluationStatus
    satisfied_criteria: list[str] = field(default_factory=list)
    unsatisfied_criteria: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""


class GoalEvaluator:
    """Deterministically evaluate whether explicit goal criteria are satisfied."""

    def __init__(self, registry: EvaluatorRegistry | None = None) -> None:
        if registry is None:
            from .evaluator_registry import EvaluatorRegistry

            registry = EvaluatorRegistry()
        self.registry = registry

    def evaluate(self, goal: Goal, state: WorldState, progress: GoalProgress | None = None) -> GoalEvaluation:
        return self.registry.evaluate(goal, state, progress)
