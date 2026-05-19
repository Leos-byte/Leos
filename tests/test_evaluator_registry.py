from __future__ import annotations

import unittest

from leos_agent.evaluator_registry import (
    DomainEvaluator,
    EvaluatorRegistry,
    EvaluatorRegistryError,
)
from leos_agent.goal_evaluator import GoalEvaluationStatus, GoalEvaluator
from leos_agent.goals import Goal, GoalProgress
from leos_agent.state import WorldState


class EvaluatorRegistryTests(unittest.TestCase):
    def test_registry_registers_software_engineering_evaluator(self) -> None:
        registry = EvaluatorRegistry()

        self.assertIn("software_engineering:tests_pass", registry.rules())

    def test_tests_pass_rule_works_through_registry(self) -> None:
        evaluation = EvaluatorRegistry().evaluate(Goal("verify", ["tests pass"]), WorldState(facts={"tests_ok": True}))

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_pr_opened_rule_works_through_registry(self) -> None:
        state = WorldState(facts={"github_pr": {"number": 1, "state": "open"}})

        evaluation = EvaluatorRegistry().evaluate(Goal("pr", ["PR opened"]), state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_ci_passed_rule_works_through_registry(self) -> None:
        state = WorldState(facts={"github_ci_status": {"state": "success"}})

        evaluation = EvaluatorRegistry().evaluate(Goal("ci", ["CI passed"]), state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_unmatched_criteria_prevents_full_success(self) -> None:
        state = WorldState(facts={"tests_ok": True})

        evaluation = EvaluatorRegistry().evaluate(Goal("mixed", ["tests pass", "documentation updated"]), state)

        self.assertIsNot(evaluation.status, GoalEvaluationStatus.SUCCEEDED)
        self.assertIn("documentation updated", evaluation.unsatisfied_criteria)

    def test_mixed_satisfied_and_unmatched_returns_partial(self) -> None:
        state = WorldState(facts={"tests_ok": True})

        evaluation = EvaluatorRegistry().evaluate(Goal("mixed", ["tests pass", "unknown criterion"]), state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.PARTIAL)

    def test_failed_criterion_dominates_partial(self) -> None:
        state = WorldState(facts={"tests_ok": True, "github_ci_status": {"state": "failure"}})

        evaluation = EvaluatorRegistry().evaluate(Goal("mixed", ["tests pass", "CI passed"]), state)

        self.assertIs(evaluation.status, GoalEvaluationStatus.FAILED)

    def test_fallback_complete_succeeds_only_when_no_rule_matched(self) -> None:
        progress = GoalProgress(total_steps=1, verified_steps=1)

        evaluation = EvaluatorRegistry().evaluate(Goal("generic", ["do the task"]), WorldState(), progress)

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)
        self.assertIn("Fallback", evaluation.explanation)

    def test_fallback_does_not_override_matched_failed_criterion(self) -> None:
        progress = GoalProgress(total_steps=1, verified_steps=1)
        state = WorldState(facts={"tests_ok": False})

        evaluation = EvaluatorRegistry().evaluate(Goal("tests", ["tests pass"]), state, progress)

        self.assertIs(evaluation.status, GoalEvaluationStatus.FAILED)

    def test_goal_evaluator_old_api_still_works(self) -> None:
        evaluation = GoalEvaluator().evaluate(Goal("verify", ["tests pass"]), WorldState(facts={"tests_ok": True}))

        self.assertIs(evaluation.status, GoalEvaluationStatus.SUCCEEDED)

    def test_duplicate_evaluator_domain_rejected(self) -> None:
        registry = EvaluatorRegistry([])
        registry.register(DomainEvaluator("demo", []))

        with self.assertRaises(EvaluatorRegistryError):
            registry.register(DomainEvaluator("demo", []))

    def test_unregister_missing_domain_gives_clear_error(self) -> None:
        with self.assertRaises(EvaluatorRegistryError):
            EvaluatorRegistry([]).unregister("missing")


if __name__ == "__main__":
    unittest.main()
