from __future__ import annotations

import tempfile
import time
import unittest
import json
from pathlib import Path
from typing import Any, Mapping

from leos_agent import (
    ActionStep,
    AgentKernel,
    ApprovalGate,
    AuditLog,
    BUILT_IN_POLICY_PROFILES,
    CausalGraph,
    CausalHypothesis,
    CompensationStrategy,
    CausalWorldModel,
    Goal,
    GoalStatus,
    InvalidGoalTransition,
    MemorySensitivity,
    MemoryStore,
    MemoryType,
    Permission,
    PlanProposal,
    PlannerConfig,
    PolicyConfigurationError,
    PolicyEngine,
    PolicyProfile,
    PolicyRule,
    ResourceBudget,
    RiskLevel,
    AuditReplayer,
    ReplayResult,
    RetryPolicy,
    Reversibility,
    RuntimeTask,
    SchemaValidationFailed,
    StepStatus,
    StateCondition,
    TaskQueue,
    TaskRunner,
    TaskStatus,
    ToolManifest,
    TrustLevel,
    TimeoutPolicy,
    Watchdog,
    default_registry,
    replay_audit_log,
    validate_json_schema,
    validate_policy_config,
)
from leos_agent.cli import build_demo_agent
from leos_agent.errors import RollbackFailed, SecretBoundaryViolation, WorkspaceEscapeBlocked
from leos_agent.core import ToolResult, ToolSpec, ToolRegistry, WorldState


class DryRunFailingTool:
    spec = ToolSpec(
        name="dry_run_fails",
        description="Fails dry-run and records if execute is called.",
        permissions=(),
        default_risk=RiskLevel.LOW,
    )

    def __init__(self) -> None:
        self.executed = False

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(False, "dry-run failed")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.executed = True
        return ToolResult(True, "executed")

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "rolled back")


class HighRiskTool:
    spec = ToolSpec(
        name="high_risk",
        description="High-risk test tool.",
        permissions=(),
        default_risk=RiskLevel.HIGH,
    )

    def __init__(self) -> None:
        self.executed = False

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry-run passed")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.executed = True
        return ToolResult(True, "executed")

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "rolled back")


class RollbackFailingTool:
    spec = ToolSpec(
        name="rollback_fails",
        description="Executes successfully but cannot roll back.",
        permissions=(),
        default_risk=RiskLevel.LOW,
    )

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry-run passed")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(
            True,
            "executed",
            observed_state_delta={"rollback_failing_tool_executed": True},
            rollback_token={"resource": "rollback-failure-test"},
        )

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(False, "rollback failed", error=RollbackFailed("rollback failed"))


class RollbackSucceedingTool:
    spec = ToolSpec(
        name="rollback_succeeds",
        description="Executes successfully and rolls back successfully.",
        permissions=(),
        default_risk=RiskLevel.LOW,
    )

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry-run passed")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(
            True,
            "executed",
            observed_state_delta={"rollback_succeeding_tool_executed": True},
            rollback_token={"resource": "rollback-success-test"},
        )

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "rollback succeeded")


class IrreversibleWriteTool:
    spec = ToolSpec(
        name="irreversible_write",
        description="Consequential write that cannot be undone.",
        permissions=(Permission.WRITE_FILES,),
        default_risk=RiskLevel.MEDIUM,
        reversibility=Reversibility.IRREVERSIBLE,
        compensation_strategy=CompensationStrategy.MANUAL,
    )

    def __init__(self) -> None:
        self.executed = False

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry-run passed")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.executed = True
        return ToolResult(True, "executed")

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(False, "cannot roll back")


class CompensatableWriteTool:
    spec = ToolSpec(
        name="compensatable_write",
        description="Consequential write that can only be compensated.",
        permissions=(Permission.WRITE_FILES,),
        default_risk=RiskLevel.MEDIUM,
        reversibility=Reversibility.COMPENSATABLE,
        compensation_strategy=CompensationStrategy.COMPENSATE,
    )

    def __init__(self) -> None:
        self.executed = False

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry-run passed")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.executed = True
        return ToolResult(True, "executed")

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "compensated")


class NetworkTool:
    spec = ToolSpec(
        name="network_fetch",
        description="Network test tool.",
        permissions=(Permission.NETWORK,),
        default_risk=RiskLevel.LOW,
    )

    def __init__(self) -> None:
        self.executed = False

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry-run passed")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.executed = True
        return ToolResult(True, "executed")

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "rolled back")


