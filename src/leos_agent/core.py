"""Leos autonomous-agent kernel.

This module is intentionally small, typed, and dependency-free. It is not a
chatbot wrapper. It is a safety-first runtime skeleton for building agents that
can plan, predict, act, verify, remember, and roll back under explicit policy.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence


class Permission(str, Enum):
    """Capability classes used by the policy engine."""

    READ_MEMORY = "read_memory"
    WRITE_MEMORY = "write_memory"
    READ_FILES = "read_files"
    WRITE_FILES = "write_files"
    NETWORK = "network"
    SEND_MESSAGE = "send_message"
    EXECUTE_CODE = "execute_code"
    DELETE = "delete"
    FINANCIAL = "financial"
    SYSTEM_CONFIG = "system_config"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Decision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    NEEDS_HUMAN = "needs_human"


class StepStatus(str, Enum):
    PENDING = "pending"
    DRY_RUN_OK = "dry_run_ok"
    EXECUTED = "executed"
    VERIFIED = "verified"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Goal:
    """A user or system goal with explicit success and stop conditions."""

    description: str
    success_criteria: Sequence[str]
    constraints: Sequence[str] = ()
    stop_conditions: Sequence[str] = ()
    priority: int = 5
    goal_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class WorldState:
    """The agent's explicit belief state.

    `facts` should contain verified claims. `assumptions` should contain beliefs
    that still need validation. Treating these as separate fields prevents the
    agent from silently confusing guesses with reality.
    """

    facts: Dict[str, Any] = field(default_factory=dict)
    assumptions: Dict[str, Any] = field(default_factory=dict)
    uncertainty: Dict[str, float] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "facts": dict(self.facts),
            "assumptions": dict(self.assumptions),
            "uncertainty": dict(self.uncertainty),
        }


@dataclass(frozen=True)
class EffectPrediction:
    """A causal prediction made before an action is taken."""

    variable: str
    before: Any
    expected_after: Any
    confidence: float
    rationale: str


@dataclass(frozen=True)
class CausalHypothesis:
    """A simple causal edge: taking an action changes one or more variables."""

    action_name: str
    affected_variables: Sequence[str]
    rationale: str
    confidence: float = 0.5


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    observed_state_delta: Dict[str, Any] = field(default_factory=dict)
    rollback_token: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    permissions: Sequence[Permission]
    default_risk: RiskLevel = RiskLevel.LOW
    reversible: bool = False


class Tool(Protocol):
    spec: ToolSpec

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        ...

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        ...

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        ...


@dataclass
class ActionStep:
    tool_name: str
    arguments: Dict[str, Any]
    reason: str
    status: StepStatus = StepStatus.PENDING
    risk: RiskLevel = RiskLevel.LOW
    required_permissions: Sequence[Permission] = ()
    predictions: List[EffectPrediction] = field(default_factory=list)
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class TransactionPlan:
    goal: Goal
    steps: List[ActionStep]
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    message: str
    payload: Dict[str, Any]
    created_at: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class AuditLog:
    """Append-only JSONL audit log."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self.events: List[AuditEvent] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, message: str, **payload: Any) -> AuditEvent:
        event = AuditEvent(event_type=event_type, message=message, payload=payload)
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")
        return event


