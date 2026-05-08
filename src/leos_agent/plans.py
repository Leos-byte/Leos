"""Plan and step data models."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .causal import ActionConsequence, CounterfactualReport
from .enums import CompensationStrategy, Permission, Reversibility, RiskLevel, StepStatus
from .goals import Goal


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
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class TransactionPlan:
    goal: Goal
    steps: List[ActionStep]
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)


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
