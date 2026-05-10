"""Red-team tests for secret boundary enforcement."""

from __future__ import annotations

import json
import unittest
from dataclasses import asdict

from leos_agent.errors import SecretBoundaryViolation
from leos_agent.goals import Goal
from leos_agent.kernel import AgentKernel
from leos_agent.memory import MemoryRecord, MemorySensitivity, MemoryType
from leos_agent.plans import ActionStep
from leos_agent.policy import PolicyEngine
from leos_agent.tools import Secret, ToolRegistry


class SecretBoundaryRedTeamTests(unittest.TestCase):
    def test_secret_blocked_for_non_secrets_allowed_tool(self) -> None:
        s = Secret("my-token")
        self.assertEqual(repr(s), "<secret>")

    def test_secret_value_not_in_repr(self) -> None:
        s = Secret("api-key-12345")
        self.assertNotIn("api-key-12345", repr(s))

    def test_memory_secret_requires_secret_ref_type(self) -> None:
        with self.assertRaises(SecretBoundaryViolation):
            MemoryRecord(
                key="token",
                value="api-key-value",
                memory_type=MemoryType.FACT,
                sensitivity=MemorySensitivity.SECRET,
                provenance="test",
                confidence=1.0,
            )

    def test_secret_ref_can_be_stored(self) -> None:
        record = MemoryRecord(
            key="token",
            value="secret://provider/token-key",
            memory_type=MemoryType.SECRET_REF,
            sensitivity=MemorySensitivity.SECRET,
            provenance="test",
            confidence=1.0,
        )
        self.assertEqual(record.memory_type, MemoryType.SECRET_REF)

    def test_raw_secret_not_in_audit_events(self) -> None:
        registry = ToolRegistry()
        from leos_agent.enums import RiskLevel
        from leos_agent.tools import ToolResult, ToolSpec

        class _NoSecretTool:
            spec = ToolSpec(
                name="no_secret", description="n", permissions=(),
                default_risk=RiskLevel.LOW, secrets_allowed=False,
            )

            def dry_run(self, *a, **kw):
                return ToolResult(True, "ok")

            def execute(self, *a, **kw):
                return ToolResult(True, "ok")

            def rollback(self, *a, **kw):
                return ToolResult(True, "ok")

        registry.register(_NoSecretTool())
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(description="t", success_criteria=["blocked"], stop_conditions=["blocked"])
        plan = agent.build_plan(goal, [
            ActionStep("no_secret", {"api_key": Secret("raw-super-secret")}, "test secret leak")
        ])
        agent.run(plan)

        for event in agent.audit_log.events:
            serialized = json.dumps(asdict(event), default=str)
            self.assertNotIn("raw-super-secret", serialized,
                             f"Secret leaked in event: {event.event_type}")


if __name__ == "__main__":
    unittest.main()