class MemoryStore:
    """Small persistent memory store with explicit confidence and provenance."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self.items: List[Dict[str, Any]] = []
        if path and path.exists():
            self.items = json.loads(path.read_text(encoding="utf-8"))

    def remember(self, key: str, value: Any, *, confidence: float, provenance: str) -> None:
        self.items.append(
            {
                "key": key,
                "value": value,
                "confidence": confidence,
                "provenance": provenance,
                "created_at": time.time(),
            }
        )
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, indent=2, ensure_ascii=False), encoding="utf-8")

    def recall(self, key: str) -> List[Dict[str, Any]]:
        return [item for item in self.items if item["key"] == key]


class CausalWorldModel:
    """Minimal causal model with pre-action predictions and post-action checks."""

    def __init__(self, hypotheses: Optional[Iterable[CausalHypothesis]] = None) -> None:
        self.hypotheses: List[CausalHypothesis] = list(hypotheses or [])

    def register(self, hypothesis: CausalHypothesis) -> None:
        self.hypotheses.append(hypothesis)

    def predict(self, step: ActionStep, state: WorldState) -> List[EffectPrediction]:
        predictions: List[EffectPrediction] = []
        for hypothesis in self.hypotheses:
            if hypothesis.action_name != step.tool_name:
                continue
            for variable in hypothesis.affected_variables:
                before = state.facts.get(variable, state.assumptions.get(variable))
                expected = step.arguments.get(variable, "changed")
                predictions.append(
                    EffectPrediction(
                        variable=variable,
                        before=before,
                        expected_after=expected,
                        confidence=hypothesis.confidence,
                        rationale=hypothesis.rationale,
                    )
                )
        return predictions

    def verify(self, predictions: Sequence[EffectPrediction], result: ToolResult) -> ToolResult:
        mismatches = []
        for prediction in predictions:
            if prediction.variable not in result.observed_state_delta:
                mismatches.append(
                    {
                        "variable": prediction.variable,
                        "expected_after": prediction.expected_after,
                        "observed": None,
                        "reason": "missing_observation",
                    }
                )
                continue
            observed = result.observed_state_delta[prediction.variable]
            if prediction.expected_after != "changed" and observed != prediction.expected_after:
                mismatches.append(
                    {
                        "variable": prediction.variable,
                        "expected_after": prediction.expected_after,
                        "observed": observed,
                        "reason": "unexpected_value",
                    }
                )
        if mismatches:
            return ToolResult(False, "Causal verification failed", {"mismatches": mismatches})
        return ToolResult(True, "Causal verification passed")


class PolicyEngine:
    """Capability and risk policy.

    The default rule is conservative:
    - LOW actions can run automatically.
    - MEDIUM actions require explicit permission grant.
    - HIGH/CRITICAL actions require human approval.
    """

    def __init__(self, granted_permissions: Optional[Iterable[Permission]] = None) -> None:
        self.granted_permissions = set(granted_permissions or [])

    def assess(self, tool: Tool, arguments: Mapping[str, Any]) -> RiskLevel:
        risk = tool.spec.default_risk
        if any(permission in tool.spec.permissions for permission in [Permission.DELETE, Permission.FINANCIAL, Permission.SYSTEM_CONFIG]):
            return RiskLevel.CRITICAL
        if arguments.get("destructive") is True:
            return RiskLevel.HIGH
        return risk

    def decide(self, step: ActionStep) -> Decision:
        missing = set(step.required_permissions) - self.granted_permissions
        if missing:
            return Decision.NEEDS_HUMAN
        if step.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return Decision.NEEDS_HUMAN
        return Decision.APPROVED


class ApprovalGate:
    """Human-in-the-loop gate for risky steps."""

    def __init__(self, approver: Optional[Callable[[ActionStep], bool]] = None) -> None:
        self.approver = approver

    def request(self, step: ActionStep) -> Decision:
        if not self.approver:
            return Decision.DENIED
        return Decision.APPROVED if self.approver(step) else Decision.DENIED


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.spec.name}")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def names(self) -> List[str]:
        return sorted(self._tools)


class TransactionManager:
    """Executes plan steps as reversible transactions where possible."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        causal_model: CausalWorldModel,
        audit_log: AuditLog,
        approval_gate: Optional[ApprovalGate] = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.causal_model = causal_model
        self.audit_log = audit_log
        self.approval_gate = approval_gate or ApprovalGate()

    def execute_plan(self, plan: TransactionPlan, state: WorldState) -> TransactionPlan:
        self.audit_log.record("plan.started", "Starting transaction plan", plan_id=plan.plan_id, goal=plan.goal.description)
        rollback_stack: List[tuple[Tool, Dict[str, Any], ActionStep]] = []

        for step in plan.steps:
            tool = self.registry.get(step.tool_name)
            step.required_permissions = tuple(tool.spec.permissions)
            step.risk = self.policy.assess(tool, step.arguments)
            step.predictions = self.causal_model.predict(step, state)

            decision = self.policy.decide(step)
            if decision is Decision.NEEDS_HUMAN:
                decision = self.approval_gate.request(step)
            if decision is not Decision.APPROVED:
                step.status = StepStatus.BLOCKED
                self.audit_log.record("step.blocked", "Step blocked by policy", step_id=step.step_id, tool=step.tool_name, decision=decision.value)
                self._rollback(rollback_stack, state)
                break

            dry_run = tool.dry_run(step.arguments, state)
            if not dry_run.ok:
                step.status = StepStatus.FAILED
                self.audit_log.record("step.dry_run_failed", dry_run.message, step_id=step.step_id, data=dry_run.data)
                self._rollback(rollback_stack, state)
                break
            step.status = StepStatus.DRY_RUN_OK
            self.audit_log.record("step.dry_run_ok", dry_run.message, step_id=step.step_id, tool=step.tool_name)

            result = tool.execute(step.arguments, state)
            if not result.ok:
                step.status = StepStatus.FAILED
                self.audit_log.record("step.execution_failed", result.message, step_id=step.step_id, data=result.data)
                self._rollback(rollback_stack, state)
                break

            step.status = StepStatus.EXECUTED
            if result.rollback_token:
                rollback_stack.append((tool, dict(result.rollback_token), step))
            state.facts.update(result.observed_state_delta)
            self.audit_log.record("step.executed", result.message, step_id=step.step_id, observed=result.observed_state_delta)

            verification = self.causal_model.verify(step.predictions, result)
            if not verification.ok:
                step.status = StepStatus.FAILED
                self.audit_log.record("step.verification_failed", verification.message, step_id=step.step_id, data=verification.data)
                self._rollback(rollback_stack, state)
                break
            step.status = StepStatus.VERIFIED
            self.audit_log.record("step.verified", verification.message, step_id=step.step_id)

        self.audit_log.record("plan.finished", "Finished transaction plan", plan_id=plan.plan_id)
        return plan

    def _rollback(self, rollback_stack: List[tuple[Tool, Dict[str, Any], ActionStep]], state: WorldState) -> None:
        while rollback_stack:
            tool, token, step = rollback_stack.pop()
            result = tool.rollback(token, state)
            step.status = StepStatus.ROLLED_BACK if result.ok else StepStatus.FAILED
            self.audit_log.record("step.rollback", result.message, step_id=step.step_id, ok=result.ok)


