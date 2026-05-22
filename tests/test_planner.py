"""Planner regression tests."""

from __future__ import annotations

import unittest

from leos_agent.enums import CompensationStrategy, Permission, Reversibility, RiskLevel
from leos_agent.goals import Goal
from leos_agent.planner import Planner
from leos_agent.plans import ActionStep, PlanProposal, StateCondition
from leos_agent.policy import PolicyEngine
from leos_agent.tools import EchoTool, ToolRegistry


class PlannerStepCloneTests(unittest.TestCase):
    def test_generate_candidates_preserves_step_safety_metadata(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        planner = Planner(registry, PolicyEngine())
        original = ActionStep(
            "echo",
            {"message": "hi", "nested": {"value": ["original"]}},
            "preserve safety metadata",
            risk=RiskLevel.HIGH,
            reversibility=Reversibility.COMPENSATABLE,
            compensation_strategy=CompensationStrategy.COMPENSATE,
            rollback_reliability=0.42,
            required_permissions=(Permission.SEND_MESSAGE,),
            idempotency_key="goal-1-echo",
            preconditions=(StateCondition("ready"),),
            postconditions=(StateCondition("last_echo", "equals", "hi"),),
            invariants=(StateCondition("workspace_safe"),),
        )
        proposal = PlanProposal([original], "proposal")
        goal = Goal("Clone safely", ["candidate generated"])

        candidate = planner.generate_candidates(goal, [proposal])[0]
        cloned = candidate.plan.steps[0]

        self.assertEqual(cloned.step_id, original.step_id)
        self.assertEqual(cloned.idempotency_key, "goal-1-echo")
        self.assertEqual(cloned.preconditions, original.preconditions)
        self.assertEqual(cloned.postconditions, original.postconditions)
        self.assertEqual(cloned.invariants, original.invariants)
        self.assertEqual(cloned.reversibility, Reversibility.COMPENSATABLE)
        self.assertEqual(cloned.compensation_strategy, CompensationStrategy.COMPENSATE)
        self.assertEqual(cloned.rollback_reliability, 0.42)
        self.assertEqual(tuple(cloned.required_permissions), (Permission.SEND_MESSAGE,))

    def test_cloned_step_arguments_are_defensively_copied(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        planner = Planner(registry, PolicyEngine())
        original = ActionStep("echo", {"message": "hi", "nested": {"items": ["a"]}}, "copy args")
        proposal = PlanProposal([original], "proposal")
        goal = Goal("Clone safely", ["candidate generated"])

        cloned = planner.generate_candidates(goal, [proposal])[0].plan.steps[0]

        self.assertIsNot(cloned.arguments, original.arguments)
        self.assertIsNot(cloned.arguments["nested"], original.arguments["nested"])
        original.arguments["nested"]["items"].append("original-only")
        cloned.arguments["nested"]["items"].append("clone-only")

        self.assertEqual(original.arguments["nested"]["items"], ["a", "original-only"])
        self.assertEqual(cloned.arguments["nested"]["items"], ["a", "clone-only"])


if __name__ == "__main__":
    unittest.main()
