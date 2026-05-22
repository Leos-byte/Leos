"""Explicitly gated GitHub real-write smoke path.

This script is disabled by default and is intended for manual workflow_dispatch
verification only. It never prints the token value.
"""

from __future__ import annotations

import json
import os
import sys
import time

from leos_agent import (
    ActionStep,
    AgentKernel,
    ApprovalGate,
    EgressPolicy,
    GitHubConflictError,
    GitHubCreateBranchTool,
    GitHubOpenPRTool,
    GitHubRESTClient,
    GitHubUpdateFileTool,
    Goal,
    PolicyEngine,
    Secret,
    ToolRegistry,
)

PROTECTED_BRANCHES = {"main", "master", "trunk", "release"}


def main() -> int:
    if os.environ.get("LEOS_ENABLE_REAL_GITHUB_WRITES") != "1":
        print("real write disabled; set LEOS_ENABLE_REAL_GITHUB_WRITES=1 explicitly")
        return 0

    repo = _required_env("LEOS_GITHUB_TEST_REPO")
    token_ref = _required_env("LEOS_GITHUB_TOKEN_SECRET_REF")
    token_value = _required_env(token_ref)
    base_branch = os.environ.get("LEOS_GITHUB_BASE_BRANCH", "main")
    branch_prefix = os.environ.get("LEOS_GITHUB_WORK_BRANCH_PREFIX", "leos/")
    if base_branch in PROTECTED_BRANCHES and not branch_prefix:
        print("refusing to write directly to a protected branch", file=sys.stderr)
        return 2
    work_branch = f"{branch_prefix}real-write-smoke-{int(time.time())}"
    target_path = os.environ.get("LEOS_GITHUB_TEST_PATH", "leos-real-write-smoke.txt")
    idempotency_key = f"leos-real-write-smoke-{work_branch}"
    content = f"Leos real-write gated smoke test.\nbranch={work_branch}\n"
    token = Secret(token_value)
    client = GitHubRESTClient()
    registry = ToolRegistry()
    registry.register(GitHubCreateBranchTool(client))
    registry.register(GitHubUpdateFileTool(client))
    registry.register(GitHubOpenPRTool(client))
    kernel = AgentKernel(
        registry=registry,
        policy=_production_github_policy(),
        approval_gate=ApprovalGate(lambda step: True),
    )

    summary: dict[str, object] = {"repo": repo, "base_branch": base_branch, "work_branch": work_branch}
    try:
        previous = None
        try:
            current = client.get_file(repo, target_path, base_branch, token=token.unwrap())
            previous = str(current.get("content", ""))
            expected_sha = str(current.get("sha", ""))
        except Exception:
            expected_sha = None
            previous = ""
        goal = Goal(
            "Gated GitHub real-write smoke",
            ["file updated", "PR opened"],
            criteria=(
                {"key": "github_file_updated", "op": "exists"},
                {"key": "github_pr", "op": "exists"},
            ),
            stop_conditions=["PR opened or blocked"],
        )
        plan = kernel.build_plan(
            goal,
            [
                ActionStep(
                    "github_create_branch",
                    {"repo": repo, "branch": work_branch, "base": base_branch, "token": token},
                    "Create isolated work branch for gated smoke test.",
                ),
                ActionStep(
                    "github_update_file",
                    _without_none(
                        {
                            "repo": repo,
                            "path": target_path,
                            "branch": work_branch,
                            "content": content,
                            "message": "Leos gated real-write smoke",
                            "expected_sha": expected_sha,
                            "expected_previous": previous if expected_sha is None else None,
                            "token": token,
                        }
                    ),
                    "Write smoke file using optimistic guard.",
                ),
                ActionStep(
                    "github_open_pr",
                    {
                        "repo": repo,
                        "title": "Leos gated real-write smoke",
                        "body": "Manual gated real-write verification.",
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
        if not all(step.status.value == "verified" for step in executed.steps):
            raise GitHubConflictError("transaction did not verify every real-write step")
        summary["branch_created"] = "github_branch" in kernel.state.facts
        summary["file_updated"] = "github_file_updated" in kernel.state.facts
        read_back = client.get_file(repo, target_path, work_branch, token=token.unwrap())
        kernel.audit_log.record(
            "github.real_write.readback_direct_client_call",
            "Read-back verification used direct GitHubRESTClient call; "
            "keep token redacted and migrate to tool-mediated read-back.",
            repo=repo,
            path=target_path,
            branch=work_branch,
        )
        if read_back.get("content") != content:
            raise GitHubConflictError("read-back verification failed")
        summary["read_back_verified"] = True
        pr = kernel.state.facts.get("github_pr", {})
        summary["pr_number"] = pr.get("number")
        summary["idempotency_key"] = idempotency_key
    except Exception as exc:  # noqa: BLE001 - script should return structured failure
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        print(json.dumps(summary, indent=2, sort_keys=True))
        print("token not printed")
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    print("token not printed")
    return 0


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def _without_none(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if item is not None}


def _production_github_policy() -> PolicyEngine:
    policy = PolicyEngine.from_profile("production_locked_down")
    policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",))
    return policy


if __name__ == "__main__":
    raise SystemExit(main())
