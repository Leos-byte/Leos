"""Deterministic candidate planner."""

from __future__ import annotations

from typing import Optional, Sequence

from .audit import AuditLog
from .enums import _max_risk, _risk_value
from .goals import Goal
from .plans import ActionStep, PlanCandidate, PlanProposal, PlanScore, PlannerConfig, PlannerResult, TransactionPlan
from .policy import PolicyEngine
from .tools import ToolRegistry


class Planner:
    """Deterministic satisficing planner for explicit candidate proposals."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        config: Optional[PlannerConfig] = None,
        audit_log: Optional[AuditLog] = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.config = config or PlannerConfig()
        self.audit_log = audit_log

    def generate_candidates(self, goal: Goal, proposals: Sequence[PlanProposal]) -> list[PlanCandidate]:
        if not goal.success_criteria:
            raise ValueError("Goal must have explicit success criteria")
        candidates = [
            PlanCandidate(
                proposal=proposal,
                plan=TransactionPlan(goal=goal, steps=[self._clone_step(step) for step in proposal.steps]),
            )
            for proposal in proposals
        ]
        if self.audit_log:
            self.audit_log.record("planner.candidates_generated", "Generated plan candidates", goal_id=goal.goal_id, count=len(candidates))
        return candidates

    def score(self, candidate: PlanCandidate) -> PlanScore:
        risks = []
        for step in candidate.plan.steps:
            tool = self.registry.get(step.tool_name)
            risks.append(self.policy.assess(tool, step.arguments))
        risk = _max_risk(risks)
        risk_value = _risk_value(risk)
        estimated_cost = float(candidate.proposal.estimated_cost)
        expected_benefit = float(candidate.proposal.expected_benefit)
        utility = (
            expected_benefit * self.config.benefit_weight
            - estimated_cost * self.config.cost_weight
            - risk_value * self.config.risk_weight
        )
        satisfies = (
            risk_value <= _risk_value(self.config.max_risk)
            and estimated_cost <= self.config.max_cost
            and expected_benefit >= self.config.min_benefit
        )
        return PlanScore(
            risk=risk,
            risk_value=risk_value,
            estimated_cost=estimated_cost,
            expected_benefit=expected_benefit,
            utility=utility,
            satisfies=satisfies,
        )

    def select_satisfactory(self, candidates: Sequence[PlanCandidate]) -> Optional[PlanCandidate]:
        selected = None
        for candidate in candidates:
            candidate.score = candidate.score or self.score(candidate)
            if selected is None and candidate.score.satisfies:
                selected = candidate
        if self.audit_log:
            self.audit_log.record(
                "planner.selection_finished",
                "Selected satisfactory plan candidate" if selected else "No satisfactory plan candidate found",
                selected_proposal_id=selected.proposal.proposal_id if selected else None,
                candidate_count=len(candidates),
            )
        return selected

    def plan(self, goal: Goal, proposals: Sequence[PlanProposal]) -> PlannerResult:
        candidates = self.generate_candidates(goal, proposals)
        selected = self.select_satisfactory(candidates)
        return PlannerResult(goal=goal, candidates=candidates, selected=selected)

    @staticmethod
    def _clone_step(step: ActionStep) -> ActionStep:
        return ActionStep(
            tool_name=step.tool_name,
            arguments=dict(step.arguments),
            reason=step.reason,
            risk=step.risk,
        )
