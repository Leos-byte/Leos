from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from typing import Any

from leos_agent import (
    ActionStep,
    ApprovalGate,
    AuditLog,
    CausalGraph,
    Goal,
    Permission,
    PolicyEngine,
    Reversibility,
    TransactionManager,
    TransactionPlan,
)
from leos_agent.errors import LeosError
from leos_agent.github_client import GitHubHTTPResponse, GitHubRESTClient
from leos_agent.github_tools import (
    GitHubCheckCIStatusTool,
    GitHubClosePRTool,
    GitHubCommentTool,
    GitHubCreateBranchTool,
    GitHubDeleteBranchTool,
    GitHubGetBranchTool,
    GitHubGetFileTool,
    GitHubGetPRTool,
    GitHubGetRepositoryTool,
    GitHubOpenPRTool,
    GitHubReadIssueTool,
    GitHubUpdateFileTool,
    InMemoryGitHubClient,
)
from leos_agent.state import WorldState
from leos_agent.tools import Secret, ToolRegistry


class FakeTransport:
    def __init__(self, responses: list[GitHubHTTPResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> GitHubHTTPResponse:
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)


def _response(status: int, payload: Any) -> GitHubHTTPResponse:
    return GitHubHTTPResponse(status, json.dumps(payload).encode("utf-8"), {})


class GitHubToolsTests(unittest.TestCase):
    def test_read_issue_low_risk_dry_run(self) -> None:
        tool = GitHubReadIssueTool(InMemoryGitHubClient())

        result = tool.dry_run({"repo": "o/r", "issue_number": 1}, WorldState())

        self.assertTrue(result.ok)
        self.assertEqual(tool.spec.default_risk.value, "low")

    def test_update_file_requires_expected_guard(self) -> None:
        tool = GitHubUpdateFileTool(InMemoryGitHubClient())

        result = tool.dry_run(
            {"repo": "o/r", "path": "a.py", "branch": "b", "content": "x", "message": "m"}, WorldState()
        )

        self.assertFalse(result.ok)
        self.assertIn("expected_sha", result.message)

    def test_open_pr_is_compensatable_and_needs_approval(self) -> None:
        client = InMemoryGitHubClient()
        tool = GitHubOpenPRTool(client)
        registry = ToolRegistry()
        registry.register(tool)
        manager = TransactionManager(registry, PolicyEngine(), CausalGraph(), AuditLog())
        plan = TransactionPlan(
            Goal("pr", ["opened"], stop_conditions=["done"]),
            [
                ActionStep(
                    "github_open_pr", {"repo": "o/r", "title": "t", "body": "b", "head": "h", "base": "main"}, "pr"
                )
            ],
        )

        result = manager.execute_plan(plan, WorldState())

        self.assertEqual(tool.spec.reversibility, Reversibility.COMPENSATABLE)
        self.assertEqual(result.steps[0].status.value, "blocked")

    def test_secret_token_not_in_audit(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("o/r", 1, title="Issue", body="Body")
        registry = ToolRegistry()
        registry.register(GitHubReadIssueTool(client))
        audit = AuditLog()
        manager = TransactionManager(
            registry,
            PolicyEngine(granted_permissions={Permission.READ_FILES}),
            CausalGraph(),
            audit,
            ApprovalGate(lambda step: True),
            allow_network_tools=True,
        )
        plan = TransactionPlan(
            Goal("read", ["read"], stop_conditions=["done"]),
            [
                ActionStep(
                    "github_read_issue", {"repo": "o/r", "issue_number": 1, "token": Secret("ghp_secret")}, "read"
                )
            ],
        )

        manager.execute_plan(plan, WorldState())

        self.assertNotIn("ghp_secret", repr(audit.records()))

    def test_plain_string_token_is_rejected(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("o/r", 1, title="Issue", body="Body")
        tool = GitHubReadIssueTool(client)

        result = tool.execute({"repo": "o/r", "issue_number": 1, "token": "ghp_plain"}, WorldState())

        self.assertFalse(result.ok)
        self.assertIn("Secret", type(result.error).__name__)

    def test_secret_token_is_accepted(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("o/r", 1, title="Issue", body="Body")
        tool = GitHubReadIssueTool(client)

        result = tool.execute({"repo": "o/r", "issue_number": 1, "token": Secret("ghp_secret")}, WorldState())

        self.assertTrue(result.ok)
        self.assertEqual(result.observed_state_delta["github_issue"]["title"], "Issue")
        self.assertEqual(client.accepted_token_count, 1)
        self.assertEqual(len(client.accepted_token_fingerprints), 1)
        self.assertNotIn("ghp_secret", repr(client))

    def test_plain_string_token_not_in_audit(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("o/r", 1, title="Issue", body="Body")
        registry = ToolRegistry()
        registry.register(GitHubReadIssueTool(client))
        audit = AuditLog()
        manager = TransactionManager(
            registry,
            PolicyEngine(),
            CausalGraph(),
            audit,
            ApprovalGate(lambda step: True),
            allow_network_tools=True,
        )
        plan = TransactionPlan(
            Goal("read", ["read"], stop_conditions=["done"]),
            [ActionStep("github_read_issue", {"repo": "o/r", "issue_number": 1, "token": "ghp_plain"}, "read")],
        )

        manager.execute_plan(plan, WorldState())

        self.assertNotIn("ghp_plain", repr(audit.records()))

    def test_in_memory_flow_read_branch_update_pr(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("o/r", 1, title="Bug", body="Fix it")
        sha = client.seed_file("o/r", "main", "app.py", "bad")

        issue = GitHubReadIssueTool(client).execute({"repo": "o/r", "issue_number": 1}, WorldState())
        branch = GitHubCreateBranchTool(client).execute(
            {"repo": "o/r", "branch": "agent/fix", "base": "main"}, WorldState()
        )
        file_data = GitHubGetFileTool(client).execute(
            {"repo": "o/r", "path": "app.py", "ref": "agent/fix"}, WorldState()
        )
        updated = GitHubUpdateFileTool(client).execute(
            {
                "repo": "o/r",
                "path": "app.py",
                "branch": "agent/fix",
                "content": "good",
                "message": "fix",
                "expected_sha": sha,
            },
            WorldState(),
        )
        pr = GitHubOpenPRTool(client).execute(
            {
                "repo": "o/r",
                "title": "Fix",
                "body": "Fixes #1",
                "head": "agent/fix",
                "base": "main",
                "idempotency_key": "fix-1",
            },
            WorldState(),
        )

        self.assertTrue(issue.ok)
        self.assertTrue(branch.ok)
        self.assertEqual(file_data.observed_state_delta["github_file"]["content"], "bad")
        self.assertTrue(updated.ok)
        self.assertEqual(pr.observed_state_delta["github_pr"]["number"], 1)

    def test_idempotency_key_prevents_duplicate_pr(self) -> None:
        client = InMemoryGitHubClient()
        tool = GitHubOpenPRTool(client)
        args = {"repo": "o/r", "title": "t", "body": "b", "head": "h", "base": "main", "idempotency_key": "same"}

        first = tool.execute(args, WorldState())
        second = tool.execute(args, WorldState())

        self.assertEqual(
            first.observed_state_delta["github_pr"]["number"], second.observed_state_delta["github_pr"]["number"]
        )
        self.assertEqual(len(client.prs), 1)

    def test_in_memory_client_does_not_store_raw_token(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("o/r", 1, title="Issue", body="Body")

        result = GitHubReadIssueTool(client).execute(
            {"repo": "o/r", "issue_number": 1, "token": Secret("ghp_secret")}, WorldState()
        )

        self.assertTrue(result.ok)
        self.assertEqual(client.accepted_token_count, 1)
        self.assertNotIn("ghp_secret", repr(client))
        self.assertNotIn("ghp_secret", repr(client.accepted_token_fingerprints))

    def test_read_issue_tool_uses_rest_client(self) -> None:
        transport = FakeTransport(
            [_response(200, {"number": 1, "title": "Bug", "body": "Details", "state": "open", "html_url": "url"})]
        )
        tool = GitHubReadIssueTool(GitHubRESTClient(transport=transport))

        result = tool.execute({"repo": "o/r", "issue_number": 1, "token": Secret("ghp_secret")}, WorldState())

        self.assertTrue(result.ok)
        self.assertEqual(result.observed_state_delta["github_issue"]["title"], "Bug")
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer ghp_secret")

    def test_update_file_tool_uses_rest_client_expected_guard(self) -> None:
        transport = FakeTransport([_response(200, {"content": {"sha": "new"}, "commit": {"sha": "commit"}})])
        tool = GitHubUpdateFileTool(GitHubRESTClient(transport=transport))

        result = tool.execute(
            {
                "repo": "o/r",
                "path": "app.py",
                "branch": "agent/fix",
                "content": "new",
                "message": "fix",
                "expected_sha": "old",
                "token": Secret("ghp_secret"),
            },
            WorldState(),
        )

        self.assertTrue(result.ok)
        body = json.loads(transport.calls[0]["body"].decode("utf-8"))
        self.assertEqual(body["sha"], "old")

    def test_open_pr_tool_uses_rest_client_idempotency_key(self) -> None:
        marker = "<!-- leos-idempotency-key: same -->"
        transport = FakeTransport(
            [
                _response(
                    200,
                    [{"number": 3, "title": "Fix", "body": marker, "state": "open", "html_url": "url"}],
                )
            ]
        )
        tool = GitHubOpenPRTool(GitHubRESTClient(transport=transport))

        result = tool.execute(
            {
                "repo": "o/r",
                "title": "Fix",
                "body": "body",
                "head": "agent/fix",
                "base": "main",
                "idempotency_key": "same",
                "token": Secret("ghp_secret"),
            },
            WorldState(),
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.observed_state_delta["github_pr"]["already_exists"])
        self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_plain_token_rejected_before_rest_transport(self) -> None:
        transport = FakeTransport([])
        tool = GitHubReadIssueTool(GitHubRESTClient(transport=transport))

        result = tool.execute({"repo": "o/r", "issue_number": 1, "token": "ghp_plain"}, WorldState())

        self.assertFalse(result.ok)
        self.assertEqual(transport.calls, [])

    def test_secret_token_not_in_audit_with_rest_client(self) -> None:
        transport = FakeTransport(
            [_response(200, {"number": 1, "title": "Bug", "body": "Details", "state": "open", "html_url": "url"})]
        )
        registry = ToolRegistry()
        registry.register(GitHubReadIssueTool(GitHubRESTClient(transport=transport)))
        audit = AuditLog()
        manager = TransactionManager(
            registry,
            PolicyEngine(),
            CausalGraph(),
            audit,
            ApprovalGate(lambda step: True),
            allow_network_tools=True,
        )
        plan = TransactionPlan(
            Goal("read", ["read"], stop_conditions=["done"]),
            [
                ActionStep(
                    "github_read_issue",
                    {"repo": "o/r", "issue_number": 1, "token": Secret("ghp_secret")},
                    "read",
                )
            ],
        )

        manager.execute_plan(plan, WorldState())

        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer ghp_secret")
        self.assertNotIn("ghp_secret", repr(audit.records()))

    def test_rest_create_branch_rollback_uses_secret_without_audit_token(self) -> None:
        transport = FakeTransport(
            [
                _response(200, {"object": {"sha": "base"}}),
                _response(201, {"object": {"sha": "base"}}),
                GitHubHTTPResponse(204, b"", {}),
            ]
        )
        tool = GitHubCreateBranchTool(GitHubRESTClient(transport=transport))

        result = tool.execute(
            {"repo": "o/r", "branch": "agent/fix", "base": "main", "token": Secret("ghp_secret")},
            WorldState(),
        )
        rollback = tool.rollback(result.rollback_token or {}, WorldState())

        self.assertTrue(result.ok)
        self.assertTrue(rollback.ok)
        self.assertEqual(transport.calls[-1]["headers"]["Authorization"], "Bearer ghp_secret")
        self.assertNotIn("ghp_secret", repr(result.rollback_token))

    def test_rest_update_file_rollback_restores_previous_with_secret(self) -> None:
        old_content = "old"
        transport = FakeTransport(
            [
                _response(200, {"content": {"sha": "new"}, "commit": {"sha": "commit"}}),
                _response(200, {"content": "bmV3", "encoding": "base64", "sha": "new"}),
                _response(200, {"content": {"sha": "old"}, "commit": {"sha": "rollback"}}),
            ]
        )
        tool = GitHubUpdateFileTool(GitHubRESTClient(transport=transport))

        result = tool.execute(
            {
                "repo": "o/r",
                "path": "app.py",
                "branch": "agent/fix",
                "content": "new",
                "message": "fix",
                "expected_sha": "old",
                "token": Secret("ghp_secret"),
            },
            WorldState(),
        )
        token = result.rollback_token or {}
        token["previous"] = {"content": old_content}
        rollback = tool.rollback(token, WorldState())

        self.assertTrue(result.ok)
        self.assertTrue(rollback.ok)
        self.assertEqual(transport.calls[-1]["headers"]["Authorization"], "Bearer ghp_secret")

    def test_rest_open_pr_and_comment_rollback_use_secret(self) -> None:
        transport = FakeTransport(
            [
                _response(201, {"number": 5, "title": "Fix", "state": "open", "html_url": "pr"}),
                _response(200, {"state": "closed"}),
                _response(201, {"id": 6, "html_url": "comment"}),
                GitHubHTTPResponse(204, b"", {}),
            ]
        )
        client = GitHubRESTClient(transport=transport)
        pr_tool = GitHubOpenPRTool(client)
        comment_tool = GitHubCommentTool(client)

        pr = pr_tool.execute(
            {
                "repo": "o/r",
                "title": "Fix",
                "body": "body",
                "head": "agent/fix",
                "base": "main",
                "token": Secret("ghp_secret"),
            },
            WorldState(),
        )
        pr_rollback = pr_tool.rollback(pr.rollback_token or {}, WorldState())
        comment = comment_tool.execute(
            {"repo": "o/r", "issue_number": 1, "body": "hello", "token": Secret("ghp_secret")},
            WorldState(),
        )
        comment_rollback = comment_tool.rollback(comment.rollback_token or {}, WorldState())

        self.assertTrue(pr_rollback.ok)
        self.assertTrue(comment_rollback.ok)
        self.assertEqual(transport.calls[1]["headers"]["Authorization"], "Bearer ghp_secret")
        self.assertEqual(transport.calls[3]["headers"]["Authorization"], "Bearer ghp_secret")

    def test_rest_client_errors_return_tool_results(self) -> None:
        transport = FakeTransport([_response(500, {"message": "server error"})])
        tool = GitHubGetFileTool(GitHubRESTClient(transport=transport))

        result = tool.execute({"repo": "o/r", "path": "app.py", "ref": "main"}, WorldState())

        self.assertFalse(result.ok)

    def test_check_ci_status_dry_run_ok(self) -> None:
        tool = GitHubCheckCIStatusTool(InMemoryGitHubClient())
        result = tool.dry_run({"repo": "o/r", "ref": "main"}, WorldState())
        self.assertTrue(result.ok)

    def test_check_ci_status_dry_run_missing_args(self) -> None:
        tool = GitHubCheckCIStatusTool(InMemoryGitHubClient())
        result = tool.dry_run({"repo": "o/r"}, WorldState())
        self.assertFalse(result.ok)

    def test_comment_dry_run_missing_args(self) -> None:
        tool = GitHubCommentTool(InMemoryGitHubClient())
        result = tool.dry_run({"repo": "o/r"}, WorldState())
        self.assertFalse(result.ok)

    def test_update_file_rollback_no_previous_content(self) -> None:
        tool = GitHubUpdateFileTool(InMemoryGitHubClient())
        result = tool.rollback(
            {"repo": "o/r", "path": "new.py", "branch": "b"},
            WorldState(),
        )
        self.assertTrue(result.ok)
        self.assertIn("No previous", result.message)

    def test_repository_branch_and_pr_observation_tools(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("o/r", "main", "README.md", "content")
        client.create_branch("o/r", "leos/smoke", "main")
        pull_request = client.open_pr("o/r", "Smoke", "body", "leos/smoke", "main")

        repository = GitHubGetRepositoryTool(client).execute(
            {"repo": "o/r", "token": Secret("token-value")},
            WorldState(),
        )
        branch = GitHubGetBranchTool(client).execute(
            {"repo": "o/r", "branch": "leos/smoke", "token": Secret("token-value")},
            WorldState(),
        )
        pr = GitHubGetPRTool(client).execute(
            {"repo": "o/r", "pr_number": pull_request["number"], "token": Secret("token-value")},
            WorldState(),
        )

        self.assertTrue(repository.observed_state_delta["github_repository"]["private"])
        self.assertTrue(branch.observed_state_delta["github_branch_status"]["exists"])
        self.assertEqual(pr.observed_state_delta["github_pr_status"]["state"], "open")

    def test_close_pr_requires_expected_head_and_base(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("o/r", "main", "README.md", "content")
        client.create_branch("o/r", "leos/smoke", "main")
        pull_request = client.open_pr("o/r", "Smoke", "body", "leos/smoke", "main")
        tool = GitHubClosePRTool(client)

        mismatch = tool.execute(
            {
                "repo": "o/r",
                "pr_number": pull_request["number"],
                "expected_head": "leos/other",
                "expected_base": "main",
                "token": Secret("token-value"),
            },
            WorldState(),
        )
        closed = tool.execute(
            {
                "repo": "o/r",
                "pr_number": pull_request["number"],
                "expected_head": "leos/smoke",
                "expected_base": "main",
                "token": Secret("token-value"),
            },
            WorldState(),
        )

        self.assertFalse(mismatch.ok)
        self.assertTrue(closed.ok)
        self.assertEqual(closed.observed_state_delta["github_pr_closed"]["state"], "closed")

    def test_delete_branch_requires_leos_prefix_and_expected_sha(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("o/r", "main", "README.md", "content")
        created = client.create_branch("o/r", "leos/smoke", "main")
        tool = GitHubDeleteBranchTool(client)

        unsafe = tool.dry_run(
            {"repo": "o/r", "branch": "feature", "expected_sha": "sha"},
            WorldState(),
        )
        mismatch = tool.execute(
            {
                "repo": "o/r",
                "branch": "leos/smoke",
                "expected_sha": "wrong",
                "token": Secret("token-value"),
            },
            WorldState(),
        )
        deleted = tool.execute(
            {
                "repo": "o/r",
                "branch": "leos/smoke",
                "expected_sha": created["sha"],
                "token": Secret("token-value"),
            },
            WorldState(),
        )

        self.assertFalse(unsafe.ok)
        self.assertFalse(mismatch.ok)
        self.assertTrue(deleted.ok)
        self.assertFalse(client.get_branch("o/r", "leos/smoke")["exists"])

    def test_protected_branch_delete_blocked(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("o/r", "main", "app.py", "content")
        tool = GitHubCreateBranchTool(client)
        tool.execute(
            {"repo": "o/r", "branch": "main", "base": "main", "token": "t"},
            WorldState(),
        )
        with self.assertRaises(LeosError):
            client.delete_branch("o/r", "main")

    def test_all_tools_dry_run_reject_empty_args(self) -> None:
        client = InMemoryGitHubClient()
        tools = [
            GitHubReadIssueTool(client),
            GitHubGetRepositoryTool(client),
            GitHubGetBranchTool(client),
            GitHubGetPRTool(client),
            GitHubCreateBranchTool(client),
            GitHubGetFileTool(client),
            GitHubUpdateFileTool(client),
            GitHubOpenPRTool(client),
            GitHubClosePRTool(client),
            GitHubDeleteBranchTool(client),
            GitHubCommentTool(client),
            GitHubCheckCIStatusTool(client),
        ]
        for tool in tools:
            with self.subTest(tool=tool.spec.name):
                result = tool.dry_run({}, WorldState())
                self.assertFalse(result.ok, f"{tool.spec.name} dry_run with empty args should fail")


if __name__ == "__main__":
    unittest.main()
