"""Red-team tests for policy bypass attempts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leos_agent.enums import Permission
from leos_agent.errors import PolicyConfigurationError
from leos_agent.goals import Goal
from leos_agent.kernel import AgentKernel
from leos_agent.plans import ActionStep
from leos_agent.policy import CapabilityGrant, PolicyEngine, PolicyRule
from leos_agent.tools import ToolRegistry, default_registry


class PolicyBypassRedTeamTests(unittest.TestCase):
    def test_policy_rule_cannot_directly_approve(self) -> None:
        with self.assertRaises(PolicyConfigurationError):
            PolicyRule(
                name="auto_approve",
                when={"tool": "echo"},
                decision="approved",
            )

    def test_deny_beats_granted(self) -> None:
        policy = PolicyEngine(
            granted_permissions={Permission.WRITE_FILES},
            deny_permissions={Permission.WRITE_FILES},
        )
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(registry=registry, policy=policy)
            goal = Goal(description="t", success_criteria=["blocked"], stop_conditions=["blocked"])
            plan = agent.build_plan(
                goal,
                [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "test")],
            )
            result = agent.run(plan)
            self.assertNotEqual(result.steps[0].status.value, "verified")

    def test_high_risk_blocked_under_production(self) -> None:
        from leos_agent.enums import RiskLevel
        from leos_agent.tools import ToolResult, ToolSpec

        registry = ToolRegistry()

        class _HRT:
            spec = ToolSpec(
                name="high_risk_tool",
                description="h",
                permissions=(),
                default_risk=RiskLevel.HIGH,
            )

            def dry_run(self, *a, **kw):
                return ToolResult(True, "ok")

            def execute(self, *a, **kw):
                return ToolResult(True, "ok")

            def rollback(self, *a, **kw):
                return ToolResult(True, "ok")

        registry.register(_HRT())
        policy = PolicyEngine.from_profile("production")
        agent = AgentKernel(registry=registry, policy=policy)
        goal = Goal(description="t", success_criteria=["blocked"], stop_conditions=["blocked"])
        plan = agent.build_plan(goal, [ActionStep("high_risk_tool", {}, "test")])
        result = agent.run(plan)
        self.assertNotEqual(result.steps[0].status.value, "verified")

    def test_expired_grant_not_usable(self) -> None:
        import time

        grant = CapabilityGrant(
            principal="alice",
            permissions=["write_files"],
            tools=["safe_file_write"],
            expires_at=time.time() - 3600,
        )
        self.assertFalse(grant.applies_to("alice", "safe_file_write", now=time.time()))

    def test_max_uses_grant_exhausted(self) -> None:
        grant = CapabilityGrant(principal="bob", permissions=["write_files"], max_uses=1)
        self.assertTrue(grant.applies_to("bob", "safe_file_write"))
        grant.record_use()
        self.assertFalse(grant.applies_to("bob", "safe_file_write"))

    def test_decision_result_contains_reason(self) -> None:
        from leos_agent.plans import ActionStep

        engine = PolicyEngine(granted_permissions={Permission.READ_FILES})
        result = engine.decide(ActionStep("echo", {"message": "hi"}, "test"))
        self.assertEqual(result.decision.value, "approved")
        self.assertTrue(len(result.reason) > 0)

    def test_decision_result_denied_has_reason(self) -> None:
        from leos_agent.plans import ActionStep

        engine = PolicyEngine(deny_permissions={Permission.WRITE_FILES})
        result = engine.decide(
            ActionStep(
                "safe_file_write", {"path": "x", "content": "x"}, "test", required_permissions=(Permission.WRITE_FILES,)
            )
        )
        self.assertEqual(result.decision.value, "denied")

    def test_capability_grant_from_mapping_with_max_risk(self) -> None:
        from leos_agent.enums import RiskLevel

        grant = CapabilityGrant.from_mapping(
            {
                "principal": "alice",
                "permissions": ["write_files"],
                "max_risk": "medium",
            }
        )
        self.assertEqual(grant.max_risk, RiskLevel.MEDIUM)

    def test_policy_rule_permission_condition_matches(self) -> None:
        rule = PolicyRule(name="r", when={"permission": "write_files"}, decision="denied")
        step = ActionStep("echo", {"msg": "hi"}, "test", required_permissions=(Permission.WRITE_FILES,))
        self.assertTrue(rule.matches(step, profile_name="test"))

    def test_policy_rule_risk_at_least_condition(self) -> None:
        from leos_agent.enums import RiskLevel

        rule = PolicyRule(name="r", when={"risk_at_least": "high"}, decision="needs_human")
        step = ActionStep("echo", {"msg": "hi"}, "test")
        step.risk = RiskLevel.CRITICAL
        self.assertTrue(rule.matches(step, profile_name="test"))
        step.risk = RiskLevel.LOW
        self.assertFalse(rule.matches(step, profile_name="test"))

    def test_policy_rule_profile_condition(self) -> None:
        rule = PolicyRule(name="r", when={"profile": "production"}, decision="denied")
        step = ActionStep("echo", {"msg": "hi"}, "test")
        self.assertTrue(rule.matches(step, profile_name="production"))
        self.assertFalse(rule.matches(step, profile_name="developer_local"))

    def test_interactive_approval_gate_non_tty_denies(self) -> None:
        import sys

        from leos_agent.plans import ActionStep
        from leos_agent.policy import InteractiveApprovalGate

        old_stdout = sys.stdout
        try:
            import io

            sys.stdout = io.StringIO()
            gate = InteractiveApprovalGate(timeout_seconds=1.0)
            step = ActionStep("echo", {"msg": "hi"}, "test")
            self.assertEqual(gate.request(step).value, "denied")
        finally:
            sys.stdout = old_stdout


if __name__ == "__main__":
    unittest.main()
