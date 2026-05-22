from __future__ import annotations

import unittest

from leos_agent.goal_evaluator import GoalEvaluationStatus, GoalEvaluator
from leos_agent.goals import Goal, GoalProgress
from leos_agent.state import WorldState


class GoalEvaluatorTests(unittest.TestCase):
    def test_tests_pass_succeeds_when_tests_ok_true(self) -> None:
        state = WorldState(facts={"tests_ok": True})
        goal = Goal("verify", ["tests pass"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)
        self.assertEqual(evaluation.satisfied_criteria, ["tests pass"])

    def test_tests_pass_fails_when_tests_ok_false(self) -> None:
        state = WorldState(facts={"tests_ok": False})
        goal = Goal("verify", ["tests passed"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.FAILED)
        self.assertEqual(evaluation.unsatisfied_criteria, ["tests passed"])

    def test_tests_pass_unknown_when_tests_ok_missing(self) -> None:
        goal = Goal("verify", ["测试通过"])

        evaluation = GoalEvaluator().evaluate(goal, WorldState())

        self.assertIs(evaluation.status, GoalEvaluationStatus.UNKNOWN)
        self.assertFalse(evaluation.satisfied_criteria)

    def test_file_updated_succeeds_with_file_patch_evidence(self) -> None:
        state = WorldState(facts={"file_patched": "/workspace/app.py"})
        goal = Goal("patch", ["file updated"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)
        self.assertIn("file_patched", evaluation.evidence["file updated"])

    def test_github_pr_opened_succeeds_with_open_pr(self) -> None:
        state = WorldState(facts={"github_pr": {"number": 1, "state": "open"}})
        goal = Goal("open pr", ["PR opened"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_ci_passed_succeeds_with_success_status(self) -> None:
        state = WorldState(facts={"github_ci_status": {"state": "success"}})
        goal = Goal("wait for ci", ["CI passed"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_fallback_complete_succeeds_with_explanation(self) -> None:
        progress = GoalProgress(total_steps=1, verified_steps=1)
        goal = Goal("generic", ["do the task"])

        evaluation = GoalEvaluator().evaluate(goal, WorldState(), progress)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)
        self.assertIn("Fallback", evaluation.explanation)

    def test_fallback_blocked_returns_blocked(self) -> None:
        progress = GoalProgress(total_steps=1, blocked_steps=1)
        goal = Goal("generic", ["do the task"])

        evaluation = GoalEvaluator().evaluate(goal, WorldState(), progress)

        self.assertIs(evaluation.status, GoalEvaluationStatus.BLOCKED)

    def test_unmatched_criterion_prevents_success(self) -> None:
        state = WorldState(facts={"tests_ok": True})
        goal = Goal("verify", ["tests pass", "documentation updated"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.PARTIAL)
        self.assertIn("documentation updated", evaluation.unsatisfied_criteria)

    def test_tests_and_ci_success_succeeds(self) -> None:
        state = WorldState(facts={"tests_ok": True, "github_ci_status": {"state": "success"}})
        goal = Goal("verify", ["tests pass", "CI passed"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_tests_and_ci_failure_fails(self) -> None:
        state = WorldState(facts={"tests_ok": True, "github_ci_status": {"state": "failure"}})
        goal = Goal("verify", ["tests pass", "CI passed"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.FAILED)

    def test_tests_and_pr_open_succeeds(self) -> None:
        state = WorldState(facts={"tests_ok": True, "github_pr": {"number": 1, "state": "open"}})
        goal = Goal("verify", ["tests pass", "PR opened"])

        evaluation = GoalEvaluator().evaluate(goal, state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_typed_equals_success(self) -> None:
        goal = Goal("verify", ["tests pass"], criteria=({"key": "tests_ok", "op": "equals", "value": True},))

        evaluation = GoalEvaluator().evaluate(goal, WorldState(facts={"tests_ok": True}))

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)
        self.assertIn("tests_ok equals", evaluation.satisfied_criteria)

    def test_typed_in_success(self) -> None:
        goal = Goal(
            "verify",
            ["CI passed"],
            criteria=({"key": "ci_status", "op": "in", "value": ["success", "skipped"]},),
        )

        evaluation = GoalEvaluator().evaluate(
            goal,
            WorldState(facts={"ci_status": "success", "github_ci_status": {"state": "success"}}),
        )

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_missing_required_typed_criterion_fails(self) -> None:
        goal = Goal("verify", ["tests pass"], criteria=({"key": "tests_ok", "op": "equals", "value": True},))

        evaluation = GoalEvaluator().evaluate(goal, WorldState())

        self.assertIs(evaluation.status, GoalEvaluationStatus.FAILED)

    def test_failed_tests_cannot_be_interpreted_as_success(self) -> None:
        progress = GoalProgress(total_steps=1, verified_steps=1)
        goal = Goal("verify", ["do the task"], criteria=({"key": "tests_ok", "op": "equals", "value": True},))

        evaluation = GoalEvaluator().evaluate(goal, WorldState(facts={"tests_ok": False}), progress)

        self.assertIs(evaluation.status, GoalEvaluationStatus.FAILED)

    def test_required_typed_success_with_optional_missing_still_succeeds(self) -> None:
        goal = Goal(
            "verify",
            [],
            criteria=(
                {"key": "tests_ok", "op": "equals", "value": True},
                {"key": "coverage_ok", "op": "equals", "value": True, "required": False},
            ),
        )

        evaluation = GoalEvaluator().evaluate(goal, WorldState(facts={"tests_ok": True}))

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)
        self.assertIn("Optional typed criteria", evaluation.explanation)
        self.assertNotIn("coverage_ok equals", evaluation.unsatisfied_criteria)

    def test_required_typed_success_with_optional_failed_still_succeeds(self) -> None:
        goal = Goal(
            "verify",
            [],
            criteria=(
                {"key": "tests_ok", "op": "equals", "value": True},
                {"key": "coverage_ok", "op": "equals", "value": True, "required": False},
            ),
        )

        evaluation = GoalEvaluator().evaluate(
            goal,
            WorldState(facts={"tests_ok": True, "coverage_ok": False}),
        )

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)
        self.assertIn("coverage_ok equals", evaluation.evidence)
        self.assertNotIn("coverage_ok equals", evaluation.unsatisfied_criteria)

    def test_required_failed_dominates_optional_satisfied(self) -> None:
        goal = Goal(
            "verify",
            [],
            criteria=(
                {"key": "tests_ok", "op": "equals", "value": True},
                {"key": "docs_ok", "op": "equals", "value": True, "required": False},
            ),
        )

        evaluation = GoalEvaluator().evaluate(
            goal,
            WorldState(facts={"tests_ok": False, "docs_ok": True}),
        )

        self.assertIs(evaluation.status, GoalEvaluationStatus.FAILED)

    def test_only_optional_typed_criteria_do_not_false_succeed(self) -> None:
        goal = Goal(
            "verify",
            [],
            criteria=({"key": "docs_ok", "op": "equals", "value": True, "required": False},),
        )

        evaluation = GoalEvaluator().evaluate(goal, WorldState(facts={"docs_ok": True}))

        self.assertIs(evaluation.status, GoalEvaluationStatus.PARTIAL)


if __name__ == "__main__":
    unittest.main()
