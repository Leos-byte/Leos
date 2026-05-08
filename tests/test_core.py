from __future__ import annotations

import tempfile
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
    Permission,
    PlanProposal,
    PlannerConfig,
    PolicyEngine,
    PolicyProfile,
    RiskLevel,
    AuditReplayer,
    ReplayResult,
    Reversibility,
    SchemaValidationFailed,
    StepStatus,
    ToolManifest,
    TrustLevel,
    default_registry,
    replay_audit_log,
    validate_json_schema,
)
from leos_agent.cli import build_demo_agent
from leos_agent.errors import RollbackFailed, WorkspaceEscapeBlocked
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
