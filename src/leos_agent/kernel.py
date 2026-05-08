"""Agent kernel orchestration."""

from __future__ import annotations

from typing import Optional, Sequence

from .audit import AuditLog
from .causal import CausalGraph, CounterfactualReview
from .goals import Goal
from .memory import MemoryStore
from .planner import Planner
from .plans import ActionStep, PlanProposal, PlannerConfig, PlannerResult, TransactionPlan
from .policy import ApprovalGate, PolicyEngine
from .state import WorldState
from .tools import ToolRegistry
from .transactions import TransactionManager


class AgentKernel:
    """The orchestration kernel for a Leos-style autonomous agent."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        causal_model: Optional[CausalGraph] = None,
        memory: Optional[MemoryStore] = None,
        audit_log: Optional[AuditLog] = None,
        approval_gate: Optional[ApprovalGate] = None,
        planner_config: Optional[PlannerConfig] = None,
        counterfactual_review: Optional[CounterfactualReview] = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.causal_model = causal_model or CausalGraph()
        self.memory = memory or MemoryStore()
        self.audit_log = audit_log or AuditLog()
        self.state = WorldState()
        self.planner = Planner(registry=registry, policy=policy, config=planner_config, audit_log=self.audit_log)
        self.transactions = TransactionManager(
            registry=registry,
            policy=policy,
            causal_model=self.causal_model,
            audit_log=self.audit_log,
            approval_gate=approval_gate,
            counterfactual_review=counterfactual_review,
        )

    def build_plan(self, goal: Goal, steps: Sequence[ActionStep]) -> TransactionPlan:
        if not goal.success_criteria:
            raise ValueError("Goal must have explicit success criteria")
        if not goal.stop_conditions:
            self.audit_log.record("goal.warning", "Goal has no stop conditions", goal_id=goal.goal_id)
        return TransactionPlan(goal=goal, steps=list(steps))

    def plan(self, goal: Goal, proposals: Sequence[PlanProposal]) -> PlannerResult:
        if not goal.stop_conditions:
            self.audit_log.record("goal.warning", "Goal has no stop conditions", goal_id=goal.goal_id)
        return self.planner.plan(goal, proposals)

    def run(self, plan: TransactionPlan) -> TransactionPlan:
        return self.transactions.execute_plan(plan, self.state)
