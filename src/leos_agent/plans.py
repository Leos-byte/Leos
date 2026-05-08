"""Plan and step data models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .causal import ActionConsequence, CounterfactualReport
from .enums import CompensationStrategy, Permission, Reversibility, RiskLevel, StepStatus
from .goals import Goal, ResourceBudget
from .state import TrustLevel


@dataclass(frozen=True)
class StateCondition:
    """A structured state condition used for auditable step gates."""

    variable: str
    operator: str = "exists"
    value: Any = None
    trust_level: Optional[TrustLevel] = None

    def __post_init__(self) -> None:
        if self.operator not in {"exists", "not_exists", "equals"}:
            raise ValueError(f"Unsupported condition operator: {self.operator}")
        if self.trust_level is not None:
            object.__setattr__(self, "trust_level", TrustLevel(self.trust_level))

    def describe(self) -> Dict[str, Any]:
        payload = {
            "variable": self.variable,
            "operator": self.operator,
        }
        if self.operator == "equals":
            payload["value"] = self.value
        if self.trust_level is not None:
            payload["trust_level"] = self.trust_level.value
        return payload


@dataclass
class ActionStep:
    tool_name: str
    arguments: Dict[str, Any]
    reason: str
    status: StepStatus = StepStatus.PENDING
    risk: RiskLevel = RiskLevel.LOW
    reversibility: Reversibility = Reversibility.IRREVERSIBLE
    compensation_strategy: CompensationStrategy = CompensationStrategy.NONE
    rollback_reliability: float = 0.0
    required_permissions: Sequence[Permission] = ()
    predictions: List[ActionConsequence] = field(default_factory=list)
    counterfactual_report: Optional[CounterfactualReport] = None
    idempotency_key: Optional[str] = None
    preconditions: Sequence[StateCondition] = ()
    postconditions: Sequence[StateCondition] = ()
    invariants: Sequence[StateCondition] = ()
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class TransactionPlan:
    goal: Goal
    steps: List[ActionStep]
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    budget: Optional[ResourceBudget] = None


@dataclass(frozen=True)
class PlanProposal:
    """A candidate way to satisfy a goal before scoring."""

    steps: Sequence[ActionStep]
    rationale: str
    estimated_cost: float = 0.0
    expected_benefit: float = 1.0
    proposal_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class PlanScore:
    """Risk/cost/benefit score for one candidate plan."""

    risk: RiskLevel
    risk_value: int
    estimated_cost: float
    expected_benefit: float
    utility: float
    satisfies: bool


@dataclass
class PlanCandidate:
    proposal: PlanProposal
    plan: TransactionPlan
    score: Optional[PlanScore] = None


@dataclass
class PlannerResult:
    goal: Goal
    candidates: List[PlanCandidate]
    selected: Optional[PlanCandidate]


@dataclass(frozen=True)
class PlannerConfig:
    max_risk: RiskLevel = RiskLevel.MEDIUM
    max_cost: float = float("inf")
    min_benefit: float = 0.0
    benefit_weight: float = 1.0
    cost_weight: float = 1.0
    risk_weight: float = 0.25
