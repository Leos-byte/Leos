from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from examples.github_rest_agent import run_production_github_smoke
from examples.github_rest_agent.run_real_write_gated import (
    _evaluate_real_write_goal,
    _production_github_policy,
    _tool_mediated_get_file,
)
from leos_agent import (
    ActionStep,
    AgentKernel,
    AuditLog,
    GitHubConflictError,
    GitHubGetFileTool,
    Goal,
    InMemoryGitHubClient,
    LeosError,
    Secret,
    ToolRegistry,
    TransactionPlan,
)
from leos_agent.enums import StepStatus
from leos_agent.github_tools import GitHubUpdateFileTool, _token_or_error
from leos_agent.state import WorldState


class GitHubRealWriteGatedTests(unittest.TestCase):
    def test_real_write_script_disabled_by_default(self) -> None:
        script = Path("examples/github_rest_agent/run_real_write_gated.py")
        env = dict(os.environ)
        env.pop("LEOS_ENABLE_REAL_GITHUB_WRITES", None)
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(script)],
            check=False,
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("real write disabled", result.stdout)

    def test_production_smoke_disabled_by_default(self) -> None:
        script = Path("examples/github_rest_agent/run_production_github_smoke.py")
        env = dict(os.environ)
        env.pop("LEOS_ENABLE_REAL_GITHUB_WRITES", None)
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(script)],
            check=False,
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("real write disabled", result.stdout)

    def test_production_smoke_refuses_without_disposable_repo_flag(self) -> None:
        env = {
            "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
            "LEOS_GITHUB_TEST_REPO": "owner/leos-smoke-test",
            "LEOS_GITHUB_TOKEN_SECRET_REF": "TOKEN_ENV",
            "TOKEN_ENV": "ghp_test_secret",
            "LEOS_APPROVAL_HMAC_SECRET_REF": "APPROVAL_ENV",
            "APPROVAL_ENV": "approval-secret",
            "LEOS_GITHUB_SMOKE_FAKE": "1",
        }
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout"):
            result = run_production_github_smoke.main()

        self.assertEqual(result, 1)

    def test_production_smoke_fake_path_succeeds_without_token_leak(self) -> None:
        env = {
            "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
            "LEOS_GITHUB_TEST_REPO": "owner/leos-smoke-test",
            "LEOS_GITHUB_TEST_REPO_MUST_BE_DISPOSABLE": "1",
            "LEOS_GITHUB_TOKEN_SECRET_REF": "TOKEN_ENV",
            "TOKEN_ENV": "ghp_test_secret",
            "LEOS_APPROVAL_HMAC_SECRET_REF": "APPROVAL_ENV",
            "APPROVAL_ENV": "approval-secret",
            "LEOS_GITHUB_SMOKE_FAKE": "1",
        }
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout") as stdout:
            result = run_production_github_smoke.main()

        self.assertEqual(result, 0)
        output = "".join(str(call.args[0]) for call in stdout.write.call_args_list if call.args)
        self.assertIn("production_github_only", output)
        self.assertIn("succeeded", output)
        self.assertNotIn("ghp_test_secret", output)

    def test_production_smoke_accepts_neutral_secret_env_names(self) -> None:
        env = {
            "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
            "LEOS_GITHUB_TEST_REPO": "owner/leos-smoke-test",
            "LEOS_GITHUB_TEST_REPO_MUST_BE_DISPOSABLE": "1",
            "SMOKE_AUTH_ENV": "SMOKE_AUTH_VALUE",
            "SMOKE_AUTH_VALUE": "ghp_test_secret",
            "SMOKE_APPROVAL_ENV": "SMOKE_APPROVAL_VALUE",
            "SMOKE_APPROVAL_VALUE": "approval-secret",
            "LEOS_GITHUB_SMOKE_FAKE": "1",
        }
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout") as stdout:
            result = run_production_github_smoke.main()

        self.assertEqual(result, 0)
        output = "".join(str(call.args[0]) for call in stdout.write.call_args_list if call.args)
        self.assertIn("production_github_only", output)
        self.assertNotIn("ghp_test_secret", output)

    def test_protected_branch_cleanup_rejected(self) -> None:
        client = InMemoryGitHubClient()
        with self.assertRaises(LeosError):
            client.delete_branch("owner/repo", "main", token="token")

    def test_update_without_expected_guard_blocked(self) -> None:
        tool = GitHubUpdateFileTool(InMemoryGitHubClient())
        result = tool.dry_run(
            {"repo": "owner/repo", "path": "x.txt", "branch": "b", "content": "x", "message": "m"},
            WorldState(),
        )
        self.assertFalse(result.ok)

    def test_plain_token_rejected_before_client_use(self) -> None:
        token, error = _token_or_error({"token": "ghp_plain_token_value"})
        self.assertIsNone(token)
        self.assertIsNotNone(error)

    def test_secret_token_records_only_fingerprint(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("owner/repo", 1, title="t", body="b")
        client.read_issue("owner/repo", 1, token=Secret("token-value").unwrap())
        self.assertEqual(client.accepted_token_count, 1)
        self.assertNotIn("token-value", repr(client))

    def test_tool_mediated_preread_records_audit_event(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("owner/repo", "main", "x.txt", "content")
        kernel = _github_get_file_kernel(client)

        result = _tool_mediated_get_file(
            kernel,
            repo="owner/repo",
            path="x.txt",
            ref="main",
            token=Secret("ghp_test_secret"),
            purpose="preread",
            allow_missing=False,
        )

        self.assertEqual(result["content"], "content")
        event_types = [event.event_type for event in kernel.audit_log.events]
        self.assertIn("github.real_write.tool_mediated_preread", event_types)
        self.assertNotIn("github.real_write.readback_direct_client_call", event_types)
        self.assertNotIn("ghp_test_secret", repr(kernel.audit_log.records()))

    def test_tool_mediated_preread_missing_can_proceed(self) -> None:
        kernel = _github_get_file_kernel(InMemoryGitHubClient())

        result = _tool_mediated_get_file(
            kernel,
            repo="owner/repo",
            path="new.txt",
            ref="main",
            token=Secret("ghp_test_secret"),
            purpose="preread",
            allow_missing=True,
        )

        self.assertIsNone(result)
        event_types = [event.event_type for event in kernel.audit_log.events]
        self.assertIn("github.real_write.tool_mediated_preread_missing", event_types)

    def test_tool_mediated_readback_missing_fails(self) -> None:
        kernel = _github_get_file_kernel(InMemoryGitHubClient())

        with self.assertRaises(LeosError):
            _tool_mediated_get_file(
                kernel,
                repo="owner/repo",
                path="missing.txt",
                ref="branch",
                token=Secret("ghp_test_secret"),
                purpose="readback",
                allow_missing=False,
            )

    def test_real_write_goal_evaluation_fails_without_readback_fact(self) -> None:
        kernel = _github_get_file_kernel(InMemoryGitHubClient())
        kernel.state.observe(
            {
                "github_branch": {"branch": "feature"},
                "github_file_updated": {"path": "x.txt"},
                "github_pr": {"number": 1, "state": "open"},
            }
        )
        summary: dict[str, object] = {}

        with self.assertRaises(GitHubConflictError):
            _evaluate_real_write_goal(kernel, _verified_real_write_plan(), summary)

        self.assertEqual(summary["evaluation_status"], "failed")
        self.assertTrue(
            any(event.event_type == "github.real_write.goal_evaluated" for event in kernel.audit_log.events)
        )

    def test_real_write_goal_evaluation_succeeds_with_all_typed_criteria(self) -> None:
        kernel = _github_get_file_kernel(InMemoryGitHubClient())
        kernel.state.observe(
            {
                "github_branch": {"branch": "feature"},
                "github_file_updated": {"path": "x.txt"},
                "github_pr": {"number": 1, "state": "open"},
                "read_back_verified": True,
            }
        )
        summary: dict[str, object] = {}

        _evaluate_real_write_goal(kernel, _verified_real_write_plan(), summary)

        self.assertEqual(summary["evaluation_status"], "succeeded")
        self.assertNotIn("ghp_", repr(summary))
        self.assertTrue(
            any(event.event_type == "github.real_write.goal_evaluated" for event in kernel.audit_log.events)
        )


def _github_get_file_kernel(client: InMemoryGitHubClient) -> AgentKernel:
    registry = ToolRegistry()
    registry.register(GitHubGetFileTool(client))
    return AgentKernel(
        registry=registry,
        policy=_production_github_policy(),
        audit_log=AuditLog(),
    )


def _verified_real_write_plan() -> TransactionPlan:
    goal = Goal(
        "Gated GitHub real-write smoke",
        ["file updated", "PR opened"],
        criteria=(
            {"key": "github_branch", "op": "exists"},
            {"key": "github_file_updated", "op": "exists"},
            {"key": "github_pr", "op": "exists"},
            {"key": "read_back_verified", "op": "equals", "value": True},
        ),
        stop_conditions=["done"],
    )
    steps = [ActionStep("github_update_file", {}, "verified"), ActionStep("github_open_pr", {}, "verified")]
    for step in steps:
        step.status = StepStatus.VERIFIED
    return TransactionPlan(goal, steps)


if __name__ == "__main__":
    unittest.main()
