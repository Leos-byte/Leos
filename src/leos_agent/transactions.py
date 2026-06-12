"""Transactional plan execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from .approval import build_approval_packet, build_step_hash, validate_approval_decision
from .audit import AuditLog
from .causal import CausalGraph, CounterfactualReview
from .enums import (
    Decision,
    GoalStatus,
    Permission,
    Reversibility,
    RiskLevel,
    SandboxPolicy,
    StepStatus,
    _risk_value,
)
from .errors import (
    BudgetExceeded,
    DryRunFailed,
    IdempotencyConflict,
    LeosError,
    PolicyDenied,
    PostconditionFailed,
    PreconditionFailed,
    RollbackFailed,
    SandboxViolation,
    SchemaValidationFailed,
    SecretLeakedToUntrustedTool,
)
from .goals import GoalProgress, ResourceBudget
from .plans import ActionStep, StateCondition, TransactionPlan
from .policy import PRODUCTION_PROFILE_NAMES, ApprovalGate, PolicyEngine
from .recovery import ManualRecoveryPacket
from .sandbox import SandboxRunner
from .state import TrustLevel, WorldState
from .tools import (
    Secret,
    Tool,
    ToolRegistry,
    ToolResult,
    _contains_secrets,
    _redact_secrets,
)


def _error_type(error: LeosError | None) -> str | None:
    return type(error).__name__ if error else None


def _affected_resources(step: ActionStep) -> list[str]:
    resources = []
    for key in ("repo", "path", "branch", "issue_number", "pr_number"):
        value = step.arguments.get(key)
        if value is not None:
            resources.append(f"{key}:{value}")
    return resources


class TransactionManager:
    """Executes plan steps as reversible transactions where possible."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        causal_model: CausalGraph,
        audit_log: AuditLog,
        approval_gate: ApprovalGate | None = None,
        counterfactual_review: CounterfactualReview | None = None,
        allow_network_tools: bool = False,
        sandbox_runners: Mapping[SandboxPolicy, SandboxRunner] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.causal_model = causal_model
        self.audit_log = audit_log
        self.approval_gate = approval_gate or ApprovalGate()
        self.counterfactual_review = counterfactual_review or CounterfactualReview(causal_model, audit_log)
        self.allow_network_tools = allow_network_tools
        self.sandbox_runners = dict(sandbox_runners or {})

    def execute_plan(self, plan: TransactionPlan, state: WorldState) -> TransactionPlan:
        self.audit_log.record(
            "plan.started", "Starting transaction plan", plan_id=plan.plan_id, goal=plan.goal.description
        )
        self._transition_plan_goal(plan, GoalStatus.RUNNING)
        rollback_stack: list[tuple[Tool, dict[str, Any], ActionStep]] = []
        budget = plan.budget or plan.goal.budget
        if self._budget_exceeded(plan, budget):
            self._transition_plan_goal(plan, self._final_goal_status(plan))
            self.audit_log.record(
                "plan.finished",
                "Finished transaction plan",
                plan_id=plan.plan_id,
                goal_status=plan.goal.status.value,
            )
            return plan

        for step in plan.steps:
            try:
                tool = self.registry.get(step.tool_name)
            except KeyError as exc:
                if self.policy.profile_name not in PRODUCTION_PROFILE_NAMES:
                    raise
                step.status = StepStatus.BLOCKED
                error: LeosError = PolicyDenied(f"Unknown tool: {step.tool_name}")
                self.audit_log.record(
                    "step.blocked",
                    "Step blocked: unknown tool",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    decision="denied",
                    reason=str(exc),
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break
            self._hydrate_step_metadata(step, tool)

            egress_assessment = self.policy.production_egress_assessment(step, tool)
            if egress_assessment is not None:
                self.audit_log.record(
                    "egress.allowed" if egress_assessment.allowed else "egress.blocked",
                    "Production egress policy assessed tool network access",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    host=egress_assessment.host,
                    forward_methods=list(egress_assessment.forward_methods),
                    rollback_methods=list(egress_assessment.rollback_methods),
                    profile=self.policy.profile_name,
                    policy_name=self.policy.profile_name,
                    network_access=tool.spec.network_access,
                    reason=egress_assessment.reason,
                )

            attestation_issue = self._runtime_attestation_block_reason(step, tool)
            if attestation_issue:
                step.status = StepStatus.BLOCKED
                error = PolicyDenied(attestation_issue)
                self.audit_log.record(
                    "step.blocked",
                    "Step blocked by runtime attestation policy",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    decision="denied",
                    risk=step.risk.value,
                    profile=self.policy.profile_name,
                    reason=attestation_issue,
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            production_issue = self.policy.production_block_reason(step, tool)
            if production_issue:
                step.status = StepStatus.BLOCKED
                error = PolicyDenied(production_issue)
                event_type = "causal_contract.missing" if "causal contract" in production_issue else "policy.blocked"
                self.audit_log.record(
                    event_type,
                    "Step blocked by production policy",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    decision="denied",
                    risk=step.risk.value,
                    profile=self.policy.profile_name,
                    reason=production_issue,
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            sandbox_issue = self._enforce_sandbox(tool)
            if sandbox_issue:
                step.status = StepStatus.BLOCKED
                error = SandboxViolation(sandbox_issue)
                self.audit_log.record(
                    "step.blocked",
                    "Step blocked by sandbox policy",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    decision="denied",
                    error_type=type(error).__name__,
                    reason=sandbox_issue,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            if _contains_secrets(step.arguments) and not tool.spec.secrets_allowed:
                step.status = StepStatus.BLOCKED
                error = SecretLeakedToUntrustedTool(f"Tool '{step.tool_name}' does not allow secrets")
                self.audit_log.record(
                    "step.blocked",
                    "Step blocked: secrets not allowed",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    decision="denied",
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            contract = getattr(tool.spec, "causal_contract", None)
            if contract is not None:
                self.audit_log.record(
                    "step.causal_contract_used",
                    "Using tool causal contract",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    required_observations=list(getattr(contract, "required_observations", ())),
                )
            elif _risk_value(step.risk) >= _risk_value(RiskLevel.MEDIUM):
                self.audit_log.record(
                    "step.causal_contract_missing_warning",
                    "Medium or higher risk tool has no causal contract",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    risk=step.risk.value,
                )
            step.predictions = self.causal_model.predict_for_tool(step, state, tool=tool)
            step.counterfactual_report = self.counterfactual_review.review(step, state, step.predictions)

            if self._idempotency_conflict(step, state):
                self._rollback(rollback_stack, state, plan=plan)
                break

            precondition_issues = self._check_conditions((*step.preconditions, *step.invariants), state)
            if precondition_issues:
                step.status = StepStatus.BLOCKED
                error = PreconditionFailed("Step preconditions failed")
                self.audit_log.record(
                    "step.precondition_failed",
                    "Step preconditions failed",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    issues=precondition_issues,
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            decision_result = self.policy.decide(step)
            decision = decision_result.decision
            preserve_secret_keys = ("token",) if tool.spec.name.startswith("github_") else ()
            prepared_args = self._prepare_arguments(
                step.arguments,
                tool.spec.secrets_allowed,
                preserve_secret_keys=preserve_secret_keys,
            )
            dry_run: ToolResult | None = None
            dry_run_recorded = False
            if decision is Decision.NEEDS_HUMAN:
                dry_run = tool.dry_run(prepared_args, state)
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
                    self._rollback(rollback_stack, state, plan=plan)
                    break
                step.status = StepStatus.DRY_RUN_OK
                self.audit_log.record("step.dry_run_ok", dry_run.message, step_id=step.step_id, tool=step.tool_name)
                dry_run_recorded = True
                if self.policy.require_signed_approval and not bool(
                    getattr(self.approval_gate, "signed_approval_enforced", False)
                ):
                    step.status = StepStatus.BLOCKED
                    signature_issue = f"{self.policy.profile_name} requires signed approval decisions"
                    error = PolicyDenied(signature_issue)
                    self.audit_log.record(
                        "approval.signature_required",
                        "Signed approval gate is required by production policy",
                        step_id=step.step_id,
                        tool=step.tool_name,
                        profile=self.policy.profile_name,
                        reason=signature_issue,
                        error_type=type(error).__name__,
                    )
                    self.audit_log.record(
                        "step.blocked",
                        "Step blocked by policy",
                        step_id=step.step_id,
                        tool=step.tool_name,
                        decision="denied",
                        reason=signature_issue,
                        rule_name=decision_result.rule_name,
                        reversibility=step.reversibility.value,
                        compensation_strategy=step.compensation_strategy.value,
                        error_type=type(error).__name__,
                    )
                    self._rollback(rollback_stack, state, plan=plan)
                    break
                packet = build_approval_packet(
                    plan=plan,
                    step=step,
                    tool=tool,
                    dry_run_summary=dry_run.message,
                    profile=self.policy.profile_name,
                    requester=self.policy.principal,
                )
                try:
                    packet = self.approval_gate.prepare_packet(packet, step)
                except Exception as exc:
                    step.status = StepStatus.BLOCKED
                    reason = f"Approval packet preparation failed: {type(exc).__name__}"
                    self.audit_log.record(
                        "step.blocked",
                        "Step blocked by approval packet validation",
                        step_id=step.step_id,
                        tool=step.tool_name,
                        decision="denied",
                        reason=reason,
                        rule_name=decision_result.rule_name,
                        reversibility=step.reversibility.value,
                        compensation_strategy=step.compensation_strategy.value,
                        error_type=type(exc).__name__,
                    )
                    self.audit_log.record(
                        "approval.rejected",
                        "Approval packet rejected before execution",
                        step_id=step.step_id,
                        tool=step.tool_name,
                        reason=reason,
                        error_type=type(exc).__name__,
                    )
                    self._rollback(rollback_stack, state, plan=plan)
                    break
                self.audit_log.record(
                    "approval.packet_created",
                    "Approval packet created",
                    **packet.as_dict(),
                )
                approval_decision = self.approval_gate.request_packet(packet, step)
                self.audit_log.record(
                    "approval.decision_recorded",
                    "Approval decision recorded",
                    **approval_decision.as_dict(),
                )
                current_hash = build_step_hash(goal_id=plan.goal.goal_id, plan_id=plan.plan_id, step=step, tool=tool)
                approval_issue = validate_approval_decision(
                    packet,
                    approval_decision,
                    current_step_hash=current_hash,
                    profile=self.policy.profile_name,
                )
                if approval_issue:
                    step.status = StepStatus.BLOCKED
                    error = PolicyDenied(f"Step approval rejected: {approval_issue}")
                    if approval_decision.decision.value == "dry_run_only":
                        self.audit_log.record(
                            "approval.dry_run_only",
                            "Approval limited the step to dry-run only",
                            step_id=step.step_id,
                            tool=step.tool_name,
                            approval_id=packet.approval_id,
                            reason=approval_issue,
                        )
                    elif approval_decision.decision.value == "narrow_scope":
                        self.audit_log.record(
                            "approval.narrow_scope_requested",
                            "Approval requested a narrower action scope",
                            step_id=step.step_id,
                            tool=step.tool_name,
                            approval_id=packet.approval_id,
                            reason=approval_issue,
                        )
                    self.audit_log.record(
                        "step.blocked",
                        "Step blocked by policy",
                        step_id=step.step_id,
                        tool=step.tool_name,
                        decision="denied",
                        reason=approval_issue,
                        rule_name=decision_result.rule_name,
                        reversibility=step.reversibility.value,
                        compensation_strategy=step.compensation_strategy.value,
                        error_type=type(error).__name__,
                    )
                    self.audit_log.record(
                        "approval.rejected",
                        "Approval rejected before execution",
                        step_id=step.step_id,
                        tool=step.tool_name,
                        approval_id=packet.approval_id,
                        reason=approval_issue,
                        error_type=type(error).__name__,
                    )
                    self._rollback(rollback_stack, state, plan=plan)
                    break
                consumption_issue = self.approval_gate.consume_approval(packet, approval_decision, step)
                if consumption_issue:
                    step.status = StepStatus.BLOCKED
                    error = PolicyDenied(f"Step approval rejected: {consumption_issue}")
                    self.audit_log.record(
                        "step.blocked",
                        "Step blocked by approval replay protection",
                        step_id=step.step_id,
                        tool=step.tool_name,
                        decision="denied",
                        reason=consumption_issue,
                        rule_name=decision_result.rule_name,
                        reversibility=step.reversibility.value,
                        compensation_strategy=step.compensation_strategy.value,
                        error_type=type(error).__name__,
                    )
                    self.audit_log.record(
                        "approval.rejected",
                        "Approval rejected before execution",
                        step_id=step.step_id,
                        tool=step.tool_name,
                        approval_id=packet.approval_id,
                        reason=consumption_issue,
                        error_type=type(error).__name__,
                    )
                    self._rollback(rollback_stack, state, plan=plan)
                    break
                self.audit_log.record(
                    "approval.used",
                    "Approval used for step execution",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    approval_id=packet.approval_id,
                    approval_decision=approval_decision.decision.value,
                    approval_approver=approval_decision.approver,
                    signed_approval_required=self.policy.require_signed_approval,
                    signed_approval_enforced=bool(getattr(self.approval_gate, "signed_approval_enforced", False)),
                    approval_signature_verified=bool(
                        getattr(self.approval_gate, "last_decision_signature_valid", False)
                    ),
                    approval_signature_algorithm=getattr(
                        self.approval_gate,
                        "last_decision_signature_algorithm",
                        None,
                    ),
                )
                decision = Decision.APPROVED
            if decision is not Decision.APPROVED:
                step.status = StepStatus.BLOCKED
                error = PolicyDenied(f"Step blocked by policy: {decision.value}")
                self.audit_log.record(
                    "step.blocked",
                    "Step blocked by policy",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    decision=decision.value,
                    reason=decision_result.reason,
                    rule_name=decision_result.rule_name,
                    reversibility=step.reversibility.value,
                    compensation_strategy=step.compensation_strategy.value,
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            if dry_run is None:
                dry_run = tool.dry_run(prepared_args, state)
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
                self._rollback(rollback_stack, state, plan=plan)
                break
            step.status = StepStatus.DRY_RUN_OK
            if not dry_run_recorded:
                self.audit_log.record("step.dry_run_ok", dry_run.message, step_id=step.step_id, tool=step.tool_name)

            result = tool.execute(prepared_args, state)
            if not result.ok:
                step.status = StepStatus.FAILED
                self.audit_log.record(
                    "step.execution_failed",
                    result.message,
                    step_id=step.step_id,
                    data=result.data,
                    error_type=_error_type(result.error),
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            step.status = StepStatus.EXECUTED
            if result.rollback_token:
                rollback_stack.append((tool, dict(result.rollback_token), step))

            output_schema_issues = tool.spec.validate_output(result.observed_state_delta)
            if output_schema_issues:
                step.status = StepStatus.FAILED
                error = SchemaValidationFailed("Output schema validation failed")
                self.audit_log.record(
                    "step.output_schema_failed",
                    "Output schema validation failed",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    data={"schema_issues": output_schema_issues},
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            contract_missing = self._missing_contract_observations(tool, result)
            contract_field_violations = self._contract_field_violations(tool, result, step)
            if contract_missing or contract_field_violations:
                step.status = StepStatus.FAILED
                error = SchemaValidationFailed("Causal contract required observations missing")
                self.audit_log.record(
                    "step.causal_contract_verification_failed",
                    "Causal contract verification failed",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    missing=contract_missing,
                    missing_observations=contract_missing,
                    field_violations=contract_field_violations,
                    observed=list(result.observed_state_delta),
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break
            if contract is not None:
                self.audit_log.record(
                    "step.causal_contract_verified",
                    "Causal contract required observations verified",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    required_observations=list(getattr(contract, "required_observations", ())),
                )

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
                self._rollback(rollback_stack, state, plan=plan)
                break

            postcondition_issues = self._check_conditions((*step.postconditions, *step.invariants), state)
            if postcondition_issues:
                step.status = StepStatus.FAILED
                error = PostconditionFailed("Step postconditions failed")
                self.audit_log.record(
                    "step.postcondition_failed",
                    "Step postconditions failed",
                    step_id=step.step_id,
                    tool=step.tool_name,
                    issues=postcondition_issues,
                    error_type=type(error).__name__,
                )
                self._rollback(rollback_stack, state, plan=plan)
                break

            state.mark_trust(result.observed_state_delta.keys(), TrustLevel.VERIFIED)
            self._record_grant_use(step)
            self.audit_log.record(
                "state.trust_escalated",
                "Trust escalated for observed state keys",
                step_id=step.step_id,
                keys=list(result.observed_state_delta),
                from_trust=TrustLevel.TOOL_REPORTED.value,
                to_trust=TrustLevel.VERIFIED.value,
            )
            if step.idempotency_key:
                self._record_idempotency_key(step, state)
            step.status = StepStatus.VERIFIED
            self.audit_log.record(
                "step.verified",
                verification.message,
                step_id=step.step_id,
                verified=list(result.observed_state_delta),
                verified_trust=TrustLevel.VERIFIED.value,
            )

        self._transition_plan_goal(plan, self._final_goal_status(plan))
        self.audit_log.record(
            "plan.finished",
            "Finished transaction plan",
            plan_id=plan.plan_id,
            goal_status=plan.goal.status.value,
        )
        return plan

    def _transition_plan_goal(self, plan: TransactionPlan, status: GoalStatus) -> None:
        if plan.goal.status is status:
            return
        if plan.goal.status is GoalStatus.CREATED and status is GoalStatus.RUNNING:
            self._transition_plan_goal(plan, GoalStatus.PLANNING)
        previous = plan.goal.status
        plan.goal = plan.goal.transition(status)
        self.audit_log.record(
            "goal.status_changed",
            "Goal status changed",
            goal_id=plan.goal.goal_id,
            from_status=previous.value,
            to_status=plan.goal.status.value,
        )

    @staticmethod
    def _final_goal_status(plan: TransactionPlan) -> GoalStatus:
        if not plan.steps:
            return GoalStatus.PARTIALLY_DONE
        verified = sum(1 for step in plan.steps if step.status is StepStatus.VERIFIED)
        if verified == len(plan.steps):
            return GoalStatus.PARTIALLY_DONE
        if verified:
            return GoalStatus.PARTIALLY_DONE
        if any(step.status is StepStatus.BLOCKED for step in plan.steps):
            return GoalStatus.BLOCKED
        if any(step.status in {StepStatus.FAILED, StepStatus.ROLLED_BACK} for step in plan.steps):
            return GoalStatus.FAILED
        return GoalStatus.FAILED

    def _enforce_sandbox(self, tool: Tool | None = None) -> str | None:
        target_tool: Any
        if tool is None:
            target_tool = self  # backward-compatible class-style call: TransactionManager._enforce_sandbox(tool)
            sandbox_runners: Mapping[SandboxPolicy, SandboxRunner] = {}
            allow_network_tools = False
        else:
            target_tool = tool
            sandbox_runners = self.sandbox_runners
            allow_network_tools = self.allow_network_tools
        policy = target_tool.spec.sandbox_policy
        if policy is SandboxPolicy.CONTAINER and policy not in sandbox_runners:
            return "container sandbox not available — requires external container runtime"
        if policy is SandboxPolicy.MICROVM and policy not in sandbox_runners:
            return "microvm sandbox not available — requires external microvm runtime"
        if target_tool.spec.network_access and not allow_network_tools:
            if (
                tool is not None
                and self.policy.profile_name in PRODUCTION_PROFILE_NAMES
                and self.policy.egress_policy is not None
            ):
                return None
            return "network access not allowed in default sandbox"
        if policy is SandboxPolicy.WORKSPACE and target_tool.spec.filesystem_scope == "none":
            return "workspace sandbox requires filesystem_scope"
        return None

    @staticmethod
    def _prepare_arguments(
        arguments: dict[str, Any],
        secrets_allowed: bool,
        *,
        preserve_secret_keys: Sequence[str] = (),
    ) -> dict[str, Any]:
        if secrets_allowed:
            preserve = set(preserve_secret_keys)
            return {
                k: v if k in preserve and isinstance(v, Secret) else v.unwrap() if isinstance(v, Secret) else v
                for k, v in arguments.items()
            }
        return cast(dict[str, Any], _redact_secrets(arguments))

    def track_progress(self, plan: TransactionPlan) -> GoalProgress:
        progress = GoalProgress(total_steps=len(plan.steps))
        for step in plan.steps:
            if step.status is StepStatus.VERIFIED:
                progress.verified_steps += 1
            elif step.status is StepStatus.BLOCKED:
                progress.blocked_steps += 1
            elif step.status in {StepStatus.FAILED, StepStatus.ROLLED_BACK}:
                if step.status is StepStatus.ROLLED_BACK:
                    progress.rolled_back_steps += 1
                else:
                    progress.failed_steps += 1
        return progress

    def _record_grant_use(self, step: ActionStep) -> None:
        grant = self.policy._matching_grant(step.tool_name)
        if grant is not None:
            grant.record_use()

    @staticmethod
    def _missing_contract_observations(tool: Tool, result: ToolResult) -> list[str]:
        contract = getattr(tool.spec, "causal_contract", None)
        if contract is None:
            return []
        return list(contract.missing_required_observations(result.observed_state_delta))

    @staticmethod
    def _contract_field_violations(tool: Tool, result: ToolResult, step: ActionStep) -> list[str]:
        contract = getattr(tool.spec, "causal_contract", None)
        if contract is None or not hasattr(contract, "field_violations"):
            return []
        return list(contract.field_violations(result.observed_state_delta, step=step))

    def _hydrate_step_metadata(self, step: ActionStep, tool: Tool) -> None:
        step.required_permissions = tuple(tool.spec.permissions)
        step.risk = self.policy.assess(tool, step.arguments)
        step.reversibility = tool.spec.reversibility or Reversibility.IRREVERSIBLE
        step.compensation_strategy = tool.spec.compensation_strategy
        step.rollback_reliability = tool.spec.rollback_reliability

    def _budget_exceeded(self, plan: TransactionPlan, budget: ResourceBudget) -> bool:
        if budget.max_tool_calls is not None and len(plan.steps) > budget.max_tool_calls:
            step = plan.steps[min(budget.max_tool_calls, len(plan.steps) - 1)]
            self._record_budget_exceeded(
                step,
                "Plan exceeds maximum tool calls",
                limit="max_tool_calls",
                allowed=budget.max_tool_calls,
                actual=len(plan.steps),
            )
            return True

        file_writes = 0
        network_requests = 0
        for step in plan.steps:
            try:
                tool = self.registry.get(step.tool_name)
            except KeyError as exc:
                if self.policy.profile_name not in PRODUCTION_PROFILE_NAMES:
                    raise
                self._record_budget_exceeded(
                    step,
                    "Unknown tool in plan",
                    limit="known_tools",
                    allowed="registered tool",
                    actual=step.tool_name,
                    error=str(exc),
                )
                return True
            self._hydrate_step_metadata(step, tool)

            if _risk_value(step.risk) > _risk_value(budget.max_risk_level):
                self._record_budget_exceeded(
                    step,
                    "Step exceeds maximum risk level",
                    limit="max_risk_level",
                    allowed=budget.max_risk_level.value,
                    actual=step.risk.value,
                )
                return True

            required = set(step.required_permissions)
            if Permission.WRITE_FILES in required:
                file_writes += 1
                if budget.max_file_writes is not None and file_writes > budget.max_file_writes:
                    self._record_budget_exceeded(
                        step,
                        "Plan exceeds maximum file writes",
                        limit="max_file_writes",
                        allowed=budget.max_file_writes,
                        actual=file_writes,
                    )
                    return True

            if tool.spec.network_access or Permission.NETWORK in required:
                network_requests += 1
                if budget.max_network_requests is not None and network_requests > budget.max_network_requests:
                    self._record_budget_exceeded(
                        step,
                        "Plan exceeds maximum network requests",
                        limit="max_network_requests",
                        allowed=budget.max_network_requests,
                        actual=network_requests,
                    )
                    return True

        if (
            hasattr(plan, "metrics")
            and budget.max_retries is not None
            and plan.metrics.retries_used > budget.max_retries
        ):
            if not plan.steps:
                return True
            self._record_budget_exceeded(
                plan.steps[0],
                "Plan exceeds maximum retries",
                limit="max_retries",
                allowed=budget.max_retries,
                actual=plan.metrics.retries_used,
            )
            return True

        self.audit_log.record(
            "budget.checked",
            "Resource budget accepted",
            plan_id=plan.plan_id,
            max_tool_calls=budget.max_tool_calls,
            max_file_writes=budget.max_file_writes,
            max_network_requests=budget.max_network_requests,
            max_risk_level=budget.max_risk_level.value,
        )
        return False

    def _record_budget_exceeded(self, step: ActionStep, message: str, **payload: Any) -> None:
        step.status = StepStatus.BLOCKED
        error = BudgetExceeded(message)
        self.audit_log.record(
            "budget.exceeded",
            message,
            step_id=step.step_id,
            tool=step.tool_name,
            error_type=type(error).__name__,
            **payload,
        )

    def _idempotency_conflict(self, step: ActionStep, state: WorldState) -> bool:
        if not step.idempotency_key:
            return False
        marker = self._idempotency_marker(step.idempotency_key)
        if marker not in state.facts:
            return False
        step.status = StepStatus.BLOCKED
        error = IdempotencyConflict("Step idempotency key was already consumed")
        self.audit_log.record(
            "step.idempotency_duplicate",
            "Step idempotency key was already consumed",
            step_id=step.step_id,
            tool=step.tool_name,
            idempotency_key=step.idempotency_key,
            previous=state.facts[marker],
            error_type=type(error).__name__,
        )
        return True

    def _record_idempotency_key(self, step: ActionStep, state: WorldState) -> None:
        marker = self._idempotency_marker(step.idempotency_key or "")
        record = {"step_id": step.step_id, "tool": step.tool_name}
        state.set_fact(marker, record, trust_level=TrustLevel.VERIFIED)
        self.audit_log.record(
            "step.idempotency_recorded",
            "Step idempotency key recorded",
            step_id=step.step_id,
            tool=step.tool_name,
            idempotency_key=step.idempotency_key,
        )

    @staticmethod
    def _idempotency_marker(idempotency_key: str) -> str:
        return f"idempotency:{idempotency_key}"

    def _check_conditions(self, conditions: Sequence[StateCondition], state: WorldState) -> list[dict[str, Any]]:
        issues = []
        for condition in conditions:
            present = condition.variable in state.facts
            actual = state.facts.get(condition.variable)
            if condition.operator == "exists" and not present:
                issues.append({**condition.describe(), "reason": "missing_fact"})
                continue
            if condition.operator == "not_exists" and present:
                issues.append({**condition.describe(), "reason": "unexpected_fact", "actual": actual})
                continue
            if condition.operator == "equals" and (not present or actual != condition.value):
                issues.append({**condition.describe(), "reason": "value_mismatch", "actual": actual})
                continue
            if condition.trust_level is not None and state.trust.get(condition.variable) != condition.trust_level:
                trust = state.trust.get(condition.variable)
                issues.append(
                    {
                        **condition.describe(),
                        "reason": "trust_mismatch",
                        "actual_trust": trust.value if trust else None,
                    }
                )
        return issues

    def _runtime_attestation_block_reason(self, step: ActionStep, tool: Tool) -> str | None:
        if self.policy.profile_name not in PRODUCTION_PROFILE_NAMES:
            return None
        if not (tool.spec.network_access or Permission.NETWORK in tool.spec.permissions):
            return None

        attestations: Mapping[str, Any]
        runtime_attestations = getattr(tool, "runtime_attestations", None)
        if callable(runtime_attestations):
            try:
                raw = runtime_attestations()
                attestations = raw if isinstance(raw, Mapping) else {}
            except Exception:  # noqa: BLE001 - attestation failure must fail closed
                attestations = {}
        else:
            attestations = {}

        expected_host = tool.spec.egress_host or ""
        runtime_host = str(attestations.get("runtime_egress_host", ""))
        host_match = bool(expected_host and runtime_host == expected_host)
        forward_methods = tuple(str(method).upper() for method in tool.spec.egress_methods)
        rollback_methods = tuple(str(method).upper() for method in tool.spec.rollback_egress_methods)
        mode = str(attestations.get("runtime_egress_mode", "unknown"))
        runtime_allows = getattr(getattr(tool, "client", None), "runtime_allows_egress", None)
        if callable(runtime_allows) and expected_host and mode == "enforced":
            missing_forward = tuple(method for method in forward_methods if not runtime_allows(expected_host, method))
            missing_rollback = tuple(method for method in rollback_methods if not runtime_allows(expected_host, method))
        else:
            missing_forward = ()
            missing_rollback = ()

        self.audit_log.record(
            "runtime.attestation_checked",
            "Runtime attestations checked for production network tool",
            step_id=step.step_id,
            tool=step.tool_name,
            profile=self.policy.profile_name,
            network_access=tool.spec.network_access,
            runtime_egress_enforced=attestations.get("runtime_egress_enforced"),
            runtime_egress_policy_configured=attestations.get("runtime_egress_policy_configured"),
            runtime_egress_mode=mode,
            runtime_egress_host=runtime_host,
            expected_egress_host=expected_host,
            host_match=host_match,
            expected_forward_methods=list(forward_methods),
            expected_rollback_methods=list(rollback_methods),
            runtime_missing_forward_methods=list(missing_forward),
            runtime_missing_rollback_methods=list(missing_rollback),
        )

        reason = f"{self.policy.profile_name} requires runtime egress enforcement for network tools"
        if not attestations:
            self.audit_log.record(
                "runtime.attestation_failed",
                "Runtime attestation failed for production network tool",
                step_id=step.step_id,
                tool=step.tool_name,
                profile=self.policy.profile_name,
                network_access=tool.spec.network_access,
                reason=reason,
            )
            return reason
        if self.policy.profile_name == "production_github_only" and mode == "in_memory":
            reason = "production_github_only requires enforced runtime egress, not in-memory attestation"
            self.audit_log.record(
                "runtime.attestation_failed",
                "Runtime attestation failed for production network tool",
                step_id=step.step_id,
                tool=step.tool_name,
                profile=self.policy.profile_name,
                network_access=tool.spec.network_access,
                runtime_egress_enforced=attestations.get("runtime_egress_enforced"),
                runtime_egress_policy_configured=attestations.get("runtime_egress_policy_configured"),
                runtime_egress_mode=mode,
                runtime_egress_host=runtime_host,
                expected_egress_host=expected_host,
                host_match=host_match,
                reason=reason,
            )
            return reason
        if (
            attestations.get("runtime_egress_enforced") is not True
            or attestations.get("runtime_egress_policy_configured") is not True
            or mode not in {"enforced", "in_memory"}
        ):
            self.audit_log.record(
                "runtime.attestation_failed",
                "Runtime attestation failed for production network tool",
                step_id=step.step_id,
                tool=step.tool_name,
                profile=self.policy.profile_name,
                network_access=tool.spec.network_access,
                runtime_egress_enforced=attestations.get("runtime_egress_enforced"),
                runtime_egress_policy_configured=attestations.get("runtime_egress_policy_configured"),
                runtime_egress_mode=mode,
                runtime_egress_host=runtime_host,
                expected_egress_host=expected_host,
                host_match=host_match,
                reason=reason,
            )
            return reason
        if mode == "enforced" and not host_match:
            reason = "production profile requires runtime egress host to match tool egress host"
            self.audit_log.record(
                "runtime.attestation_failed",
                "Runtime attestation failed for production network tool",
                step_id=step.step_id,
                tool=step.tool_name,
                profile=self.policy.profile_name,
                network_access=tool.spec.network_access,
                runtime_egress_mode=mode,
                runtime_egress_host=runtime_host,
                expected_egress_host=expected_host,
                host_match=host_match,
                reason=reason,
            )
            return reason
        if mode == "enforced" and not callable(runtime_allows):
            reason = "production profile requires runtime egress method attestation"
            self.audit_log.record(
                "runtime.attestation_failed",
                "Runtime attestation failed for production network tool",
                step_id=step.step_id,
                tool=step.tool_name,
                profile=self.policy.profile_name,
                network_access=tool.spec.network_access,
                runtime_egress_mode=mode,
                runtime_egress_host=runtime_host,
                expected_egress_host=expected_host,
                reason=reason,
            )
            return reason
        if missing_forward:
            reason = "production profile runtime egress policy does not allow required forward methods"
            self.audit_log.record(
                "runtime.attestation_failed",
                "Runtime attestation failed for production network tool",
                step_id=step.step_id,
                tool=step.tool_name,
                profile=self.policy.profile_name,
                expected_egress_host=expected_host,
                runtime_egress_host=runtime_host,
                runtime_missing_forward_methods=list(missing_forward),
                reason=reason,
            )
            return reason
        if missing_rollback:
            reason = "production profile runtime egress policy does not allow required rollback methods"
            self.audit_log.record(
                "runtime.attestation_failed",
                "Runtime attestation failed for production network tool",
                step_id=step.step_id,
                tool=step.tool_name,
                profile=self.policy.profile_name,
                expected_egress_host=expected_host,
                runtime_egress_host=runtime_host,
                runtime_missing_rollback_methods=list(missing_rollback),
                reason=reason,
            )
            return reason
        return None

    def _rollback(
        self,
        rollback_stack: list[tuple[Tool, dict[str, Any], ActionStep]],
        state: WorldState,
        *,
        plan: TransactionPlan | None = None,
    ) -> None:
        goal_id = plan.goal.goal_id if plan is not None else None
        plan_id = plan.plan_id if plan is not None else None
        rollback_succeeded = 0
        rollback_failed = 0
        while rollback_stack:
            tool, token, step = rollback_stack.pop()
            self.audit_log.record(
                "rollback_attempted", "Attempting rollback", step_id=step.step_id, tool=tool.spec.name
            )
            egress_issue = self._rollback_egress_block_reason(tool)
            if egress_issue is not None:
                step.status = StepStatus.FAILED
                rollback_failed += 1
                host, methods, reason = egress_issue
                self.audit_log.record(
                    "rollback.egress_blocked",
                    "Rollback blocked by production egress policy",
                    step_id=step.step_id,
                    tool=tool.spec.name,
                    host=host,
                    rollback_methods=list(methods),
                    profile=self.policy.profile_name,
                    network_access=tool.spec.network_access,
                    reason=reason,
                )
                self._record_manual_recovery(step, tool, reason, goal_id=goal_id, plan_id=plan_id)
                continue
            if tool.spec.network_access or Permission.NETWORK in tool.spec.permissions:
                self.audit_log.record(
                    "rollback.egress_allowed",
                    "Rollback egress allowed by runtime policy",
                    step_id=step.step_id,
                    tool=tool.spec.name,
                    host=tool.spec.egress_host,
                    rollback_methods=list(tool.spec.rollback_egress_methods),
                    profile=self.policy.profile_name,
                    network_access=tool.spec.network_access,
                )
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
                error_type=_error_type(error),
            )
            self._record_manual_recovery(step, tool, result.message, goal_id=goal_id, plan_id=plan_id)
        if rollback_failed and rollback_succeeded:
            self.audit_log.record(
                "rollback_partially_completed",
                "Some rollback steps succeeded and some failed",
                succeeded=rollback_succeeded,
                failed=rollback_failed,
            )

    def _rollback_egress_block_reason(self, tool: Tool) -> tuple[str, tuple[str, ...], str] | None:
        if self.policy.profile_name not in PRODUCTION_PROFILE_NAMES:
            return None
        if not (tool.spec.network_access or Permission.NETWORK in tool.spec.permissions):
            return None
        host = tool.spec.egress_host or ""
        methods = tuple(str(method).upper() for method in tool.spec.rollback_egress_methods)
        if self.policy.egress_policy is None:
            return host, methods, f"{self.policy.profile_name} forbids rollback egress without an explicit policy"
        if not host:
            return host, methods, f"{self.policy.profile_name} rollback egress missing host"
        if not methods:
            return host, methods, f"{self.policy.profile_name} requires rollback egress methods for {tool.spec.name}"
        missing = tuple(method for method in methods if not self.policy.egress_policy.allows(host, method))
        if missing:
            reason = f"{self.policy.profile_name} egress policy does not allow rollback {','.join(missing)} {host}"
            return host, methods, reason
        return None

    def _record_manual_recovery(
        self,
        step: ActionStep,
        tool: Tool,
        reason: str,
        *,
        goal_id: str | None = None,
        plan_id: str | None = None,
    ) -> None:
        packet = ManualRecoveryPacket.build(
            goal_id=goal_id,
            plan_id=plan_id,
            step_id=step.step_id,
            tool_name=tool.spec.name,
            reason=reason,
            risk_level=step.risk.value,
            profile=self.policy.profile_name,
            rollback_summary=(
                f"{step.reversibility.value}; compensation={step.compensation_strategy.value}; "
                f"rollback_reliability={step.rollback_reliability:.2f}"
            ),
            affected_resources=_affected_resources(step),
        )
        self.audit_log.record(
            "recovery.packet_created",
            "Manual recovery packet created",
            packet=packet.as_dict(),
        )
        self.audit_log.record(
            "recovery.manual_action_required",
            "Manual recovery action is required",
            recovery_id=packet.recovery_id,
            goal_id=goal_id,
            plan_id=plan_id,
            step_id=step.step_id,
            tool=tool.spec.name,
            reason=reason,
        )
