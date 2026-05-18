"""Deterministic goal success-criteria evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .goals import Goal, GoalProgress
from .state import WorldState


class GoalEvaluationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GoalEvaluation:
    status: GoalEvaluationStatus
    satisfied_criteria: list[str] = field(default_factory=list)
    unsatisfied_criteria: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""


@dataclass(frozen=True)
class _CriterionEvaluation:
    criterion: str
    matched: bool
    status: GoalEvaluationStatus
    evidence: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""

    @property
    def satisfied(self) -> bool:
        return self.status is GoalEvaluationStatus.SUCCEEDED

    @property
    def failed(self) -> bool:
        return self.status is GoalEvaluationStatus.FAILED


class GoalEvaluator:
    """Deterministically evaluate whether explicit goal criteria are satisfied."""

    def evaluate(self, goal: Goal, state: WorldState, progress: GoalProgress | None = None) -> GoalEvaluation:
        evaluations: list[_CriterionEvaluation] = []
        for criterion in goal.success_criteria:
            evaluations.append(self._evaluate_criterion(str(criterion), state))

        matched = [evaluation for evaluation in evaluations if evaluation.matched]
        if not matched:
            return self._fallback(progress)

        satisfied = [evaluation.criterion for evaluation in matched if evaluation.satisfied]
        unsatisfied = [evaluation.criterion for evaluation in matched if not evaluation.satisfied]
        evidence = {evaluation.criterion: evaluation.evidence for evaluation in matched if evaluation.evidence}
        explanations = [evaluation.explanation for evaluation in matched if evaluation.explanation]

        if any(evaluation.failed for evaluation in matched):
            status = GoalEvaluationStatus.FAILED
        elif len(satisfied) == len(matched):
            status = GoalEvaluationStatus.SUCCEEDED
        elif satisfied:
            status = GoalEvaluationStatus.PARTIAL
        else:
            status = GoalEvaluationStatus.UNKNOWN

        return GoalEvaluation(
            status=status,
            satisfied_criteria=satisfied,
            unsatisfied_criteria=unsatisfied,
            evidence=evidence,
            explanation="; ".join(explanations) or "Matched deterministic goal criteria.",
        )

    def _evaluate_criterion(self, criterion: str, state: WorldState) -> _CriterionEvaluation:
        for rule in (
            self._evaluate_tests_pass,
            self._evaluate_file_updated,
            self._evaluate_pr_opened,
            self._evaluate_ci_passed,
        ):
            evaluation = rule(criterion, state)
            if evaluation.matched:
                return evaluation
        return _CriterionEvaluation(
            criterion=criterion,
            matched=False,
            status=GoalEvaluationStatus.UNKNOWN,
            explanation="No deterministic rule matched this criterion.",
        )

    @staticmethod
    def _evaluate_tests_pass(criterion: str, state: WorldState) -> _CriterionEvaluation:
        normalized = _normalize(criterion)
        if not _contains_any(
            normalized,
            (
                "tests pass",
                "test pass",
                "tests passed",
                "python -m unittest",
                "测试通过",
            ),
        ):
            return _unmatched(criterion)

        tests_ok = state.facts.get("tests_ok")
        if tests_ok is True:
            return _satisfied(criterion, {"tests_ok": True}, "Test success criterion satisfied by tests_ok=True.")
        if tests_ok is False:
            return _failed(criterion, {"tests_ok": False}, "Test success criterion failed because tests_ok=False.")
        return _unknown(criterion, {}, "Test success criterion is unknown because tests_ok is missing.")

    @staticmethod
    def _evaluate_file_updated(criterion: str, state: WorldState) -> _CriterionEvaluation:
        normalized = _normalize(criterion)
        if not _contains_any(
            normalized,
            (
                "file updated",
                "file patched",
                "file written",
                "文件已更新",
                "文件写入",
            ),
        ):
            return _unmatched(criterion)

        for key in ("file_patched", "file_written", "github_file_updated"):
            if key in state.facts:
                return _satisfied(
                    criterion,
                    {key: state.facts[key]},
                    f"File update criterion satisfied by {key}.",
                )
        return _unknown(criterion, {}, "File update criterion is unknown because no file update evidence exists.")

    @staticmethod
    def _evaluate_pr_opened(criterion: str, state: WorldState) -> _CriterionEvaluation:
        normalized = _normalize(criterion)
        if not _contains_any(
            normalized,
            (
                "pr opened",
                "pull request opened",
                "opened pr",
                "pr created",
                "github pr opened",
                "pr 已创建",
            ),
        ):
            return _unmatched(criterion)

        pr = state.facts.get("github_pr")
        if isinstance(pr, dict) and str(pr.get("state", "")).lower() == "open":
            return _satisfied(criterion, {"github_pr": pr}, "PR criterion satisfied by open GitHub PR evidence.")
        if isinstance(pr, dict) and pr.get("number") is not None:
            return _satisfied(
                criterion,
                {"github_pr": pr, "evidence_strength": "weak"},
                "PR criterion satisfied by PR number, but state evidence is weaker because state is missing.",
            )
        return _unknown(criterion, {}, "PR criterion is unknown because github_pr evidence is missing.")

    @staticmethod
    def _evaluate_ci_passed(criterion: str, state: WorldState) -> _CriterionEvaluation:
        normalized = _normalize(criterion)
        if not _contains_any(
            normalized,
            (
                "ci passing",
                "ci passed",
                "ci success",
                "ci 通过",
            ),
        ):
            return _unmatched(criterion)

        ci_status = state.facts.get("github_ci_status")
        if isinstance(ci_status, dict) and str(ci_status.get("state", "")).lower() in {"success", "passed", "green"}:
            return _satisfied(
                criterion, {"github_ci_status": ci_status}, "CI criterion satisfied by successful status."
            )
        if isinstance(ci_status, dict):
            return _failed(
                criterion, {"github_ci_status": ci_status}, "CI criterion failed because status is not green."
            )
        return _unknown(criterion, {}, "CI criterion is unknown because github_ci_status is missing.")

    @staticmethod
    def _fallback(progress: GoalProgress | None) -> GoalEvaluation:
        if progress is None:
            return GoalEvaluation(
                status=GoalEvaluationStatus.UNKNOWN,
                explanation="No deterministic success criteria matched and no progress fallback is available.",
            )
        if progress.phase == "complete":
            return GoalEvaluation(
                status=GoalEvaluationStatus.SUCCEEDED,
                explanation="Fallback based on verified steps: progress phase is complete.",
            )
        if progress.phase == "blocked":
            return GoalEvaluation(
                status=GoalEvaluationStatus.BLOCKED,
                explanation="Fallback based on verified steps: progress phase is blocked.",
            )
        if progress.phase == "failed":
            return GoalEvaluation(
                status=GoalEvaluationStatus.FAILED,
                explanation="Fallback based on verified steps: progress phase is failed.",
            )
        if progress.phase == "partial":
            return GoalEvaluation(
                status=GoalEvaluationStatus.PARTIAL,
                explanation="Fallback based on verified steps: progress phase is partial.",
            )
        return GoalEvaluation(
            status=GoalEvaluationStatus.UNKNOWN,
            explanation=f"Fallback based on verified steps: progress phase is {progress.phase}.",
        )


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _unmatched(criterion: str) -> _CriterionEvaluation:
    return _CriterionEvaluation(criterion=criterion, matched=False, status=GoalEvaluationStatus.UNKNOWN)


def _satisfied(criterion: str, evidence: dict[str, Any], explanation: str) -> _CriterionEvaluation:
    return _CriterionEvaluation(
        criterion=criterion,
        matched=True,
        status=GoalEvaluationStatus.SUCCEEDED,
        evidence=evidence,
        explanation=explanation,
    )


def _failed(criterion: str, evidence: dict[str, Any], explanation: str) -> _CriterionEvaluation:
    return _CriterionEvaluation(
        criterion=criterion,
        matched=True,
        status=GoalEvaluationStatus.FAILED,
        evidence=evidence,
        explanation=explanation,
    )


def _unknown(criterion: str, evidence: dict[str, Any], explanation: str) -> _CriterionEvaluation:
    return _CriterionEvaluation(
        criterion=criterion,
        matched=True,
        status=GoalEvaluationStatus.UNKNOWN,
        evidence=evidence,
        explanation=explanation,
    )
