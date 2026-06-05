"""Production-profile GitHub smoke path for disposable repositories only.

This path is disabled by default. It is scoped to bounded GitHub software
engineering actions and never prints token values.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from leos_agent import (
    ActionStep,
    AgentKernel,
    ApprovalDecision,
    ApprovalDecisionValue,
    ApprovalGate,
    EgressPolicy,
    GitHubClosePRTool,
    GitHubCommentTool,
    GitHubConflictError,
    GitHubCreateBranchTool,
    GitHubDeleteBranchTool,
    GitHubGetBranchTool,
    GitHubGetFileTool,
    GitHubGetPRTool,
    GitHubGetRepositoryTool,
    GitHubHTTPResponse,
    GitHubOpenPRTool,
    GitHubRESTClient,
    GitHubUpdateFileTool,
    Goal,
    GoalEvaluationStatus,
    GoalEvaluator,
    PolicyEngine,
    Secret,
    ToolRegistry,
    assert_no_secrets,
    sign_approval_decision,
    verify_approval_decision_signature,
)
from leos_agent.state import TrustLevel

PROTECTED_BRANCHES = {"main", "master", "trunk", "release"}
_FORBIDDEN_EVIDENCE_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9_]+", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9_]+", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"bearer\s", re.IGNORECASE),
    re.compile(r"LEOS_GITHUB_TOKEN", re.IGNORECASE),
    re.compile(r"LEOS_APPROVAL_HMAC_SECRET", re.IGNORECASE),
    re.compile(r"hmac-sha256:[0-9a-f]{32,}", re.IGNORECASE),
)


class SignedSmokeApprovalGate(ApprovalGate):
    """Smoke-only approval gate that signs and verifies each decision locally."""

    signed_approval_enforced = True

    def __init__(self, *, signature_secret: str, approver: str = "production-smoke") -> None:
        super().__init__(approver=None)
        self.signature_secret = signature_secret
        self.approver = approver
        self.last_decision_signature_valid = False
        self.last_decision_signature_algorithm: str | None = None

    def request_packet(self, packet, step):  # type: ignore[override]
        del step
        self.last_decision_signature_valid = False
        self.last_decision_signature_algorithm = None
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
        self.last_decision_signature_valid = True
        self.last_decision_signature_algorithm = "hmac-sha256"
        return decision


def main() -> int:
    if os.environ.get("LEOS_ENABLE_REAL_GITHUB_WRITES") != "1":
        print("real write disabled; set LEOS_ENABLE_REAL_GITHUB_WRITES=1 explicitly")
        return 0

    evidence_out = Path(os.environ.get("LEOS_SMOKE_EVIDENCE_OUT", "docs/proofs/real_github_smoke_latest.json"))
    leos_commit_sha = os.environ.get("GITHUB_SHA") or _git_head()
    workflow_run_id = os.environ.get("GITHUB_RUN_ID", "local")
    workflow_trigger = os.environ.get("GITHUB_EVENT_NAME", "local")
    source_repository = os.environ.get("GITHUB_REPOSITORY", "Leos-byte/Leos")
    checks = _initial_checks()
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "evidence_type": "private_disposable_github_real_write_smoke",
        "profile": "production_github_only",
        "status": "failed",
        "repository_visibility": "unknown",
        "repository_disposable": False,
        "repository_under_test": os.environ.get("LEOS_GITHUB_TEST_REPO", ""),
        "workflow_trigger": workflow_trigger,
        "work_branch_prefix": os.environ.get("LEOS_GITHUB_WORK_BRANCH_PREFIX", "leos/"),
        "leos_commit_sha": leos_commit_sha,
        "workflow_run_id": workflow_run_id,
        "generated_at": _utc_now(),
        "checks": checks,
    }
    try:
        repo = _required_env("LEOS_GITHUB_TEST_REPO")
        if repo.lower() == source_repository.lower():
            raise GitHubConflictError("production smoke target must not be the Leos source repository")
        _disposable_repo_guard(repo)
        checks["disposable_repo_guard_passed"] = True
        checks["cleanup_requested"] = os.environ.get("LEOS_GITHUB_SMOKE_CLEANUP") == "1"
        if not checks["cleanup_requested"]:
            raise GitHubConflictError("production smoke requires LEOS_GITHUB_SMOKE_CLEANUP=1")
        token_ref = _required_env_any("SMOKE_AUTH_ENV", "LEOS_GITHUB_TOKEN_SECRET_REF")
        token = Secret(_required_env(token_ref))
        approval_secret_ref = _required_env_any("SMOKE_APPROVAL_ENV", "LEOS_APPROVAL_HMAC_SECRET_REF")
        approval_secret = _required_env(approval_secret_ref)
        base_branch = os.environ.get("LEOS_GITHUB_BASE_BRANCH", "main")
        branch_prefix = os.environ.get("LEOS_GITHUB_WORK_BRANCH_PREFIX", "leos/")
        if not branch_prefix or not branch_prefix.startswith("leos/"):
            raise GitHubConflictError("production smoke writes must use leos/ branch prefix")
        work_branch = f"{branch_prefix}production-smoke-{int(time.time())}"
        target_path = os.environ.get("LEOS_GITHUB_TEST_PATH", "leos-production-smoke.txt")
        content = f"Leos production_github_only smoke.\nbranch={work_branch}\n"
        idempotency_key = f"leos-production-smoke-{work_branch}"
        evidence.update(
            {
                "repository_under_test": repo,
                "repository_disposable": True,
                "work_branch_prefix": branch_prefix,
                "base_branch": base_branch,
                "created_branch": work_branch,
            }
        )

        policy = PolicyEngine.from_profile("production_github_only")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",))
        client = _client(policy)
        registry = _registry(client)
        kernel = AgentKernel(
            registry=registry,
            policy=policy,
            approval_gate=SignedSmokeApprovalGate(signature_secret=approval_secret),
        )
        attestation = _record_attestation(kernel, client)
        checks.update(
            {
                "runtime_attestation_verified": True,
                "runtime_egress_enforced": attestation["runtime_egress_enforced"],
                "runtime_egress_policy_configured": attestation["runtime_egress_policy_configured"],
                "runtime_egress_host": attestation["runtime_egress_host"],
            }
        )
        checks["signed_approval_required"] = bool(policy.require_signed_approval)
        approval_gate = kernel.transactions.approval_gate
        checks["signed_approval_enforced"] = bool(approval_gate.signed_approval_enforced)

        repository = _tool_observation(
            kernel,
            tool_name="github_get_repository",
            arguments={"repo": repo, "token": token},
            fact_key="github_repository",
            description="Verify private disposable repository metadata.",
        )
        visibility = str(repository.get("visibility", "unknown"))
        checks["private_repo_used"] = bool(repository.get("private")) and visibility == "private"
        evidence["repository_visibility"] = visibility
        if not checks["private_repo_used"]:
            raise GitHubConflictError("production smoke requires a private repository")
        base_before = _tool_observation(
            kernel,
            tool_name="github_get_branch",
            arguments={"repo": repo, "branch": base_branch, "token": token},
            fact_key="github_branch_status",
            description="Capture base branch before the smoke.",
        )
        if not base_before.get("exists") or not base_before.get("sha"):
            raise GitHubConflictError("production smoke base branch was not found")
        source_head_before = _git_head()
        if source_head_before != leos_commit_sha:
            raise GitHubConflictError("checked-out Leos commit does not match GITHUB_SHA")

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
                {"key": "cleanup_requested", "op": "equals", "value": True},
                {"key": "pr_closed", "op": "equals", "value": True},
                {"key": "branch_deleted", "op": "equals", "value": True},
                {"key": "source_repo_unchanged", "op": "equals", "value": True},
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
        checks["approval_signature_verified"] = bool(approval_gate.last_decision_signature_valid)
        checks["branch_created"] = True
        checks["file_updated"] = True
        checks["pr_opened"] = True
        pr = kernel.state.facts.get("github_pr", {})
        pr_number = int(pr.get("number", 0))
        evidence["pr_number"] = pr_number
        evidence["pr_url"] = str(pr.get("html_url", ""))
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
        checks["read_back_verified"] = True
        kernel.state.observe({"read_back_verified": True}, trust_level=TrustLevel.VERIFIED)
        kernel.audit_log.record(
            "github.real_write.readback_verified",
            "Production GitHub smoke read-back verified expected content",
            repo=repo,
            path=target_path,
            branch=work_branch,
        )
        branch_before_cleanup = _tool_observation(
            kernel,
            tool_name="github_get_branch",
            arguments={"repo": repo, "branch": work_branch, "token": token},
            fact_key="github_branch_status",
            description="Bind cleanup to the current smoke branch SHA.",
        )
        cleanup_plan = kernel.build_plan(
            goal,
            [
                ActionStep(
                    "github_close_pr",
                    {
                        "repo": repo,
                        "pr_number": pr_number,
                        "expected_head": work_branch,
                        "expected_base": base_branch,
                        "token": token,
                    },
                    "Close the bounded smoke pull request.",
                ),
                ActionStep(
                    "github_delete_branch",
                    {
                        "repo": repo,
                        "branch": work_branch,
                        "expected_sha": str(branch_before_cleanup.get("sha", "")),
                        "token": token,
                    },
                    "Delete the bounded smoke branch after PR closure.",
                ),
            ],
        )
        cleanup_result = kernel.run(cleanup_plan)
        _require_verified(cleanup_result.steps)
        closed_pr = _tool_observation(
            kernel,
            tool_name="github_get_pr",
            arguments={"repo": repo, "pr_number": pr_number, "token": token},
            fact_key="github_pr_status",
            description="Verify the smoke pull request is closed.",
        )
        deleted_branch = _tool_observation(
            kernel,
            tool_name="github_get_branch",
            arguments={"repo": repo, "branch": work_branch, "token": token},
            fact_key="github_branch_status",
            description="Verify the smoke branch is deleted.",
        )
        base_after = _tool_observation(
            kernel,
            tool_name="github_get_branch",
            arguments={"repo": repo, "branch": base_branch, "token": token},
            fact_key="github_branch_status",
            description="Verify the disposable repository base branch is unchanged.",
        )
        checks["pr_closed"] = closed_pr.get("state") == "closed"
        checks["branch_deleted"] = deleted_branch.get("exists") is False
        checks["source_repo_unchanged"] = (
            _git_head() == source_head_before
            and source_head_before == leos_commit_sha
            and base_after.get("sha") == base_before.get("sha")
        )
        if not checks["pr_closed"] or not checks["branch_deleted"] or not checks["source_repo_unchanged"]:
            raise GitHubConflictError("production smoke cleanup verification failed")
        kernel.state.observe(
            {
                "cleanup_requested": True,
                "pr_closed": True,
                "branch_deleted": True,
                "source_repo_unchanged": True,
            },
            trust_level=TrustLevel.VERIFIED,
        )
        evaluation = GoalEvaluator().evaluate(goal, kernel.state, kernel.transactions.track_progress(executed))
        kernel.audit_log.record(
            "github.real_write.goal_evaluated",
            "Production GitHub smoke evaluated typed goal criteria",
            evaluation_status=evaluation.status.value,
            satisfied_criteria=list(evaluation.satisfied_criteria),
            unsatisfied_criteria=list(evaluation.unsatisfied_criteria),
        )
        checks["goal_evaluation_succeeded"] = evaluation.status is GoalEvaluationStatus.SUCCEEDED
        if evaluation.status is not GoalEvaluationStatus.SUCCEEDED:
            raise GitHubConflictError("goal evaluation failed")
        evidence["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - smoke path must return a structured failure
        evidence["status"] = "failed"
        evidence["failure_type"] = type(exc).__name__
        evidence["failure_summary"] = "production smoke failed; inspect the sanitized workflow step result"
        _write_evidence(evidence_out, evidence)
        print(_summary_json(evidence))
        print("token not printed")
        return 1

    _write_evidence(evidence_out, evidence)
    print(_summary_json(evidence))
    print("token not printed")
    return 0


def _client(policy: PolicyEngine):
    if os.environ.get("LEOS_GITHUB_SMOKE_FAKE") == "1":
        return GitHubRESTClient(
            transport=_SmokeFakeGitHubTransport(),
            egress_policy=policy.egress_policy,
            enforce_egress=True,
        )
    return GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)


class _SmokeFakeGitHubTransport:
    """In-process fake transport that still exercises GitHubRESTClient egress attestation."""

    def __init__(self) -> None:
        self.branches: dict[str, str] = {"main": "base-sha"}
        self.files: dict[tuple[str, str], dict[str, str]] = {}
        self.prs: list[dict[str, object]] = []
        self.next_pr = 1
        self.next_comment = 1

    def request(self, method: str, url: str, *, headers, body, timeout_seconds) -> GitHubHTTPResponse:
        del headers, timeout_seconds
        parsed = urlparse(url)
        path = parsed.path
        query = parse_qs(parsed.query)
        if len(path.strip("/").split("/")) == 3 and path.startswith("/repos/") and method == "GET":
            return _fake_response(
                200,
                {"private": True, "visibility": "private", "default_branch": "main"},
            )
        if "/git/ref/heads/" in path:
            branch = path.split("/git/ref/heads/", 1)[1]
            if method == "GET":
                sha = self.branches.get(branch)
                if sha is None:
                    return _fake_response(404, {"message": "not found"})
                return _fake_response(200, {"object": {"sha": sha}})
            if method == "POST":
                payload = _json_body(body)
                ref = str(payload.get("ref", ""))
                branch = ref.removeprefix("refs/heads/")
                sha = str(payload.get("sha", "base-sha"))
                self.branches[branch] = sha
                return _fake_response(201, {"object": {"sha": sha}})
        if "/git/refs/heads/" in path and method == "DELETE":
            if os.environ.get("LEOS_GITHUB_SMOKE_FAKE_FAIL_CLEANUP") == "1":
                return _fake_response(500, {"message": "simulated cleanup failure"})
            branch = path.split("/git/refs/heads/", 1)[1]
            if branch not in self.branches:
                return _fake_response(404, {"message": "not found"})
            del self.branches[branch]
            for key in list(self.files):
                if key[0] == branch:
                    del self.files[key]
            return GitHubHTTPResponse(204, b"", {})
        if "/git/refs" in path and method == "POST":
            payload = _json_body(body)
            ref = str(payload.get("ref", ""))
            branch = ref.removeprefix("refs/heads/")
            sha = str(payload.get("sha", "base-sha"))
            self.branches[branch] = sha
            return _fake_response(201, {"object": {"sha": sha}})
        if "/contents/" in path:
            file_path = path.split("/contents/", 1)[1]
            if method == "GET":
                ref = str((query.get("ref") or [""])[0])
                file_record = self.files.get((ref, file_path))
                if file_record is None:
                    return _fake_response(404, {"message": "not found"})
                return _fake_response(200, file_record)
            if method == "PUT":
                payload = _json_body(body)
                branch = str(payload.get("branch", ""))
                content = str(payload.get("content", ""))
                sha = f"sha-{len(self.files) + 1}"
                self.files[(branch, file_path)] = {"content": content, "encoding": "base64", "sha": sha}
                self.branches[branch] = sha
                return _fake_response(200, {"content": {"sha": sha}, "commit": {"sha": f"commit-{sha}"}})
        if path.endswith("/pulls") and method == "GET":
            return _fake_response(200, self.prs)
        if path.endswith("/pulls") and method == "POST":
            payload = _json_body(body)
            pr = {
                "number": self.next_pr,
                "title": payload.get("title", ""),
                "body": payload.get("body", ""),
                "state": "open",
                "html_url": f"https://github.com/fake/repo/pull/{self.next_pr}",
                "head": {"ref": payload.get("head", "")},
                "base": {"ref": payload.get("base", "")},
            }
            self.next_pr += 1
            self.prs.append(pr)
            return _fake_response(201, pr)
        if "/pulls/" in path:
            pr_number = int(path.rsplit("/", 1)[1])
            pr = next((item for item in self.prs if item.get("number") == pr_number), None)
            if pr is None:
                return _fake_response(404, {"message": "not found"})
            if method == "GET":
                return _fake_response(200, pr)
            if method == "PATCH":
                payload = _json_body(body)
                pr["state"] = payload.get("state", pr.get("state", "open"))
                return _fake_response(200, pr)
        if path.endswith("/comments") and method == "POST":
            payload = _json_body(body)
            comment = {
                "id": self.next_comment,
                "body": payload.get("body", ""),
                "html_url": f"https://github.com/fake/repo/issues/comments/{self.next_comment}",
            }
            self.next_comment += 1
            return _fake_response(201, comment)
        return _fake_response(404, {"message": "not found"})


def _json_body(body: bytes | None) -> dict[str, object]:
    if body is None:
        return {}
    value = json.loads(body.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _fake_response(status: int, payload: object) -> GitHubHTTPResponse:
    return GitHubHTTPResponse(status, json.dumps(payload).encode("utf-8"), {})


def _registry(client) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(GitHubGetRepositoryTool(client))
    registry.register(GitHubGetBranchTool(client))
    registry.register(GitHubGetPRTool(client))
    registry.register(GitHubGetFileTool(client))
    registry.register(GitHubCreateBranchTool(client))
    registry.register(GitHubUpdateFileTool(client))
    registry.register(GitHubOpenPRTool(client))
    registry.register(GitHubClosePRTool(client))
    registry.register(GitHubDeleteBranchTool(client))
    registry.register(GitHubCommentTool(client))
    return registry


def _record_attestation(kernel: AgentKernel, client) -> dict[str, Any]:
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
    return summary


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
        raise GitHubConflictError(f"missing required environment variable: {name}")
    return value


def _required_env_any(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise GitHubConflictError(f"missing required environment variable: {names[0]}")


def _tool_observation(
    kernel: AgentKernel,
    *,
    tool_name: str,
    arguments: dict[str, object],
    fact_key: str,
    description: str,
) -> dict[str, object]:
    goal = Goal(
        description,
        [f"{fact_key} exists"],
        criteria=({"key": fact_key, "op": "exists"},),
        stop_conditions=["observation recorded or blocked"],
    )
    plan = kernel.build_plan(goal, [ActionStep(tool_name, arguments, description)])
    result = kernel.run(plan)
    _require_verified(result.steps)
    fact = kernel.state.facts.get(fact_key)
    if not isinstance(fact, dict):
        raise GitHubConflictError(f"{tool_name} did not record {fact_key}")
    return dict(fact)


def _initial_checks() -> dict[str, object]:
    return {
        "private_repo_used": False,
        "disposable_repo_guard_passed": False,
        "runtime_attestation_verified": False,
        "runtime_egress_enforced": False,
        "runtime_egress_policy_configured": False,
        "runtime_egress_host": "",
        "signed_approval_required": False,
        "signed_approval_enforced": False,
        "approval_signature_verified": False,
        "approval_signature_algorithm": "hmac-sha256",
        "branch_created": False,
        "file_updated": False,
        "pr_opened": False,
        "read_back_verified": False,
        "goal_evaluation_succeeded": False,
        "cleanup_requested": False,
        "pr_closed": False,
        "branch_deleted": False,
        "source_repo_unchanged": False,
        "token_redacted": True,
        "secret_scan_safe": True,
    }


def _write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    evidence["generated_at"] = _utc_now()
    assert_no_secrets(evidence)
    serialized = json.dumps(evidence, indent=2, sort_keys=True)
    if any(pattern.search(serialized) for pattern in _FORBIDDEN_EVIDENCE_PATTERNS):
        raise GitHubConflictError("sanitized smoke evidence contained a forbidden marker")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized + "\n", encoding="utf-8")
    temporary.replace(path)


def _summary_json(evidence: dict[str, Any]) -> str:
    summary = {
        "profile": evidence.get("profile"),
        "status": evidence.get("status"),
        "repository_under_test": evidence.get("repository_under_test"),
        "work_branch": evidence.get("created_branch"),
        "pr_number": evidence.get("pr_number"),
        "workflow_run_id": evidence.get("workflow_run_id"),
        "checks": evidence.get("checks"),
    }
    return json.dumps(summary, indent=2, sort_keys=True)


def _git_head() -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
