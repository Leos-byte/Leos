from __future__ import annotations

import unittest

from leos_agent import (
    ActionStep,
    AgentKernel,
    AgentLoop,
    AgentLoopConfig,
    EchoTool,
    Goal,
    PlanProposal,
    PolicyEngine,
    ToolRegistry,
)
from leos_agent.replanning import FailureType


class _RepairProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.repairs = 0

    def propose(self, goal, state, registry):
        self.calls += 1
        return [PlanProposal([ActionStep("echo", {}, "missing message")], "bad")]

    def propose_repair(self, context, goal, state, registry):
        self.repairs += 1
        return [PlanProposal([ActionStep("echo", {"message": "fixed"}, "repair")], "repair")]


class _UnknownRepairProvider(_RepairProvider):
    def propose(self, goal, state, registry):
        self.calls += 1
        return [PlanProposal([ActionStep("missing", {}, "unknown")], "bad")]


class _RepeatedFailureProvider(_RepairProvider):
    def propose_repair(self, context, goal, state, registry):
        self.repairs += 1
        return [PlanProposal([ActionStep("echo", {}, "still missing message")], "bad repair")]


class ReplanningTests(unittest.TestCase):
    def _kernel(self) -> AgentKernel:
        registry = ToolRegistry()
        registry.register(EchoTool())
        return AgentKernel(registry, PolicyEngine())

    def test_dry_run_failure_triggers_replan(self) -> None:
        provider = _RepairProvider()
        loop = AgentLoop(
            self._kernel(),
            provider,
            config=AgentLoopConfig(max_iterations=3, max_replans=1),
        )

        result = loop.run(Goal("repair", ["do the task"], stop_conditions=["done"]))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.stop_reason, "goal_succeeded")
        self.assertEqual(provider.repairs, 1)
        self.assertEqual(result.failure_analyses[0].failure_type, FailureType.DRY_RUN_FAILED)

    def test_unknown_tool_can_replan_to_known_tool(self) -> None:
        provider = _UnknownRepairProvider()
        result = AgentLoop(
            self._kernel(),
            provider,
            config=AgentLoopConfig(max_iterations=3, max_replans=1),
        ).run(Goal("repair", ["do the task"], stop_conditions=["done"]))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.failure_analyses[0].failure_type, FailureType.UNKNOWN_TOOL)

    def test_repeated_failure_stops_after_budget(self) -> None:
        provider = _RepeatedFailureProvider()
        result = AgentLoop(
            self._kernel(),
            provider,
            config=AgentLoopConfig(max_iterations=3, max_replans=1),
        ).run(Goal("repair", ["do the task"], stop_conditions=["done"]))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.stop_reason, "goal_failed")

    def test_tool_call_budget_stops_before_second_plan(self) -> None:
        provider = _RepairProvider()
        result = AgentLoop(
            self._kernel(),
            provider,
            config=AgentLoopConfig(max_iterations=3, max_replans=1, max_tool_calls=1),
        ).run(Goal("repair", ["do the task"], stop_conditions=["done"]))

        self.assertEqual(result.stop_reason, "tool_call_budget_exceeded")


if __name__ == "__main__":
    unittest.main()
