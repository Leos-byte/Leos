"""Tests for plan serialization roundtrip fidelity."""

from __future__ import annotations

import unittest

from leos_agent.enums import CompensationStrategy, Permission, Reversibility, RiskLevel, StepStatus
from leos_agent.goals import Goal, GoalStatus, ResourceBudget
from leos_agent.plans import ActionStep, StateCondition, TransactionPlan
from leos_agent.serialization import (
    SerializationError,
    deserialize_plan,
    serialize_plan,
)
from leos_agent.state import TrustLevel
from leos_agent.tools import Secret


class SerializationRoundtripTests(unittest.TestCase):
    def _echo_plan(self) -> TransactionPlan:
        goal = Goal(description="t", success_criteria=["ok"], stop_conditions=["done"])
        return TransactionPlan(goal=goal, steps=[ActionStep("echo", {"message": "hi"}, "test", idempotency_key="once")])

    def test_idempotency_key_roundtrip(self) -> None:
        plan = self._echo_plan()
        loaded = deserialize_plan(serialize_plan(plan))
        self.assertEqual(loaded.steps[0].idempotency_key, "once")

    def test_preconditions_roundtrip(self) -> None:
        goal = Goal(description="t", success_criteria=["ok"], stop_conditions=["done"])
        plan = TransactionPlan(
            goal=goal,
            steps=[
                ActionStep("echo", {"message": "hi"}, "test", preconditions=(StateCondition("ready", "equals", True),))
            ],
        )
        loaded = deserialize_plan(serialize_plan(plan))
        self.assertEqual(len(loaded.steps[0].preconditions), 1)
        self.assertEqual(loaded.steps[0].preconditions[0].variable, "ready")
        self.assertEqual(loaded.steps[0].preconditions[0].operator, "equals")
        self.assertEqual(loaded.steps[0].preconditions[0].value, True)

    def test_postconditions_roundtrip(self) -> None:
        goal = Goal(description="t", success_criteria=["ok"], stop_conditions=["done"])
        plan = TransactionPlan(
            goal=goal,
            steps=[ActionStep("echo", {"message": "hi"}, "test", postconditions=(StateCondition("result", "exists"),))],
        )
        loaded = deserialize_plan(serialize_plan(plan))
        self.assertEqual(len(loaded.steps[0].postconditions), 1)

    def test_invariants_roundtrip(self) -> None:
        goal = Goal(description="t", success_criteria=["ok"], stop_conditions=["done"])
        plan = TransactionPlan(
            goal=goal,
            steps=[ActionStep("echo", {"message": "hi"}, "test", invariants=(StateCondition("safe", "exists"),))],
        )
        loaded = deserialize_plan(serialize_plan(plan))
        self.assertEqual(len(loaded.steps[0].invariants), 1)

    def test_trust_level_roundtrip(self) -> None:
        goal = Goal(description="t", success_criteria=["ok"], stop_conditions=["done"])
        plan = TransactionPlan(
            goal=goal,
            steps=[
                ActionStep(
                    "echo",
                    {"message": "hi"},
                    "test",
                    preconditions=(StateCondition("x", "exists", trust_level=TrustLevel.VERIFIED),),
                )
            ],
        )
        loaded = deserialize_plan(serialize_plan(plan))
        cond = loaded.steps[0].preconditions[0]
        self.assertEqual(cond.trust_level, TrustLevel.VERIFIED)

    def test_required_permissions_roundtrip(self) -> None:
        goal = Goal(description="t", success_criteria=["ok"], stop_conditions=["done"])
        step = ActionStep("echo", {"message": "hi"}, "test", required_permissions=(Permission.WRITE_FILES,))
        step.status = StepStatus.VERIFIED
        step.risk = RiskLevel.MEDIUM
        step.reversibility = Reversibility.REVERSIBLE
        step.compensation_strategy = CompensationStrategy.UNDO
        plan = TransactionPlan(goal=goal, steps=[step])
        loaded = deserialize_plan(serialize_plan(plan))
        s = loaded.steps[0]
        self.assertEqual(s.status, StepStatus.VERIFIED)
        self.assertEqual(s.risk, RiskLevel.MEDIUM)
        self.assertEqual(s.reversibility, Reversibility.REVERSIBLE)
        self.assertEqual(s.compensation_strategy, CompensationStrategy.UNDO)
        self.assertIn(Permission.WRITE_FILES, s.required_permissions)

    def test_non_serializable_argument_raises(self) -> None:
        goal = Goal(description="t", success_criteria=["ok"], stop_conditions=["done"])
        plan = TransactionPlan(goal=goal, steps=[ActionStep("echo", {"msg": Secret("token")}, "test")])
        with self.assertRaises(SerializationError):
            serialize_plan(plan)

    def test_budget_roundtrip(self) -> None:
        goal = Goal(
            description="t",
            success_criteria=["ok"],
            stop_conditions=["done"],
            budget=ResourceBudget(max_tool_calls=5, max_file_writes=2),
        )
        plan = TransactionPlan(goal=goal, steps=[ActionStep("echo", {"message": "hi"}, "test")])
        loaded = deserialize_plan(serialize_plan(plan))
        self.assertEqual(loaded.goal.budget.max_tool_calls, 5)
        self.assertEqual(loaded.goal.budget.max_file_writes, 2)

    def test_goal_status_roundtrip(self) -> None:
        plan = self._echo_plan()
        plan.goal = plan.goal.transition(GoalStatus.PLANNING).transition(GoalStatus.RUNNING)
        loaded = deserialize_plan(serialize_plan(plan))
        self.assertEqual(loaded.goal.status, GoalStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