class AgentKernel:
    """The orchestration kernel for a Leos-style autonomous agent."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        causal_model: Optional[CausalWorldModel] = None,
        memory: Optional[MemoryStore] = None,
        audit_log: Optional[AuditLog] = None,
        approval_gate: Optional[ApprovalGate] = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.causal_model = causal_model or CausalWorldModel()
        self.memory = memory or MemoryStore()
        self.audit_log = audit_log or AuditLog()
        self.state = WorldState()
        self.transactions = TransactionManager(
            registry=registry,
            policy=policy,
            causal_model=self.causal_model,
            audit_log=self.audit_log,
            approval_gate=approval_gate,
        )

    def build_plan(self, goal: Goal, steps: Sequence[ActionStep]) -> TransactionPlan:
        if not goal.success_criteria:
            raise ValueError("Goal must have explicit success criteria")
        if not goal.stop_conditions:
            self.audit_log.record("goal.warning", "Goal has no stop conditions", goal_id=goal.goal_id)
        return TransactionPlan(goal=goal, steps=list(steps))

    def run(self, plan: TransactionPlan) -> TransactionPlan:
        return self.transactions.execute_plan(plan, self.state)


class EchoTool:
    spec = ToolSpec(
        name="echo",
        description="Return a message and record it in observed state.",
        permissions=(),
        default_risk=RiskLevel.LOW,
        reversible=False,
    )

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        if "message" not in arguments:
            return ToolResult(False, "Missing required argument: message")
        return ToolResult(True, f"Would echo: {arguments['message']}")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        message = str(arguments["message"])
        return ToolResult(True, message, observed_state_delta={"last_echo": message})

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "Echo has no rollback side effect")


class SafeFileWriteTool:
    """A reversible file writer constrained to a workspace root."""

    spec = ToolSpec(
        name="safe_file_write",
        description="Write a UTF-8 file inside the configured workspace root.",
        permissions=(Permission.WRITE_FILES,),
        default_risk=RiskLevel.MEDIUM,
        reversible=True,
    )

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        resolved = (self.workspace_root / path).resolve()
        if os.path.commonpath([self.workspace_root, resolved]) != str(self.workspace_root):
            raise ValueError("Path escapes workspace root")
        return resolved

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        try:
            path = self._resolve(str(arguments["path"]))
        except Exception as exc:  # noqa: BLE001 - dry-run should report any validation issue
            return ToolResult(False, f"Invalid path: {exc}")
        if "content" not in arguments:
            return ToolResult(False, "Missing required argument: content")
        return ToolResult(True, f"Would write {path}", data={"path": str(path)})

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        path = self._resolve(str(arguments["path"]))
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(arguments["content"]), encoding="utf-8")
        return ToolResult(
            True,
            f"Wrote {path}",
            observed_state_delta={"file_written": str(path)},
            rollback_token={"path": str(path), "previous": previous},
        )

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        path = Path(str(token["path"]))
        previous = token.get("previous")
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(str(previous), encoding="utf-8")
        return ToolResult(True, f"Rolled back {path}")


def default_registry(workspace_root: Optional[Path] = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    if workspace_root:
        registry.register(SafeFileWriteTool(workspace_root))
    return registry
