"""Production-profile GitHub smoke path for disposable repositories only.

This path is disabled by default. It is scoped to bounded GitHub software
engineering actions and never prints token values.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from leos_agent import (
    ActionStep,
    AgentKernel,
    ApprovalDecision,
    ApprovalDecisionValue,
    ApprovalGate,
    EgressPolicy,
    GitHubCommentTool,
    GitHubConflictError,
    GitHubCreateBranchTool,
    GitHubGetFileTool,
    GitHubOpenPRTool,
    GitHubRESTClient,
    GitHubUpdateFileTool,
    Goal,
    GoalEvaluationStatus,
    GoalEvaluator,
    InMemoryGitHubClient,
    PolicyEngine,
    Secret,
    ToolRegistry,
    sign_approval_decision,
    verify_approval_decision_signature,
)
from leos_agent.state import TrustLevel

PROTECTED_BRANCHES = {"main", "master", "trunk", "release"}


class SignedSmokeApprovalGate(ApprovalGate):
    """Smoke-only approval gate that signs and verifies each decision locally."""

    signed_approval_enforced = True

    def __init__(self, *, signature_secret: str, approver: str = "production-smoke") -> None:
        super().__init__(approver=None)
        self.signature_secret = signature_secret
        self.approver = approver

    def request_packet(self, packet, step):  # type: ignore[override]
        del step
        decision = ApprovalDecision(
            packet.approval_id,
            packet.step_hash,
            ApprovalDecisionValue.APPROVE,
            approver=str(self.approver),
            reason="explicit production_github_only smoke approval",
        )
        signature = sign_approval_decision(decision, self.signature_secret)
        if not verify_approval_decision_signature(decision, self.signature_secret, signature):
            return ApprovalDecision(packet.approval_id, packet.step_hash, ApprovalDecisionValue.DENY)
        return decision


def main() -> int:
    if os.environ.get("LEOS_ENABLE_REAL_GITHUB_WRITES") != "1":
        print("real write disabled; set LEOS_ENABLE_REAL_GITHUB_WRITES=1 explicitly")
        return 0

    summary: dict[str, Any] = {}
    try:
        repo = _required_env("LEOS_GITHUB_TEST_REPO")
        _disposable_repo_guard(repo)
        token_ref = _required_env("LEOS_GITHUB_TOKEN_SECRET_REF")
        token = Secret(_required_env(token_ref))
        approval_secret_ref = _required_env("LEOS_APPROVAL_HMAC_SECRET_REF")
        approval_secret = _required_env(approval_secret_ref)
        base_branch = os.environ.get("LEOS_GITHUB_BASE_BRANCH", "main")
        branch_prefix = os.environ.get("LEOS_GITHUB_WORK_BRANCH_PREFIX", "leos/")
        if not branch_prefix or not branch_prefix.startswith("leos/"):
            raise GitHubConflictError("production smoke writes must use leos/ branch prefix")
        work_branch = f"{branch_prefix}production-smoke-{int(time.time())}"
        target_path = os.environ.get("LEOS_GITHUB_TEST_PATH", "leos-production-smoke.txt")
        content = f"Leos production_github_only smoke.\nbranch={work_branch}\n"
        idempotency_key = f"leos-production-smoke-{work_branch}"

        policy = PolicyEngine.from_profile("production_github_only")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",))
        client = _client(policy)
        registry = _registry(client)
        kernel = AgentKernel(
            registry=registry,
            policy=policy,
            approval_gate=SignedSmokeApprovalGate(signature_secret=approval_secret),
        )
        _record_attestation(kernel, client)

        summary.update(
            {
                "repo": repo,
                "base_branch": base_branch,
                "work_branch": work_branch,
                "profile": "production_github_only",
                "runtime_attestation_verified": True,
            }
        )
        current = _tool_mediated_get_file(
            kernel,
            repo=repo,
            path=target_path,
            ref=base_branch,
            token=token,
            purpose="preread",
            allow_missing=True,
        )
        previous = str(current.get("content", "")) if current else ""
        expected_sha = str(current.get("sha", "")) if current else None

        goal = Goal(
            "Disposable GitHub production smoke",
            ["file updated", "PR opened"],
            criteria=(
                {"key": "github_branch", "op": "exists"},
                {"key": "github_file_updated", "op": "exists"},
                {"key": "github_pr", "op": "exists"},
                {"key": "github_comment", "op": "exists", "required": False},
                {"key": "read_back_verified", "op": "equals", "value": True},
                {"key": "runtime_attestation_verified", "op": "equals", "value": True},
            ),
            stop_conditions=["PR opened or blocked"],
        )
        plan = kernel.build_plan(
            goal,
            [
                ActionStep(
                    "github_create_branch",
                    {"repo": repo, "branch": work_branch, "base": base_branch, "token": token},
                    "Create isolated production smoke branch.",
                ),
                ActionStep(
                    "github_update_file",
                    _without_none(
                        {
                            "repo": repo,
                            "path": target_path,
                            "branch": work_branch,
                            "content": content,
                            "message": "Leos production smoke",
                            "expected_sha": expected_sha,
                            "expected_previous": previous if expected_sha is None else None,
                            "token": token,
                        }
                    ),
                    "Write bounded smoke file with optimistic guard.",
                ),
                ActionStep(
                    "github_open_pr",
                    {
                        "repo": repo,
                        "title": "Leos production_github_only smoke",
                        "body": "Manual gated production smoke.",
                        "head": work_branch,
                        "base": base_branch,
                        "idempotency_key": idempotency_key,
                        "token": token,
                    },
                    "Open idempotent smoke PR.",
                    idempotency_key=idempotency_key,
                ),
            ],
        )
        executed = kernel.run(plan)
        _require_verified(executed.steps)
        pr = kernel.state.facts.get("github_pr", {})
        pr_number = int(pr.get("number", 0))
        if pr_number:
            comment_plan = kernel.build_plan(
                goal,
                [
                    ActionStep(
                        "github_comment",
                        {
                            "repo": repo,
                            "issue_number": pr_number,
                            "body": "Leos production smoke completed tool-mediated write and read-back.",
                            "token": token,
                        },
                        "Comment on production smoke PR.",
                    )
                ],
            )
            kernel.run(comment_plan)
        read_back = _tool_mediated_get_file(
            kernel,
            repo=repo,
            path=target_path,
            ref=work_branch,
            token=token,
            purpose="readback",
            allow_missing=False,
        )
        if read_back is None or read_back.get("content") != content:
            raise GitHubConflictError("read-back verification failed")
        kernel.state.observe({"read_back_verified": True}, trust_level=TrustLevel.VERIFIED)
        kernel.audit_log.record(
            "github.real_write.readback_verified",
            "Production GitHub smoke read-back verified expected content",
            repo=repo,
            path=target_path,
            branch=work_branch,
        )
        evaluation = GoalEvaluator().evaluate(goal, kernel.state, kernel.transactions.track_progress(executed))
        kernel.audit_log.record(
            "github.real_write.goal_evaluated",
            "Production GitHub smoke evaluated typed goal criteria",
            evaluation_status=evaluation.status.value,
            satisfied_criteria=list(evaluation.satisfied_criteria),
            unsatisfied_criteria=list(evaluation.unsatisfied_criteria),
        )
        summary["evaluation_status"] = evaluation.status.value
        summary["pr_number"] = pr.get("number")
        if evaluation.status is not GoalEvaluationStatus.SUCCEEDED:
            raise GitHubConflictError("goal evaluation failed")
    except Exception as exc:  # noqa: BLE001 - smoke path must return a structured failure
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        print(json.dumps(summary, indent=2, sort_keys=True))
        print("token not printed")
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    print("token not printed")
    return 0


def _client(policy: PolicyEngine):
    if os.environ.get("LEOS_GITHUB_SMOKE_FAKE") == "1":
        return InMemoryGitHubClient()
    return GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)


def _registry(client) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(GitHubGetFileTool(client))
    registry.register(GitHubCreateBranchTool(client))
    registry.register(GitHubUpdateFileTool(client))
    registry.register(GitHubOpenPRTool(client))
    registry.register(GitHubCommentTool(client))
    return registry


def _record_attestation(kernel: AgentKernel, client) -> None:
    summary = {
        "runtime_egress_enforced": bool(getattr(client, "runtime_egress_enforced", False)),
        "runtime_egress_policy_configured": bool(getattr(client, "runtime_egress_policy_configured", False)),
        "runtime_egress_mode": str(getattr(client, "runtime_egress_mode", "unknown")),
        "runtime_egress_host": str(getattr(client, "runtime_egress_host", "")),
    }
    if not summary["runtime_egress_enforced"] or not summary["runtime_egress_policy_configured"]:
        raise GitHubConflictError("runtime egress attestation failed")
    kernel.state.observe({"runtime_attestation_verified": True}, trust_level=TrustLevel.VERIFIED)
    kernel.audit_log.record(
        "github.real_write.runtime_attestations_checked",
        "Production GitHub smoke checked runtime egress attestations",
        **summary,
    )


def _disposable_repo_guard(repo: str) -> None:
    if os.environ.get("LEOS_GITHUB_TEST_REPO_MUST_BE_DISPOSABLE") != "1":
        raise GitHubConflictError("set LEOS_GITHUB_TEST_REPO_MUST_BE_DISPOSABLE=1 for real writes")
    repo_name = repo.split("/")[-1].lower()
    if "leos-smoke" not in repo_name and os.environ.get("LEOS_ALLOW_NON_NAMED_DISPOSABLE_REPO") != "1":
        raise GitHubConflictError("refusing real write unless repo name contains leos-smoke")
    print("disposable repo guard passed")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def _without_none(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if item is not None}


def _tool_mediated_get_file(
    kernel: AgentKernel,
    *,
    repo: str,
    path: str,
    ref: str,
    token: Secret,
    purpose: str,
    allow_missing: bool,
) -> dict[str, object] | None:
    event_type = f"github.real_write.tool_mediated_{purpose}"
    goal = Goal(
        f"GitHub production smoke {purpose}",
        ["file read"],
        criteria=({"key": "github_file", "op": "exists"},),
        stop_conditions=["file read or missing"],
    )
    plan = kernel.build_plan(
        goal,
        [
            ActionStep(
                "github_get_file",
                {"repo": repo, "path": path, "ref": ref, "token": token},
                f"Tool-mediated GitHub production smoke {purpose}.",
            )
        ],
    )
    result = kernel.run(plan)
    file_data = kernel.state.facts.get("github_file")
    if result.steps and result.steps[0].status.value == "verified" and isinstance(file_data, dict):
        kernel.audit_log.record(event_type, "GitHub file read via tool-mediated path", repo=repo, path=path, ref=ref)
        return dict(file_data)
    if allow_missing:
        kernel.audit_log.record(
            f"{event_type}_missing",
            "GitHub file was not found through tool-mediated pre-read; proceeding as create-new-file case",
            repo=repo,
            path=path,
            ref=ref,
        )
        return None
    raise GitHubConflictError(f"tool-mediated GitHub production smoke {purpose} failed")


def _require_verified(steps) -> None:
    if not all(step.status.value == "verified" for step in steps):
        raise GitHubConflictError("transaction did not verify every production smoke step")


if __name__ == "__main__":
    raise SystemExit(main())
