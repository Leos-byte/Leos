from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from leos_agent import (
    ActionStep,
    AgentKernel,
    AgentLoop,
    AuditLog,
    CausalGraph,
    DeterministicProposalProvider,
    Goal,
    PlanProposal,
    PolicyEngine,
    RiskLevel,
    ToolResult,
    ToolSpec,
)
from leos_agent.runtime_store import InMemoryRuntimeStore, RuntimeStoreError
from leos_agent.state import WorldState
from leos_agent.tools import Secret, ToolRegistry


class EvidenceTool:
    spec = ToolSpec(name="evidence", description="evidence", permissions=(), default_risk=RiskLevel.LOW)

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "execute", observed_state_delta={"tests_ok": True})

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "rollback")


class FailingRuntimeStore(InMemoryRuntimeStore):
    def save_goal(self, goal: Goal) -> None:
        raise RuntimeStoreError("store failed")


class AgentLoopRuntimeStoreTests(unittest.TestCase):
    def _kernel(self, audit: AuditLog | None = None) -> AgentKernel:
        registry = ToolRegistry()
        registry.register(EvidenceTool())
        return AgentKernel(
            registry=registry,
            policy=PolicyEngine(max_auto_risk=RiskLevel.HIGH),
            causal_model=CausalGraph(),
            audit_log=audit or AuditLog(),
        )

    def _proposal(self) -> PlanProposal:
        return PlanProposal([ActionStep("evidence", {}, "record evidence")], "record")

    def test_agent_loop_saves_goal_plan_events_and_checkpoint(self) -> None:
        store = InMemoryRuntimeStore()
        kernel = self._kernel()
        goal = Goal("verify", ["tests pass"], stop_conditions=["stop"])

        result = AgentLoop(kernel, DeterministicProposalProvider([self._proposal()]), runtime_store=store).run(goal)

        self.assertTrue(result.succeeded)
        self.assertIsNotNone(store.load_goal(goal.goal_id))
        self.assertEqual(len(store.plans), 1)
        self.assertEqual(len(store.events), 1)
        checkpoint = store.load_checkpoint(f"agent_loop:{result.goal.goal_id}:final")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["stop_reason"], "goal_succeeded")

    def test_store_failure_records_audit_and_does_not_break_loop(self) -> None:
        audit = AuditLog()
        kernel = self._kernel(audit)
        goal = Goal("verify", ["tests pass"], stop_conditions=["stop"])

        result = AgentLoop(
            kernel,
            DeterministicProposalProvider([self._proposal()]),
            runtime_store=FailingRuntimeStore(),
        ).run(goal)

        self.assertTrue(result.succeeded)
        self.assertIn("loop.runtime_store_failed", {event.event_type for event in audit.events})

    def test_checkpoint_does_not_contain_secret(self) -> None:
        store = InMemoryRuntimeStore()
        kernel = self._kernel()
        goal = Goal("verify", ["tests pass"], stop_conditions=["stop"])

        AgentLoop(kernel, DeterministicProposalProvider([self._proposal()]), runtime_store=store).run(goal)

        self.assertNotIn("secret-value", repr(store.checkpoints))
        with self.assertRaises(RuntimeStoreError):
            store.save_checkpoint("bad", {"token": Secret("secret-value")})


if __name__ == "__main__":
    unittest.main()
