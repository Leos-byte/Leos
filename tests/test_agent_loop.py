from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from leos_agent import (
    ActionStep,
    AgentKernel,
    AgentLoop,
    AgentLoopConfig,
    ApprovalGate,
    AuditLog,
    CausalGraph,
    DeterministicProposalProvider,
    Goal,
    PlannerConfig,
    PlanProposal,
    PolicyEngine,
    RiskLevel,
    ToolResult,
    ToolSpec,
)
from leos_agent.state import WorldState
from leos_agent.tools import EchoTool, ToolRegistry


class SpyEchoTool(EchoTool):
    def __init__(self) -> None:
        self.executed = False

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.executed = True
        return super().execute(arguments, state)


class BlockedTool:
    spec = ToolSpec(name="blocked", description="blocked", permissions=(), default_risk=RiskLevel.HIGH)

    def __init__(self) -> None:
        self.executed = False

    def dry_run(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "dry")

    def execute(self, arguments: Mapping[str, Any], state: WorldState) -> ToolResult:
        self.executed = True
        return ToolResult(True, "executed", observed_state_delta={"blocked": False})

    def rollback(self, token: Mapping[str, Any], state: WorldState) -> ToolResult:
        return ToolResult(True, "rollback")


class AgentLoopTests(unittest.TestCase):
    def _kernel(self, registry: ToolRegistry, audit: AuditLog | None = None) -> AgentKernel:
        return AgentKernel(
            registry=registry,
            policy=PolicyEngine(max_auto_risk=RiskLevel.HIGH),
            causal_model=CausalGraph(),
            audit_log=audit or AuditLog(),
            approval_gate=ApprovalGate(lambda step: False),
            planner_config=PlannerConfig(max_risk=RiskLevel.HIGH),
        )

    def test_loop_executes_echo_goal_and_succeeds(self) -> None:
        registry = ToolRegistry()
        tool = SpyEchoTool()
        registry.register(tool)
        kernel = self._kernel(registry)
        goal = Goal("echo", ["last_echo observed"], stop_conditions=["done"])
        proposal = PlanProposal([ActionStep("echo", {"message": "hi"}, "echo")], "echo")

        result = AgentLoop(kernel, DeterministicProposalProvider([proposal])).run(goal)

        self.assertTrue(result.succeeded)
        self.assertTrue(tool.executed)
        self.assertEqual(result.stop_reason, "goal_succeeded")

    def test_loop_stops_safely_without_proposals(self) -> None:
        kernel = self._kernel(ToolRegistry())
        goal = Goal("none", ["done"], stop_conditions=["stop"])

        result = AgentLoop(kernel, DeterministicProposalProvider([])).run(goal)

        self.assertEqual(result.stop_reason, "no_plan")
        self.assertFalse(result.succeeded)

    def test_loop_does_not_exceed_max_iterations(self) -> None:
        kernel = self._kernel(ToolRegistry())
        goal = Goal("echo", ["done"], stop_conditions=["stop"])

        result = AgentLoop(
            kernel,
            DeterministicProposalProvider([[], [], []]),
            config=AgentLoopConfig(max_iterations=2),
        ).run(goal)

        self.assertLessEqual(result.iterations, 2)

    def test_blocked_step_stops_loop(self) -> None:
        registry = ToolRegistry()
        tool = BlockedTool()
        registry.register(tool)
        kernel = self._kernel(registry)
        goal = Goal("blocked", ["done"], stop_conditions=["stop"])
        proposal = PlanProposal([ActionStep("blocked", {}, "blocked")], "blocked")

        result = AgentLoop(kernel, DeterministicProposalProvider([proposal])).run(goal)

        self.assertEqual(result.stop_reason, "goal_blocked")
        self.assertFalse(tool.executed)

    def test_loop_writes_required_audit_events(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        audit = AuditLog()
        kernel = self._kernel(registry, audit)
        goal = Goal("echo", ["done"], stop_conditions=["stop"])
        proposal = PlanProposal([ActionStep("echo", {"message": "hi"}, "echo")], "echo")

        AgentLoop(kernel, DeterministicProposalProvider([proposal])).run(goal)

        event_types = {event.event_type for event in audit.events}
        for expected in {
            "loop.started",
            "loop.iteration_started",
            "loop.plan_selected",
            "loop.plan_executed",
            "loop.goal_progress_checked",
            "loop.memory_updated",
            "loop.finished",
        }:
            self.assertIn(expected, event_types)


if __name__ == "__main__":
    unittest.main()
