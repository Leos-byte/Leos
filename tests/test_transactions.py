"""Transaction manager goal-status semantics."""

from __future__ import annotations

import unittest

from leos_agent import ActionStep, AgentKernel, Goal, PolicyEngine
from leos_agent.enums import GoalStatus, StepStatus
from leos_agent.tools import default_registry


class TransactionGoalStatusTests(unittest.TestCase):
    def test_verified_steps_do_not_directly_mark_goal_succeeded(self) -> None:
        agent = AgentKernel(registry=default_registry(), policy=PolicyEngine())
        goal = Goal("Echo", ["last echo observed"])
        plan = agent.build_plan(goal, [ActionStep("echo", {"message": "hi"}, "echo")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
        self.assertEqual(result.goal.status, GoalStatus.PARTIALLY_DONE)


if __name__ == "__main__":
    unittest.main()