class BadOutputSchemaTool:
    spec = ToolSpec(
        name="bad_output_schema",
        description="Returns observed state that violates its output schema.",
        permissions=(),
        default_risk=RiskLevel.LOW,
        reversible=True,
        output_schema={
            "type": "object",
            "required": ["external_id"],
            "properties": {
                "external_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )

    def __init__(self) -> None:
        self.executed = False
        self.rolled_back = False

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry-run passed")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.executed = True
        return ToolResult(
            True,
            "executed with malformed output",
            observed_state_delta={"external_id": 42, "unexpected": True},
            rollback_token={"external_id": 42},
        )

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.rolled_back = True
        return ToolResult(True, "rolled back malformed output")


class AgentKernelTests(unittest.TestCase):
    def test_split_module_imports_match_core_exports(self) -> None:
        from leos_agent.audit import AuditLog as SplitAuditLog
        from leos_agent.core import AuditLog as CoreAuditLog
        from leos_agent.core import ToolSpec as CoreToolSpec
        from leos_agent.core import WorldState as CoreWorldState
        from leos_agent.state import WorldState as SplitWorldState
        from leos_agent.tools import ToolSpec as SplitToolSpec

        self.assertIs(CoreAuditLog, SplitAuditLog)
        self.assertIs(CoreToolSpec, SplitToolSpec)
        self.assertIs(CoreWorldState, SplitWorldState)

    def test_world_state_tracks_trust_without_promoting_assumptions(self) -> None:
        state = WorldState()

        state.set_assumption("user_timezone", "Asia/Shanghai", uncertainty=0.2)

        self.assertNotIn("user_timezone", state.facts)
        self.assertEqual(state.assumptions["user_timezone"], "Asia/Shanghai")
        self.assertEqual(state.trust["user_timezone"], TrustLevel.MODEL_INFERRED)
        self.assertEqual(state.snapshot()["trust"]["user_timezone"], "model_inferred")

        state.promote_assumption("user_timezone")

        self.assertEqual(state.facts["user_timezone"], "Asia/Shanghai")
        self.assertNotIn("user_timezone", state.assumptions)
        self.assertNotIn("user_timezone", state.uncertainty)
        self.assertEqual(state.trust["user_timezone"], TrustLevel.VERIFIED)

    def test_low_risk_echo_runs_and_verifies(self) -> None:
        registry = default_registry()
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Echo a message",
            success_criteria=["last_echo equals the message"],
            stop_conditions=["one step complete"],
        )
        plan = agent.build_plan(goal, [ActionStep("echo", {"message": "hello"}, "test echo")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
        self.assertEqual(agent.state.facts["last_echo"], "hello")

    def test_goal_lifecycle_success_transitions_are_audited(self) -> None:
        registry = default_registry()
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Echo a message",
            success_criteria=["goal succeeds"],
            stop_conditions=["one step complete"],
        )
        plan = agent.build_plan(goal, [ActionStep("echo", {"message": "hello"}, "test lifecycle")])

        result = agent.run(plan)

        self.assertEqual(result.goal.status, GoalStatus.SUCCEEDED)
        transitions = [event for event in agent.audit_log.events if event.event_type == "goal.status_changed"]
        observed = [(event.payload["from_status"], event.payload["to_status"]) for event in transitions]
        self.assertIn(("created", "planning"), observed)
        self.assertIn(("planning", "running"), observed)
        self.assertIn(("running", "succeeded"), observed)

    def test_goal_lifecycle_partially_done_after_later_block(self) -> None:
        registry = default_registry()
        high_risk = HighRiskTool()
        registry.register(high_risk)
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Partially complete before a block",
            success_criteria=["partial status is explicit"],
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(
            goal,
            [
                ActionStep("echo", {"message": "first"}, "complete low-risk work"),
                ActionStep("high_risk", {}, "blocked high-risk work"),
            ],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
        self.assertEqual(result.steps[1].status, StepStatus.BLOCKED)
        self.assertEqual(result.goal.status, GoalStatus.PARTIALLY_DONE)
        self.assertFalse(high_risk.executed)

    def test_goal_lifecycle_rejects_invalid_terminal_transition(self) -> None:
        goal = Goal(
            description="Terminal transition boundary",
            success_criteria=["invalid transition fails"],
            stop_conditions=["exception"],
        ).transition(GoalStatus.PLANNING).transition(GoalStatus.RUNNING).transition(GoalStatus.SUCCEEDED)

        with self.assertRaises(InvalidGoalTransition):
            goal.transition(GoalStatus.RUNNING)

    def test_task_queue_enqueues_and_claims_fifo(self) -> None:
        audit = AuditLog()
        queue = TaskQueue(audit)
        agent = AgentKernel(registry=default_registry(), policy=PolicyEngine(), audit_log=audit)
        first = agent.build_plan(
            Goal("First task", ["first runs"], stop_conditions=["done"]),
            [ActionStep("echo", {"message": "first"}, "first")],
        )
        second = agent.build_plan(
            Goal("Second task", ["second runs"], stop_conditions=["done"]),
            [ActionStep("echo", {"message": "second"}, "second")],
        )

        first_task = queue.enqueue(first)
        second_task = queue.enqueue(second)
        claimed = queue.claim("worker-1", now=10.0)

        self.assertIsInstance(first_task, RuntimeTask)
        self.assertEqual(claimed.task_id, first_task.task_id)
        self.assertEqual(second_task.status, TaskStatus.QUEUED)
        self.assertEqual(first_task.status, TaskStatus.RUNNING)
        self.assertEqual(first_task.locked_by, "worker-1")
        events = [event.event_type for event in audit.events]
        self.assertIn("task.enqueued", events)
        self.assertIn("task.claimed", events)

    def test_task_queue_idempotency_deduplicates_enqueued_tasks(self) -> None:
        audit = AuditLog()
        queue = TaskQueue(audit)
        agent = AgentKernel(registry=default_registry(), policy=PolicyEngine(), audit_log=audit)
        plan = agent.build_plan(
            Goal("Idempotent task", ["dedupe works"], stop_conditions=["done"]),
            [ActionStep("echo", {"message": "once"}, "once")],
        )

        first = queue.enqueue(plan, idempotency_key="task-once")
        duplicate = queue.enqueue(plan, idempotency_key="task-once")

        self.assertEqual(first.task_id, duplicate.task_id)
        self.assertEqual(len(queue.tasks()), 1)
        dedupe = [event for event in audit.events if event.event_type == "task.deduplicated"]
        self.assertEqual(dedupe[0].payload["idempotency_key"], "task-once")

    def test_watchdog_marks_stale_heartbeat_as_timed_out(self) -> None:
        audit = AuditLog()
        queue = TaskQueue(audit)
        watchdog = Watchdog(queue, audit)
        agent = AgentKernel(registry=default_registry(), policy=PolicyEngine(), audit_log=audit)
        plan = agent.build_plan(
            Goal("Watchdog task", ["timeout is explicit"], stop_conditions=["timed out"]),
            [ActionStep("echo", {"message": "slow"}, "slow")],
        )
        task = queue.enqueue(plan, timeout_policy=TimeoutPolicy(heartbeat_timeout_seconds=5.0))
        queue.claim("worker-1", now=10.0)

        timed_out = watchdog.check(now=16.0)

        self.assertEqual(timed_out[0].task_id, task.task_id)
        self.assertEqual(task.status, TaskStatus.TIMED_OUT)
        self.assertIsNone(task.locked_by)
        self.assertEqual(task.failure_reason, "Task heartbeat timed out")
        events = [event for event in audit.events if event.event_type == "task.timed_out"]
        self.assertEqual(events[0].payload["task_id"], task.task_id)

    def test_task_queue_pause_resume_and_worker_lock(self) -> None:
        queue = TaskQueue()
        agent = AgentKernel(registry=default_registry(), policy=PolicyEngine())
        plan = agent.build_plan(
            Goal("Pause task", ["pause and resume"], stop_conditions=["done"]),
            [ActionStep("echo", {"message": "pause"}, "pause")],
        )
        task = queue.enqueue(plan)
        queue.claim("worker-1")

        with self.assertRaises(PermissionError):
            queue.heartbeat(task.task_id, "worker-2")

        queue.pause(task.task_id, "worker-1")
        self.assertEqual(task.status, TaskStatus.PAUSED)
        queue.resume(task.task_id)
        self.assertEqual(task.status, TaskStatus.QUEUED)

    def test_task_runner_executes_next_task_to_completion(self) -> None:
        audit = AuditLog()
        agent = AgentKernel(registry=default_registry(), policy=PolicyEngine(), audit_log=audit)
        queue = TaskQueue(audit)
        runner = TaskRunner(queue, agent, worker_id="worker-1", audit_log=audit)
        plan = agent.build_plan(
            Goal("Run queued task", ["task succeeds"], stop_conditions=["done"]),
            [ActionStep("echo", {"message": "queued"}, "run queued echo")],
        )
        task = queue.enqueue(plan)

        result = runner.run_next(now=20.0)

        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(agent.state.facts["last_echo"], "queued")
        events = [event.event_type for event in audit.events]
        self.assertIn("task.runner_started", events)
        self.assertIn("task.runner_finished", events)
        self.assertIn("task.completed", events)

    def test_task_runner_fails_when_goal_does_not_succeed(self) -> None:
        audit = AuditLog()
        registry = ToolRegistry()
        high_risk = HighRiskTool()
        registry.register(high_risk)
        agent = AgentKernel(registry=registry, policy=PolicyEngine(), audit_log=audit)
        queue = TaskQueue(audit)
        runner = TaskRunner(queue, agent, worker_id="worker-1", audit_log=audit)
        plan = agent.build_plan(
            Goal("Blocked queued task", ["task blocks"], stop_conditions=["blocked"]),
            [ActionStep("high_risk", {}, "blocked")],
        )
        task = queue.enqueue(plan)

        result = runner.run_next(now=30.0)

        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.failure_reason, "Goal ended with status blocked")
        self.assertFalse(high_risk.executed)

    def test_task_runner_reschedules_retryable_failure(self) -> None:
        audit = AuditLog()
        registry = ToolRegistry()
        high_risk = HighRiskTool()
        registry.register(high_risk)
        agent = AgentKernel(registry=registry, policy=PolicyEngine(), audit_log=audit)
        queue = TaskQueue(audit)
        runner = TaskRunner(queue, agent, worker_id="worker-1", audit_log=audit)
        plan = agent.build_plan(
            Goal("Retry blocked task", ["retry is scheduled"], stop_conditions=["blocked"]),
            [ActionStep("high_risk", {}, "blocked")],
        )
        task = queue.enqueue(plan, retry_policy=RetryPolicy(max_attempts=2))

        result = runner.run_next(now=40.0)

        self.assertEqual(result.task_id, task.task_id)
        self.assertEqual(task.status, TaskStatus.QUEUED)
        self.assertEqual(task.attempts, 1)
        retry_events = [event for event in audit.events if event.event_type == "task.retry_scheduled"]
        self.assertEqual(retry_events[0].payload["max_attempts"], 2)

    def test_task_runner_records_idle_when_queue_empty(self) -> None:
        audit = AuditLog()
        agent = AgentKernel(registry=default_registry(), policy=PolicyEngine(), audit_log=audit)
        queue = TaskQueue(audit)
        runner = TaskRunner(queue, agent, worker_id="worker-1", audit_log=audit)

        result = runner.run_next()

        self.assertIsNone(result)
        idle_events = [event for event in audit.events if event.event_type == "task.runner_idle"]
        self.assertEqual(idle_events[0].payload["worker_id"], "worker-1")

    def test_tool_spec_reversibility_keeps_bool_compatibility(self) -> None:
        legacy_reversible = ToolSpec(
            name="legacy_reversible",
            description="Uses the legacy boolean reversible flag.",
            permissions=(),
            reversible=True,
        )
        explicit_compensatable = ToolSpec(
            name="explicit_compensatable",
            description="Uses explicit reversibility metadata.",
            permissions=(),
            reversible=True,
            reversibility=Reversibility.COMPENSATABLE,
            compensation_strategy=CompensationStrategy.COMPENSATE,
            rollback_reliability=0.5,
        )

        self.assertTrue(legacy_reversible.reversible)
        self.assertEqual(legacy_reversible.reversibility, Reversibility.REVERSIBLE)
        self.assertFalse(explicit_compensatable.reversible)
        self.assertEqual(explicit_compensatable.reversibility, Reversibility.COMPENSATABLE)
        self.assertEqual(explicit_compensatable.compensation_strategy, CompensationStrategy.COMPENSATE)
        self.assertEqual(explicit_compensatable.rollback_reliability, 0.5)

    def test_policy_profile_factory_loads_builtin_profiles(self) -> None:
        developer = PolicyEngine.from_profile("developer_local")
        production = PolicyEngine.from_profile(BUILT_IN_POLICY_PROFILES["production"])

        self.assertEqual(developer.profile_name, "developer_local")
        self.assertIn(Permission.WRITE_FILES, developer.granted_permissions)
        self.assertIn(Permission.NETWORK, developer.deny_permissions)
        self.assertEqual(production.profile_name, "production")
        self.assertIn(Permission.WRITE_FILES, production.require_human_for)

        with self.assertRaises(KeyError):
            PolicyEngine.from_profile("missing_profile")

    def test_custom_policy_profile_can_be_used_directly(self) -> None:
        profile = PolicyProfile(
            name="custom_read_only",
            granted_permissions=(Permission.READ_FILES,),
            max_auto_risk=RiskLevel.LOW,
            deny_permissions=(Permission.WRITE_FILES,),
        )

        policy = PolicyEngine.from_profile(profile)

        self.assertEqual(policy.profile_name, "custom_read_only")
        self.assertIn(Permission.READ_FILES, policy.granted_permissions)
        self.assertIn(Permission.WRITE_FILES, policy.deny_permissions)

    def test_policy_as_code_loads_profile_from_mapping(self) -> None:
        policy = PolicyEngine.from_mapping(
            {
                "name": "locked_developer",
                "granted_permissions": ["write_files"],
                "max_auto_risk": "medium",
                "rules": [
                    {
                        "name": "deny_file_writer",
                        "when": {"tool": "safe_file_write"},
                        "decision": "denied",
                    }
                ],
            }
        )

        self.assertEqual(policy.profile_name, "locked_developer")
        self.assertIn(Permission.WRITE_FILES, policy.granted_permissions)
        self.assertEqual(policy.rules[0].name, "deny_file_writer")
        self.assertEqual(validate_policy_config({"name": "valid", "rules": [{"name": "deny_network", "when": {"permission": "network"}, "decision": "denied"}]}), [])

    def test_policy_as_code_rejects_direct_approval_rules(self) -> None:
        config = {
            "name": "unsafe_policy",
            "rules": [
                {
                    "name": "approve_everything",
                    "when": {"risk_at_least": "low"},
                    "decision": "approved",
                }
            ],
        }

        issues = validate_policy_config(config)

        self.assertEqual(issues[0]["reason"], "policy_config_invalid")
        with self.assertRaises(PolicyConfigurationError):
            PolicyEngine.from_mapping(config)

    def test_policy_as_code_denies_matching_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            policy = PolicyEngine.from_mapping(
                {
                    "name": "deny_writes_by_rule",
                    "granted_permissions": ["write_files"],
                    "max_auto_risk": "medium",
                    "rules": [
                        {
                            "name": "deny_workspace_write",
                            "when": {"tool": "safe_file_write"},
                            "decision": "denied",
                        }
                    ],
                }
            )
            agent = AgentKernel(registry=registry, policy=policy)
            goal = Goal(
                description="Configured deny rule blocks writes",
                success_criteria=["file is not written"],
                stop_conditions=["blocked"],
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "test policy rule")])

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
            self.assertFalse((Path(tmp) / "x.txt").exists())
            blocked = [event for event in agent.audit_log.events if event.event_type == "step.blocked"]
            self.assertEqual(blocked[0].payload["decision"], "denied")

    def test_policy_as_code_requires_human_for_permission(self) -> None:
        registry = ToolRegistry()
        tool = NetworkTool()
        registry.register(tool)
        policy = PolicyEngine.from_mapping(
            {
                "name": "network_review",
                "granted_permissions": ["network"],
                "max_auto_risk": "medium",
                "rules": [
                    {
                        "name": "review_network",
                        "when": {"permission": "network"},
                        "decision": "needs_human",
                    }
                ],
            }
        )
        agent = AgentKernel(registry=registry, policy=policy)
        goal = Goal(
            description="Configured needs_human rule blocks without approver",
            success_criteria=["network does not execute"],
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(goal, [ActionStep("network_fetch", {}, "test needs_human rule")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        self.assertFalse(tool.executed)
        blocked = [event for event in agent.audit_log.events if event.event_type == "step.blocked"]
        self.assertEqual(blocked[0].payload["decision"], "denied")

    def test_resource_budget_rejects_negative_limits(self) -> None:
        with self.assertRaises(ValueError):
            ResourceBudget(max_tool_calls=-1)

    def test_resource_budget_blocks_before_tool_call_limit_is_exceeded(self) -> None:
        registry = default_registry()
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Stay inside tool call budget",
            success_criteria=["no tool executes"],
            stop_conditions=["blocked"],
            budget=ResourceBudget(max_tool_calls=0),
        )
        plan = agent.build_plan(goal, [ActionStep("echo", {"message": "hello"}, "test budget boundary")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        self.assertNotIn("last_echo", agent.state.facts)
        budget_events = [event for event in agent.audit_log.events if event.event_type == "budget.exceeded"]
        self.assertEqual(budget_events[0].payload["error_type"], "BudgetExceeded")
        self.assertEqual(budget_events[0].payload["limit"], "max_tool_calls")

    def test_resource_budget_blocks_risk_above_goal_limit(self) -> None:
        registry = ToolRegistry()
        tool = HighRiskTool()
        registry.register(tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Stay inside risk budget",
            success_criteria=["high risk tool does not execute"],
            stop_conditions=["blocked"],
            budget=ResourceBudget(max_risk_level=RiskLevel.LOW),
        )
        plan = agent.build_plan(goal, [ActionStep("high_risk", {}, "test risk budget")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        self.assertFalse(tool.executed)
        budget_events = [event for event in agent.audit_log.events if event.event_type == "budget.exceeded"]
        self.assertEqual(budget_events[0].payload["limit"], "max_risk_level")
        self.assertEqual(budget_events[0].payload["allowed"], "low")
        self.assertEqual(budget_events[0].payload["actual"], "high")

    def test_resource_budget_blocks_file_write_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(registry=registry, policy=PolicyEngine.from_profile("developer_local"))
            goal = Goal(
                description="Stay inside file write budget",
                success_criteria=["file write does not execute"],
                stop_conditions=["blocked"],
                budget=ResourceBudget(max_file_writes=0),
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "test file budget")])

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
            self.assertFalse((Path(tmp) / "x.txt").exists())
            budget_events = [event for event in agent.audit_log.events if event.event_type == "budget.exceeded"]
            self.assertEqual(budget_events[0].payload["limit"], "max_file_writes")

    def test_step_precondition_blocks_before_dry_run(self) -> None:
        registry = default_registry()
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Do not act without readiness fact",
            success_criteria=["echo does not execute"],
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(
            goal,
            [
                ActionStep(
                    "echo",
                    {"message": "hello"},
                    "test precondition boundary",
                    preconditions=(StateCondition("ready", "equals", True),),
                )
            ],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        self.assertNotIn("last_echo", agent.state.facts)
        failures = [event for event in agent.audit_log.events if event.event_type == "step.precondition_failed"]
        self.assertEqual(failures[0].payload["error_type"], "PreconditionFailed")
        self.assertEqual(failures[0].payload["issues"][0]["reason"], "value_mismatch")

    def test_step_postcondition_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(registry=registry, policy=PolicyEngine.from_profile("developer_local"))
            goal = Goal(
                description="Rollback when postcondition is false",
                success_criteria=["file is rolled back"],
                stop_conditions=["failed"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {"path": "x.txt", "content": "x"},
                        "test postcondition boundary",
                        postconditions=(StateCondition("file_written", "equals", "wrong-path"),),
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.ROLLED_BACK)
            self.assertFalse((Path(tmp) / "x.txt").exists())
            failures = [event for event in agent.audit_log.events if event.event_type == "step.postcondition_failed"]
            self.assertEqual(failures[0].payload["error_type"], "PostconditionFailed")
            self.assertEqual(failures[0].payload["issues"][0]["reason"], "value_mismatch")

    def test_idempotency_key_blocks_duplicate_step(self) -> None:
        registry = default_registry()
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Do not repeat the same idempotent step",
            success_criteria=["duplicate step blocks"],
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(
            goal,
            [
                ActionStep("echo", {"message": "first"}, "record idempotency", idempotency_key="echo-once"),
                ActionStep("echo", {"message": "second"}, "duplicate idempotency", idempotency_key="echo-once"),
            ],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
        self.assertEqual(result.steps[1].status, StepStatus.BLOCKED)
        self.assertEqual(agent.state.facts["last_echo"], "first")
        self.assertIn("idempotency:echo-once", agent.state.facts)
        duplicates = [event for event in agent.audit_log.events if event.event_type == "step.idempotency_duplicate"]
        self.assertEqual(duplicates[0].payload["error_type"], "IdempotencyConflict")

    def test_safe_file_write_manifest_exposes_schema_and_safety_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            tool = registry.get("safe_file_write")

            manifest = tool.spec.manifest()

            self.assertIsInstance(manifest, ToolManifest)
            self.assertEqual(manifest.name, "safe_file_write")
            self.assertEqual(manifest.permissions, (Permission.WRITE_FILES,))
            self.assertEqual(manifest.risk, RiskLevel.MEDIUM)
            self.assertEqual(manifest.reversibility, Reversibility.REVERSIBLE)
            self.assertEqual(manifest.filesystem_scope, "workspace")
            self.assertFalse(manifest.network_access)
            self.assertFalse(manifest.secrets_allowed)
            self.assertEqual(manifest.input_schema["required"], ["path", "content"])

    def test_validate_json_schema_reports_required_and_type_issues(self) -> None:
        schema = {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "additionalProperties": False,
        }

        issues = validate_json_schema({"path": 3, "unexpected": True}, schema)

        reasons = {issue["reason"] for issue in issues}
        self.assertIn("required_missing", reasons)
        self.assertIn("type_mismatch", reasons)
        self.assertIn("additional_property_not_allowed", reasons)

    def test_safe_file_write_schema_failure_blocks_before_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                approval_gate=ApprovalGate(lambda step: True),
            )
            goal = Goal(
                description="Reject malformed file write",
                success_criteria=["schema failure is recorded"],
                stop_conditions=["failed"],
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "x.txt"}, "test schema boundary")])

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.FAILED)
            self.assertFalse((Path(tmp) / "x.txt").exists())
            failures = [event for event in agent.audit_log.events if event.event_type == "step.dry_run_failed"]
            self.assertEqual(failures[0].payload["error_type"], "SchemaValidationFailed")
            self.assertEqual(failures[0].payload["data"]["schema_issues"][0]["reason"], "required_missing")

            dry_run = registry.get("safe_file_write").dry_run({"path": "x.txt"}, agent.state)
            self.assertIsInstance(dry_run.error, SchemaValidationFailed)

    def test_tool_output_schema_failure_rolls_back_before_state_write(self) -> None:
        registry = ToolRegistry()
        tool = BadOutputSchemaTool()
        registry.register(tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Reject malformed tool output",
            success_criteria=["bad output does not enter state"],
            stop_conditions=["rolled back"],
        )
        plan = agent.build_plan(goal, [ActionStep("bad_output_schema", {}, "test output schema boundary")])

        result = agent.run(plan)

        self.assertTrue(tool.executed)
        self.assertTrue(tool.rolled_back)
        self.assertEqual(result.steps[0].status, StepStatus.ROLLED_BACK)
        self.assertNotIn("external_id", agent.state.facts)
        self.assertNotIn("unexpected", agent.state.facts)
        failures = [event for event in agent.audit_log.events if event.event_type == "step.output_schema_failed"]
        self.assertEqual(failures[0].payload["error_type"], "SchemaValidationFailed")
        reasons = {issue["reason"] for issue in failures[0].payload["data"]["schema_issues"]}
        self.assertIn("type_mismatch", reasons)
        self.assertIn("additional_property_not_allowed", reasons)

    def test_memory_lifecycle_filters_expired_items(self) -> None:
        memory = MemoryStore()
        memory.remember("preference", "short answers", confidence=0.9, provenance="user", ttl=0.01)

        self.assertEqual(len(memory.recall("preference")), 1)

        removed = memory.purge_expired(now=time.time() + 1.0)

        self.assertEqual(removed, 1)
        self.assertEqual(memory.recall("preference"), [])

    def test_memory_lifecycle_filters_scope_and_type(self) -> None:
        memory = MemoryStore()
        memory.remember(
            "deploy",
            "run release script",
            confidence=0.8,
            provenance="docs",
            memory_type=MemoryType.PROCEDURE,
            scope="project-a",
        )
        memory.remember(
            "deploy",
            "do not deploy Fridays",
            confidence=0.7,
            provenance="policy",
            memory_type=MemoryType.POLICY,
            scope="project-b",
        )

        procedures = memory.recall("deploy", scope="project-a", memory_type=MemoryType.PROCEDURE)

        self.assertEqual(len(procedures), 1)
        self.assertEqual(procedures[0]["memory_type"], "procedure")
        self.assertEqual(procedures[0]["scope"], "project-a")

    def test_memory_lifecycle_persists_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory = MemoryStore(path)
            memory.remember(
                "failure",
                "tool timed out",
                confidence=0.6,
                provenance="runtime",
                memory_type=MemoryType.FAILURE,
                sensitivity=MemorySensitivity.INTERNAL,
                source="watchdog",
                conflicts_with=("old-failure",),
                supersedes=("timeout-v1",),
            )

            loaded = MemoryStore(path).recall("failure")

            self.assertEqual(loaded[0]["memory_type"], "failure")
            self.assertEqual(loaded[0]["sensitivity"], "internal")
            self.assertEqual(loaded[0]["source"], "watchdog")
            self.assertEqual(tuple(loaded[0]["conflicts_with"]), ("old-failure",))
            self.assertEqual(tuple(loaded[0]["supersedes"]), ("timeout-v1",))

    def test_memory_secret_boundary_rejects_secret_values(self) -> None:
        memory = MemoryStore()

        with self.assertRaises(SecretBoundaryViolation):
            memory.remember(
                "github_token",
                "ghp_secret_value",
                confidence=1.0,
                provenance="user",
                sensitivity=MemorySensitivity.SECRET,
                memory_type=MemoryType.FACT,
            )

    def test_memory_secret_boundary_allows_secret_reference(self) -> None:
        memory = MemoryStore()

        record = memory.remember(
            "github_token",
            "secret://github_token_write_repo_scope",
            confidence=1.0,
            provenance="secret-manager",
            sensitivity=MemorySensitivity.SECRET,
            memory_type=MemoryType.SECRET_REF,
        )

        self.assertEqual(record.memory_type, MemoryType.SECRET_REF)
        recalled = memory.recall("github_token")
        self.assertEqual(recalled[0]["sensitivity"], "secret")
        self.assertEqual(recalled[0]["memory_type"], "secret_ref")

    def test_audit_log_records_sequence_and_hash_chain(self) -> None:
        audit = AuditLog()

        first = audit.record("test.first", "first event", value=1)
        second = audit.record("test.second", "second event", value=2)

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(first.previous_hash, AuditLog.GENESIS_HASH)
        self.assertEqual(second.previous_hash, first.event_hash)
        self.assertNotEqual(first.event_hash, second.event_hash)
        self.assertTrue(audit.verify_integrity().ok)

    def test_audit_log_detects_in_memory_tampering(self) -> None:
        audit = AuditLog()
        audit.record("test.first", "first event", value=1)
        audit.record("test.second", "second event", value=2)

        audit.events[0].payload["value"] = "tampered"
        result = audit.verify_integrity()

        self.assertFalse(result.ok)
        self.assertEqual(result.data["issues"][0]["reason"], "event_hash_mismatch")

    def test_audit_log_detects_persisted_log_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            audit = AuditLog(path)
            audit.record("test.first", "first event", value=1)
            audit.record("test.second", "second event", value=2)
            self.assertTrue(audit.verify_integrity().ok)

            lines = path.read_text(encoding="utf-8").splitlines()
            first_record = json.loads(lines[0])
            first_record["payload"]["value"] = "tampered"
            lines[0] = json.dumps(first_record)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            result = audit.verify_integrity()

            self.assertFalse(result.ok)
            self.assertEqual(result.data["issues"][0]["reason"], "event_hash_mismatch")

    def test_replay_reconstructs_world_state_from_audit_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_path = str((Path(tmp) / "x.txt").resolve())
            registry = default_registry(Path(tmp))
            causal = CausalWorldModel(
                [
                    CausalHypothesis(
                        action_name="safe_file_write",
                        affected_variables=["file_written"],
                        rationale="Writing a file updates file_written",
                        confidence=0.9,
                    )
                ]
            )
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                causal_model=causal,
                approval_gate=ApprovalGate(lambda step: True),
            )
            goal = Goal(
                description="Write a file",
                success_criteria=["file exists"],
                stop_conditions=["verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {"path": "x.txt", "content": "x", "file_written": expected_path},
                        "test",
                    )
                ],
            )

            agent.run(plan)
            result = replay_audit_log(agent.audit_log)

            self.assertIsInstance(result, ReplayResult)
            self.assertTrue(result.ok)
            self.assertEqual(result.state.facts, agent.state.facts)
            self.assertEqual(result.state.facts["file_written"], expected_path)
            self.assertEqual(result.state.trust["file_written"], TrustLevel.VERIFIED)
            self.assertEqual(result.applied_events, 1)

    def test_replay_refuses_tampered_audit_log_by_default(self) -> None:
        audit = AuditLog()
        audit.record("step.executed", "executed", observed={"last_echo": "hello"})
        records = audit.records()
        records[0]["payload"]["observed"]["last_echo"] = "tampered"

        result = AuditReplayer().replay_records(records)

        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0]["reason"], "event_hash_mismatch")

    def test_replay_can_skip_integrity_verification_for_debugging(self) -> None:
        audit = AuditLog()
        audit.record("step.executed", "executed", observed={"last_echo": "hello"})
        records = audit.records()
        records[0]["payload"]["observed"]["last_echo"] = "debug-value"

        result = AuditReplayer().replay_records(records, verify_integrity=False)

        self.assertTrue(result.ok)
        self.assertEqual(result.state.facts["last_echo"], "debug-value")

    def test_file_write_requires_permission_or_human_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(registry=registry, policy=PolicyEngine())
            goal = Goal(
                description="Write a file",
                success_criteria=["file exists"],
                stop_conditions=["blocked or verified"],
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "test")])

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
            self.assertFalse((Path(tmp) / "x.txt").exists())

    def test_approved_file_write_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_path = str((Path(tmp) / "x.txt").resolve())
            registry = default_registry(Path(tmp))
            causal = CausalWorldModel(
                [
                    CausalHypothesis(
                        action_name="safe_file_write",
                        affected_variables=["file_written"],
                        rationale="Writing a file updates file_written",
                        confidence=0.9,
                    )
                ]
            )
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                causal_model=causal,
                approval_gate=ApprovalGate(lambda step: True),
            )
            goal = Goal(
                description="Write a file",
                success_criteria=["file exists"],
                stop_conditions=["verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {"path": "x.txt", "content": "x", "file_written": expected_path},
                        "test",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
            self.assertEqual(agent.state.trust["file_written"], TrustLevel.VERIFIED)
            self.assertEqual((Path(tmp) / "x.txt").read_text(encoding="utf-8"), "x")

    def test_workspace_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                approval_gate=ApprovalGate(lambda step: True),
            )
            goal = Goal(
                description="Attempt escaping workspace",
                success_criteria=["escape is rejected"],
                stop_conditions=["failed"],
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "../x.txt", "content": "x"}, "test")])

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.FAILED)

    def test_workspace_escape_records_typed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                approval_gate=ApprovalGate(lambda step: True),
            )
            goal = Goal(
                description="Attempt escaping workspace",
                success_criteria=["escape is rejected"],
                stop_conditions=["failed"],
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "../x.txt", "content": "x"}, "test")])

            result = agent.run(plan)

            failures = [event for event in agent.audit_log.events if event.event_type == "step.dry_run_failed"]
            self.assertEqual(result.steps[0].status, StepStatus.FAILED)
            self.assertEqual(failures[0].payload["error_type"], "WorkspaceEscapeBlocked")

            tool = registry.get("safe_file_write")
            dry_run = tool.dry_run({"path": "../x.txt", "content": "x"}, agent.state)
            self.assertIsInstance(dry_run.error, WorkspaceEscapeBlocked)

    def test_dry_run_failure_prevents_execute(self) -> None:
        registry = ToolRegistry()
        tool = DryRunFailingTool()
        registry.register(tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Do not execute after failed dry-run",
            success_criteria=["execute is not called"],
            stop_conditions=["failed"],
        )
        plan = agent.build_plan(goal, [ActionStep("dry_run_fails", {}, "test dry-run boundary")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.FAILED)
        self.assertFalse(tool.executed)
        failures = [event for event in agent.audit_log.events if event.event_type == "step.dry_run_failed"]
        self.assertEqual(failures[0].payload["error_type"], "DryRunFailed")

    def test_unknown_tool_never_executes(self) -> None:
        agent = AgentKernel(registry=ToolRegistry(), policy=PolicyEngine())
        goal = Goal(
            description="Unknown tool is rejected",
            success_criteria=["tool cannot execute"],
            stop_conditions=["exception"],
        )
        plan = agent.build_plan(goal, [ActionStep("missing_tool", {}, "test unknown tool boundary")])

        with self.assertRaises(KeyError):
            agent.run(plan)

    def test_high_risk_tool_requires_human_approval(self) -> None:
        registry = ToolRegistry()
        tool = HighRiskTool()
        registry.register(tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="High risk action blocks without approval",
            success_criteria=["tool is blocked"],
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(goal, [ActionStep("high_risk", {}, "test high-risk boundary")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        self.assertFalse(tool.executed)
        blocked = [event for event in agent.audit_log.events if event.event_type == "step.blocked"]
        self.assertEqual(blocked[0].payload["error_type"], "PolicyDenied")

    def test_irreversible_consequential_tool_requires_human_even_with_permission(self) -> None:
        registry = ToolRegistry()
        tool = IrreversibleWriteTool()
        registry.register(tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}))
        goal = Goal(
            description="Irreversible writes require approval",
            success_criteria=["tool is blocked"],
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(goal, [ActionStep("irreversible_write", {}, "test irreversible boundary")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        self.assertFalse(tool.executed)
        self.assertEqual(result.steps[0].reversibility, Reversibility.IRREVERSIBLE)
        blocked = [event for event in agent.audit_log.events if event.event_type == "step.blocked"]
        self.assertEqual(blocked[0].payload["reversibility"], "irreversible")
        self.assertEqual(blocked[0].payload["compensation_strategy"], "manual")

    def test_compensatable_consequential_tool_requires_human_even_with_permission(self) -> None:
        registry = ToolRegistry()
        tool = CompensatableWriteTool()
        registry.register(tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}))
        goal = Goal(
            description="Compensatable writes require approval",
            success_criteria=["tool is blocked"],
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(goal, [ActionStep("compensatable_write", {}, "test compensatable boundary")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        self.assertFalse(tool.executed)
        self.assertEqual(result.steps[0].reversibility, Reversibility.COMPENSATABLE)
        blocked = [event for event in agent.audit_log.events if event.event_type == "step.blocked"]
        self.assertEqual(blocked[0].payload["reversibility"], "compensatable")
        self.assertEqual(blocked[0].payload["compensation_strategy"], "compensate")

    def test_developer_local_profile_allows_reversible_workspace_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_path = str((Path(tmp) / "x.txt").resolve())
            registry = default_registry(Path(tmp))
            causal = CausalWorldModel(
                [
                    CausalHypothesis(
                        action_name="safe_file_write",
                        affected_variables=["file_written"],
                        rationale="Writing a file updates file_written",
                        confidence=0.9,
                    )
                ]
            )
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine.from_profile("developer_local"),
                causal_model=causal,
            )
            goal = Goal(
                description="Developer local write",
                success_criteria=["file exists"],
                stop_conditions=["verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {"path": "x.txt", "content": "x", "file_written": expected_path},
                        "test developer profile",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
            self.assertEqual((Path(tmp) / "x.txt").read_text(encoding="utf-8"), "x")

    def test_developer_local_profile_denies_network(self) -> None:
        registry = ToolRegistry()
        tool = NetworkTool()
        registry.register(tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine.from_profile("developer_local"))
        goal = Goal(
            description="Network is denied locally",
            success_criteria=["network does not execute"],
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(goal, [ActionStep("network_fetch", {}, "test denied permission")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        self.assertFalse(tool.executed)
        blocked = [event for event in agent.audit_log.events if event.event_type == "step.blocked"]
        self.assertEqual(blocked[0].payload["decision"], "denied")

    def test_production_profile_requires_human_for_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(registry=registry, policy=PolicyEngine.from_profile("production"))
            goal = Goal(
                description="Production write requires human",
                success_criteria=["file is blocked"],
                stop_conditions=["blocked"],
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "test production profile")])

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
            self.assertFalse((Path(tmp) / "x.txt").exists())
            blocked = [event for event in agent.audit_log.events if event.event_type == "step.blocked"]
            self.assertEqual(blocked[0].payload["decision"], "denied")

    def test_rollback_failure_requires_manual_recovery(self) -> None:
        registry = ToolRegistry()
        rollback_tool = RollbackFailingTool()
        dry_run_tool = DryRunFailingTool()
        registry.register(rollback_tool)
        registry.register(dry_run_tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Rollback failure enters manual recovery",
            success_criteria=["manual recovery is recorded"],
            stop_conditions=["failed"],
        )
        plan = agent.build_plan(
            goal,
            [
                ActionStep("rollback_fails", {}, "create a rollback token"),
                ActionStep("dry_run_fails", {}, "trigger rollback"),
            ],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.FAILED)
        self.assertEqual(result.steps[1].status, StepStatus.FAILED)
        self.assertFalse(dry_run_tool.executed)
        event_types = [event.event_type for event in agent.audit_log.events]
        self.assertIn("rollback_attempted", event_types)
        self.assertIn("rollback_failed", event_types)
        self.assertIn("manual_recovery_required", event_types)
        failures = [event for event in agent.audit_log.events if event.event_type == "rollback_failed"]
        self.assertEqual(failures[0].payload["error_type"], "RollbackFailed")
        manual_recovery = [event for event in agent.audit_log.events if event.event_type == "manual_recovery_required"]
        self.assertEqual(manual_recovery[0].payload["rollback_token"], {"resource": "rollback-failure-test"})

    def test_mixed_rollback_results_record_partial_completion(self) -> None:
        registry = ToolRegistry()
        failing_tool = RollbackFailingTool()
        succeeding_tool = RollbackSucceedingTool()
        dry_run_tool = DryRunFailingTool()
        registry.register(failing_tool)
        registry.register(succeeding_tool)
        registry.register(dry_run_tool)
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Partial rollback is explicit",
            success_criteria=["partial rollback is recorded"],
            stop_conditions=["failed"],
        )
        plan = agent.build_plan(
            goal,
            [
                ActionStep("rollback_fails", {}, "create failing rollback token"),
                ActionStep("rollback_succeeds", {}, "create successful rollback token"),
                ActionStep("dry_run_fails", {}, "trigger rollback"),
            ],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.FAILED)
        self.assertEqual(result.steps[1].status, StepStatus.ROLLED_BACK)
        self.assertEqual(result.steps[2].status, StepStatus.FAILED)
        partial = [event for event in agent.audit_log.events if event.event_type == "rollback_partially_completed"]
        self.assertEqual(partial[0].payload["succeeded"], 1)
        self.assertEqual(partial[0].payload["failed"], 1)

    def test_cli_demo_requires_auto_approval_for_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            agent = build_demo_agent(workspace, auto_approve=False)
            goal = Goal(
                description="Demo write",
                success_criteria=["file exists"],
                stop_conditions=["blocked or verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {
                            "path": "hello.txt",
                            "content": "hello",
                            "file_written": str((workspace / "hello.txt").resolve()),
                        },
                        "demo",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
            self.assertFalse((workspace / "hello.txt").exists())

    def test_cli_demo_auto_approval_allows_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            agent = build_demo_agent(workspace, auto_approve=True)
            goal = Goal(
                description="Demo write",
                success_criteria=["file exists"],
                stop_conditions=["blocked or verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {
                            "path": "hello.txt",
                            "content": "hello",
                            "file_written": str((workspace / "hello.txt").resolve()),
                        },
                        "demo",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
            self.assertEqual((workspace / "hello.txt").read_text(encoding="utf-8"), "hello")

    def test_planner_selects_first_satisfactory_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                planner_config=PlannerConfig(max_risk=RiskLevel.MEDIUM, max_cost=5.0, min_benefit=0.5),
            )
            goal = Goal(
                description="Choose a plan",
                success_criteria=["a candidate is selected"],
                stop_conditions=["selected"],
            )
            proposals = [
                PlanProposal(
                    steps=[ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "too costly")],
                    rationale="Too costly",
                    estimated_cost=10.0,
                    expected_benefit=1.0,
                ),
                PlanProposal(
                    steps=[ActionStep("echo", {"message": "hello"}, "low cost")],
                    rationale="Satisfactory",
                    estimated_cost=1.0,
                    expected_benefit=0.75,
                ),
                PlanProposal(
                    steps=[ActionStep("echo", {"message": "later"}, "also acceptable")],
                    rationale="Also satisfactory",
                    estimated_cost=1.0,
                    expected_benefit=1.0,
                ),
            ]

            result = agent.plan(goal, proposals)

            self.assertIs(result.selected, result.candidates[1])
            self.assertFalse(result.candidates[0].score.satisfies)
            self.assertTrue(result.candidates[1].score.satisfies)

    def test_planner_returns_no_selection_without_satisfactory_candidate(self) -> None:
        registry = default_registry()
        agent = AgentKernel(
            registry=registry,
            policy=PolicyEngine(),
            planner_config=PlannerConfig(max_risk=RiskLevel.LOW, max_cost=1.0, min_benefit=2.0),
        )
        goal = Goal(
            description="Choose a plan",
            success_criteria=["a candidate is selected"],
            stop_conditions=["selected"],
        )
        proposals = [
            PlanProposal(
                steps=[ActionStep("echo", {"message": "hello"}, "not enough benefit")],
                rationale="Low benefit",
                estimated_cost=0.5,
                expected_benefit=1.0,
            )
        ]

        result = agent.plan(goal, proposals)

        self.assertIsNone(result.selected)
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.candidates[0].score.satisfies)

    def test_causal_graph_reports_action_consequences_and_counterfactuals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_path = str((Path(tmp) / "x.txt").resolve())
            registry = default_registry(Path(tmp))
            causal = CausalGraph(
                [
                    CausalHypothesis(
                        action_name="safe_file_write",
                        affected_variables=["file_written"],
                        rationale="Writing a file updates file_written",
                        confidence=0.9,
                    )
                ]
            )
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                causal_model=causal,
            )
            goal = Goal(
                description="Write a file",
                success_criteria=["file exists"],
                stop_conditions=["verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {"path": "x.txt", "content": "x", "file_written": expected_path},
                        "test",
                    )
                ],
            )

            result = agent.run(plan)
            step = result.steps[0]

            self.assertEqual(step.status, StepStatus.VERIFIED)
            self.assertEqual(step.predictions[0].variable, "file_written")
            self.assertIsNotNone(step.counterfactual_report)
            self.assertEqual(step.counterfactual_report.action_consequences[0].expected_after, expected_path)
            self.assertIsNone(step.counterfactual_report.no_action_consequences[0].expected_after)

    def test_causal_graph_verification_reports_consequence_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            causal = CausalGraph(
                [
                    CausalHypothesis(
                        action_name="safe_file_write",
                        affected_variables=["file_written"],
                        rationale="Writing a file updates file_written",
                        confidence=0.9,
                    )
                ]
            )
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                causal_model=causal,
            )
            goal = Goal(
                description="Write a file",
                success_criteria=["file exists"],
                stop_conditions=["failed"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {"path": "x.txt", "content": "x", "file_written": "wrong-path"},
                        "test",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.ROLLED_BACK)
            self.assertEqual(agent.state.trust["file_written"], TrustLevel.TOOL_REPORTED)
            failures = [event for event in agent.audit_log.events if event.event_type == "step.verification_failed"]
            self.assertEqual(failures[0].payload["data"]["mismatches"][0]["reason"], "consequence_mismatch")


if __name__ == "__main__":
    unittest.main()
