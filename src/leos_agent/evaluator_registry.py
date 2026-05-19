"""Extensible deterministic goal evaluation registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import LeosError
from .goal_evaluator import GoalEvaluation, GoalEvaluationStatus
from .goals import Goal, GoalProgress
from .state import WorldState


class EvaluatorRegistryError(LeosError):
    """Raised when evaluator registration fails."""


@dataclass(frozen=True)
class CriterionEvaluation:
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


class CriterionRule(Protocol):
    name: str

    def matches(self, criterion: str) -> bool: ...

    def evaluate(
        self,
        criterion: str,
        goal: Goal,
        state: WorldState,
        progress: GoalProgress | None = None,
    ) -> CriterionEvaluation: ...


@dataclass
class DomainEvaluator:
    domain: str
    rules: list[CriterionRule]

    def evaluate_criterion(
        self,
        criterion: str,
        goal: Goal,
        state: WorldState,
        progress: GoalProgress | None = None,
    ) -> CriterionEvaluation:
        for rule in self.rules:
            if rule.matches(criterion):
                return rule.evaluate(criterion, goal, state, progress)
        return CriterionEvaluation(
            criterion=criterion,
            matched=False,
            status=GoalEvaluationStatus.UNKNOWN,
            explanation=f"No rule matched criterion in domain {self.domain}.",
        )

    def rule_names(self) -> list[str]:
        return [rule.name for rule in self.rules]


class EvaluatorRegistry:
    def __init__(self, evaluators: list[DomainEvaluator] | None = None) -> None:
        self.evaluators: dict[str, DomainEvaluator] = {}
        for evaluator in evaluators or default_domain_evaluators():
            self.register(evaluator)

    def register(self, evaluator: DomainEvaluator) -> None:
        if not evaluator.domain:
            raise EvaluatorRegistryError("Evaluator domain must be non-empty")
        if evaluator.domain in self.evaluators:
            raise EvaluatorRegistryError(f"Evaluator domain already registered: {evaluator.domain}")
        self.evaluators[evaluator.domain] = evaluator

    def unregister(self, domain: str) -> None:
        if domain not in self.evaluators:
            raise EvaluatorRegistryError(f"Evaluator domain not registered: {domain}")
        del self.evaluators[domain]

    def get(self, domain: str) -> DomainEvaluator:
        if domain not in self.evaluators:
            raise EvaluatorRegistryError(f"Evaluator domain not registered: {domain}")
        return self.evaluators[domain]

    def rules(self) -> list[str]:
        names: list[str] = []
        for evaluator in self.evaluators.values():
            names.extend(f"{evaluator.domain}:{name}" for name in evaluator.rule_names())
        return sorted(names)

    def evaluate(self, goal: Goal, state: WorldState, progress: GoalProgress | None = None) -> GoalEvaluation:
        evaluations = [
            self._evaluate_criterion(str(criterion), goal, state, progress) for criterion in goal.success_criteria
        ]
        matched = [evaluation for evaluation in evaluations if evaluation.matched]
        if not matched:
            fallback = self.get("fallback").evaluate_criterion("__fallback__", goal, state, progress)
            return GoalEvaluation(status=fallback.status, evidence=fallback.evidence, explanation=fallback.explanation)

        satisfied = [evaluation.criterion for evaluation in evaluations if evaluation.satisfied]
        unsatisfied = [evaluation.criterion for evaluation in evaluations if not evaluation.satisfied]
        evidence = {evaluation.criterion: evaluation.evidence for evaluation in evaluations if evaluation.evidence}
        explanations = [evaluation.explanation for evaluation in evaluations if evaluation.explanation]

        if any(evaluation.failed for evaluation in evaluations):
            status = GoalEvaluationStatus.FAILED
        elif len(satisfied) == len(evaluations) and all(evaluation.matched for evaluation in evaluations):
            status = GoalEvaluationStatus.SUCCEEDED
        elif satisfied:
            status = GoalEvaluationStatus.PARTIAL
        elif any(evaluation.matched for evaluation in evaluations):
            status = GoalEvaluationStatus.UNKNOWN
        else:
            status = GoalEvaluationStatus.UNKNOWN
        return GoalEvaluation(
            status=status,
            satisfied_criteria=satisfied,
            unsatisfied_criteria=unsatisfied,
            evidence=evidence,
            explanation="; ".join(explanations) or "Evaluated deterministic goal criteria.",
        )

    def _evaluate_criterion(
        self,
        criterion: str,
        goal: Goal,
        state: WorldState,
        progress: GoalProgress | None,
    ) -> CriterionEvaluation:
        for domain, evaluator in self.evaluators.items():
            if domain == "fallback":
                continue
            evaluation = evaluator.evaluate_criterion(criterion, goal, state, progress)
            if evaluation.matched:
                return evaluation
        return CriterionEvaluation(
            criterion=criterion,
            matched=False,
            status=GoalEvaluationStatus.UNKNOWN,
            explanation="No deterministic rule matched this criterion.",
        )


class _TestsPassRule:
    name = "tests_pass"

    def matches(self, criterion: str) -> bool:
        return _contains_any(
            _normalize(criterion), ("tests pass", "test pass", "tests passed", "python -m unittest", "测试通过")
        )

    def evaluate(
        self, criterion: str, goal: Goal, state: WorldState, progress: GoalProgress | None = None
    ) -> CriterionEvaluation:
        del goal, progress
        tests_ok = state.facts.get("tests_ok")
        if tests_ok is True:
            return _satisfied(criterion, {"tests_ok": True}, "Test success criterion satisfied by tests_ok=True.")
        if tests_ok is False:
            return _failed(criterion, {"tests_ok": False}, "Test success criterion failed because tests_ok=False.")
        return _unknown(criterion, {}, "Test success criterion is unknown because tests_ok is missing.")


class _FileUpdatedRule:
    name = "file_updated"

    def matches(self, criterion: str) -> bool:
        return _contains_any(
            _normalize(criterion), ("file updated", "file patched", "file written", "文件已更新", "文件写入")
        )

    def evaluate(
        self, criterion: str, goal: Goal, state: WorldState, progress: GoalProgress | None = None
    ) -> CriterionEvaluation:
        del goal, progress
        for key in ("file_patched", "file_written", "github_file_updated"):
            if key in state.facts:
                return _satisfied(criterion, {key: state.facts[key]}, f"File update criterion satisfied by {key}.")
        return _unknown(criterion, {}, "File update criterion is unknown because no file update evidence exists.")


class _PROpenedRule:
    name = "pr_opened"

    def matches(self, criterion: str) -> bool:
        return _contains_any(
            _normalize(criterion),
            ("pr opened", "pull request opened", "opened pr", "pr created", "github pr opened", "pr 已创建"),
        )

    def evaluate(
        self, criterion: str, goal: Goal, state: WorldState, progress: GoalProgress | None = None
    ) -> CriterionEvaluation:
        del goal, progress
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


class _CIPassedRule:
    name = "ci_passed"

    def matches(self, criterion: str) -> bool:
        return _contains_any(_normalize(criterion), ("ci passing", "ci passed", "ci success", "ci 通过"))

    def evaluate(
        self, criterion: str, goal: Goal, state: WorldState, progress: GoalProgress | None = None
    ) -> CriterionEvaluation:
        del goal, progress
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


class _FallbackRule:
    name = "progress_fallback"

    def matches(self, criterion: str) -> bool:
        return criterion == "__fallback__"

    def evaluate(
        self, criterion: str, goal: Goal, state: WorldState, progress: GoalProgress | None = None
    ) -> CriterionEvaluation:
        del criterion, goal, state
        if progress is None:
            return CriterionEvaluation(
                criterion="__fallback__",
                matched=True,
                status=GoalEvaluationStatus.UNKNOWN,
                explanation="No deterministic success criteria matched and no progress fallback is available.",
            )
        if progress.phase == "complete":
            return CriterionEvaluation(
                criterion="__fallback__",
                matched=True,
                status=GoalEvaluationStatus.SUCCEEDED,
                explanation="Fallback based on verified steps: progress phase is complete.",
            )
        if progress.phase == "blocked":
            return CriterionEvaluation(
                criterion="__fallback__",
                matched=True,
                status=GoalEvaluationStatus.BLOCKED,
                explanation="Fallback based on verified steps: progress phase is blocked.",
            )
        if progress.phase == "failed":
            return CriterionEvaluation(
                criterion="__fallback__",
                matched=True,
                status=GoalEvaluationStatus.FAILED,
                explanation="Fallback based on verified steps: progress phase is failed.",
            )
        if progress.phase == "partial":
            return CriterionEvaluation(
                criterion="__fallback__",
                matched=True,
                status=GoalEvaluationStatus.PARTIAL,
                explanation="Fallback based on verified steps: progress phase is partial.",
            )
        return CriterionEvaluation(
            criterion="__fallback__",
            matched=True,
            status=GoalEvaluationStatus.UNKNOWN,
            explanation=f"Fallback based on verified steps: progress phase is {progress.phase}.",
        )


class SoftwareEngineeringDomainEvaluator(DomainEvaluator):
    def __init__(self) -> None:
        super().__init__("software_engineering", [_TestsPassRule(), _FileUpdatedRule()])


class GitHubDomainEvaluator(DomainEvaluator):
    def __init__(self) -> None:
        super().__init__("github", [_PROpenedRule(), _CIPassedRule()])


class FallbackDomainEvaluator(DomainEvaluator):
    def __init__(self) -> None:
        super().__init__("fallback", [_FallbackRule()])


def default_domain_evaluators() -> list[DomainEvaluator]:
    return [SoftwareEngineeringDomainEvaluator(), GitHubDomainEvaluator(), FallbackDomainEvaluator()]


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _satisfied(criterion: str, evidence: dict[str, Any], explanation: str) -> CriterionEvaluation:
    return CriterionEvaluation(
        criterion=criterion,
        matched=True,
        status=GoalEvaluationStatus.SUCCEEDED,
        evidence=evidence,
        explanation=explanation,
    )


def _failed(criterion: str, evidence: dict[str, Any], explanation: str) -> CriterionEvaluation:
    return CriterionEvaluation(
        criterion=criterion,
        matched=True,
        status=GoalEvaluationStatus.FAILED,
        evidence=evidence,
        explanation=explanation,
    )


def _unknown(criterion: str, evidence: dict[str, Any], explanation: str) -> CriterionEvaluation:
    return CriterionEvaluation(
        criterion=criterion,
        matched=True,
        status=GoalEvaluationStatus.UNKNOWN,
        evidence=evidence,
        explanation=explanation,
    )
