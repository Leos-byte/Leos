from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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

    def test_real_write_workflow_uses_public_checkout_without_credentials(self) -> None:
        workflow = Path(".github/workflows/github-real-write.yml").read_text(encoding="utf-8")
        self.assertNotIn("actions/checkout", workflow)
        self.assertNotIn("persist-credentials", workflow)
        self.assertIn("git fetch --depth 1 origin", workflow)

    def test_production_smoke_refuses_without_disposable_repo_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _smoke_env(Path(tmp) / "evidence.json")
            env.pop("LEOS_GITHUB_TEST_REPO_MUST_BE_DISPOSABLE")
            with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout"):
                result = run_production_github_smoke.main()

        self.assertEqual(result, 1)

    def test_production_smoke_fake_path_succeeds_without_token_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence_path = Path(tmp) / "evidence.json"
            env = _smoke_env(evidence_path)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout") as stdout:
                result = run_production_github_smoke.main()
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        output = "".join(str(call.args[0]) for call in stdout.write.call_args_list if call.args)
        self.assertIn("production_github_only", output)
        self.assertIn("succeeded", output)
        self.assertNotIn("ghp_test_secret", output)
        self.assertEqual(evidence["status"], "passed")
        self.assertTrue(evidence["checks"]["pr_closed"])
        self.assertTrue(evidence["checks"]["branch_deleted"])
        self.assertTrue(evidence["checks"]["source_repo_unchanged"])
        evidence_text = json.dumps(evidence)
        self.assertNotIn("ghp_test_secret", evidence_text)
        self.assertNotIn("approval-secret", evidence_text)
        self.assertNotIn("Authorization", evidence_text)

    def test_production_smoke_accepts_neutral_secret_env_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _smoke_env(Path(tmp) / "evidence.json")
            with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout") as stdout:
                result = run_production_github_smoke.main()

        self.assertEqual(result, 0)
        output = "".join(str(call.args[0]) for call in stdout.write.call_args_list if call.args)
        self.assertIn("production_github_only", output)
        self.assertNotIn("ghp_test_secret", output)

    def test_production_smoke_cleanup_failure_returns_sanitized_failure_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence_path = Path(tmp) / "evidence.json"
            env = _smoke_env(evidence_path)
            env["LEOS_GITHUB_SMOKE_FAKE_FAIL_CLEANUP"] = "1"
            with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout") as stdout:
                result = run_production_github_smoke.main()
            evidence_text = evidence_path.read_text(encoding="utf-8")
            evidence = json.loads(evidence_text)

        self.assertEqual(result, 1)
        self.assertEqual(evidence["status"], "failed")
        self.assertTrue(evidence["checks"]["cleanup_requested"])
        self.assertFalse(evidence["checks"]["branch_deleted"])
        self.assertNotIn("ghp_test_secret", evidence_text)
        output = "".join(str(call.args[0]) for call in stdout.write.call_args_list if call.args)
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


def _smoke_env(evidence_path: Path) -> dict[str, str]:
    head = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
        "LEOS_GITHUB_SMOKE_CLEANUP": "1",
        "LEOS_GITHUB_TEST_REPO": "owner/leos-smoke-test",
        "LEOS_GITHUB_TEST_REPO_MUST_BE_DISPOSABLE": "1",
        "LEOS_GITHUB_BASE_BRANCH": "main",
        "LEOS_GITHUB_WORK_BRANCH_PREFIX": "leos/",
        "LEOS_GITHUB_SMOKE_FAKE": "1",
        "LEOS_SMOKE_EVIDENCE_OUT": str(evidence_path),
        "SMOKE_AUTH_ENV": "SMOKE_AUTH_VALUE",
        "SMOKE_AUTH_VALUE": "ghp_test_secret",
        "SMOKE_APPROVAL_ENV": "SMOKE_APPROVAL_VALUE",
        "SMOKE_APPROVAL_VALUE": "approval-secret",
        "GITHUB_SHA": head,
        "GITHUB_RUN_ID": "123",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "Leos-byte/Leos",
    }


if __name__ == "__main__":
    unittest.main()
