"""Transactional plan execution."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .audit import AuditLog
from .causal import CausalGraph, CounterfactualReview
from .enums import Decision, Reversibility, StepStatus
from .errors import DryRunFailed, LeosError, PolicyDenied, RollbackFailed
from .plans import ActionStep, TransactionPlan
from .policy import ApprovalGate, PolicyEngine
from .state import TrustLevel, WorldState
from .tools import Tool, ToolRegistry, ToolResult


def _error_type(error: Optional[LeosError]) -> Optional[str]:
    return type(error).__name__ if error else None


class TransactionManager:
    """Executes plan steps as reversible transactions where possible."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        causal_model: CausalGraph,
        audit_log: AuditLog,
        approval_gate: Optional[ApprovalGate] = None,
        counterfactual_review: Optional[CounterfactualReview] = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.causal_model = causal_model
        self.audit_log = audit_log
        self.approval_gate = approval_gate or ApprovalGate()
        self.counterfactual_review = counterfactual_review or CounterfactualReview(causal_model, audit_log)

    def execute_plan(self, plan: TransactionPlan, state: WorldState) -> TransactionPlan:
        self.audit_log.record("plan.started", "Starting transaction plan", plan_id=plan.plan_id, goal=plan.goal.description)
        rollback_stack: List[tuple[Tool, Dict[str, Any], ActionStep]] = []

        for step in plan.steps:
            tool = self.registry.get(step.tool_name)
            step.required_permissions = tuple(tool.spec.permissions)
            step.risk = self.policy.assess(tool, step.arguments)
            step.reversibility = tool.spec.reversibility or Reversibility.IRREVERSIBLE
            step.compensation_strategy = tool.spec.compensation_strategy
            step.rollback_reliability = tool.spec.rollback_reliability
            step.predictions = self.causal_model.predict(step, state)
            step.counterfactual_report = self.counterfactual_review.review(step, state, step.predictions)

            decision = self.policy.decide(step)
            if decision is Decision.NEEDS_HUMAN:
                decision = self.approval_gate.request(step)
            if decision is not Decision.APPROVED:
                step.status = StepStatus.BLOCKED
                error = PolicyDenied(f"Step blocked by policy: {decision.value}")
                self.audit_log.record(
                    "step.blocked",
                    "Step blocked by policy",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    decision=decision.value,
                    reversibility=step.reversibility.value,
                    compensation_strategy=step.compensation_strategy.value,
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state)
                break

            dry_run = tool.dry_run(step.arguments, state)
            if not dry_run.ok:
                step.status = StepStatus.FAILED
                error = dry_run.error or DryRunFailed(dry_run.message)
                self.audit_log.record(
                    "step.dry_run_failed",
                    dry_run.message,
                    step_id=step.step_id,
                    data=dry_run.data,
                    error_type=_error_type(error),
                )
                self._rollback(rollback_stack, state)
                break
            step.status = StepStatus.DRY_RUN_OK
            self.audit_log.record("step.dry_run_ok", dry_run.message, step_id=step.step_id, tool=step.tool_name)

            result = tool.execute(step.arguments, state)
            if not result.ok:
                step.status = StepStatus.FAILED
                self.audit_log.record(
                    "step.execution_failed",
                    result.message,
                    step_id=step.step_id,
                    data=result.data,
                    error_type=_error_type(result.error),
                )
                self._rollback(rollback_stack, state)
                break

            step.status = StepStatus.EXECUTED
            if result.rollback_token:
                rollback_stack.append((tool, dict(result.rollback_token), step))
            state.observe(result.observed_state_delta, trust_level=TrustLevel.TOOL_REPORTED)
            self.audit_log.record(
                "step.executed",
                result.message,
                step_id=step.step_id,
                observed=result.observed_state_delta,
                observed_trust=TrustLevel.TOOL_REPORTED.value,
            )

            verification = self.causal_model.verify(step.predictions, result)
            if not verification.ok:
                step.status = StepStatus.FAILED
                self.audit_log.record(
                    "step.verification_failed",
                    verification.message,
                    step_id=step.step_id,
                    data=verification.data,
                    error_type=_error_type(verification.error),
                )
                self._rollback(rollback_stack, state)
                break
            step.status = StepStatus.VERIFIED
            state.mark_trust(result.observed_state_delta.keys(), TrustLevel.VERIFIED)
            self.audit_log.record(
                "step.verified",
                verification.message,
                step_id=step.step_id,
                verified=list(result.observed_state_delta),
                verified_trust=TrustLevel.VERIFIED.value,
            )

        self.audit_log.record("plan.finished", "Finished transaction plan", plan_id=plan.plan_id)
        return plan

    def _rollback(self, rollback_stack: List[tuple[Tool, Dict[str, Any], ActionStep]], state: WorldState) -> None:
        rollback_succeeded = 0
        rollback_failed = 0
        while rollback_stack:
            tool, token, step = rollback_stack.pop()
            self.audit_log.record("rollback_attempted", "Attempting rollback", step_id=step.step_id, tool=tool.spec.name)
            try:
                result = tool.rollback(token, state)
            except Exception as exc:  # noqa: BLE001 - rollback failures must become audit events
                result = ToolResult(False, f"Rollback raised: {exc}", error=RollbackFailed(str(exc)))
            step.status = StepStatus.ROLLED_BACK if result.ok else StepStatus.FAILED
            self.audit_log.record("step.rollback", result.message, step_id=step.step_id, ok=result.ok)
            if result.ok:
                rollback_succeeded += 1
                self.audit_log.record("rollback_succeeded", result.message, step_id=step.step_id, tool=tool.spec.name)
                continue

            rollback_failed += 1
            error = result.error or RollbackFailed(result.message)
            self.audit_log.record(
                "rollback_failed",
                result.message,
                step_id=step.step_id,
                tool=tool.spec.name,
                error_type=_error_type(error),
            )
            self.audit_log.record(
                "manual_recovery_required",
                "Rollback failed; manual recovery is required",
                step_id=step.step_id,
                tool=tool.spec.name,
                rollback_token=token,
                error_type=_error_type(error),
            )
        if rollback_failed and rollback_succeeded:
            self.audit_log.record(
                "rollback_partially_completed",
                "Some rollback steps succeeded and some failed",
                succeeded=rollback_succeeded,
                failed=rollback_failed,
            )
