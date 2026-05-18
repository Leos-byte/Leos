from __future__ import annotations

import unittest

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
from leos_agent.github_tools import (
    GitHubCreateBranchTool,
    GitHubGetFileTool,
    GitHubOpenPRTool,
    GitHubReadIssueTool,
    GitHubUpdateFileTool,
    InMemoryGitHubClient,
)
from leos_agent.state import WorldState
from leos_agent.tools import Secret, ToolRegistry


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


if __name__ == "__main__":
    unittest.main()
