"""Transaction manager goal-status semantics."""

from __future__ import annotations

import unittest

from leos_agent import (
    ActionStep,
    AgentKernel,
    EgressPolicy,
    GitHubGetFileTool,
    GitHubUpdateFileTool,
    Goal,
    InMemoryGitHubClient,
    PolicyEngine,
    ResourceBudget,
    ToolRegistry,
)
from leos_agent.enums import GoalStatus, StepStatus
from leos_agent.tools import default_registry


class TransactionGoalStatusTests(unittest.TestCase):
    def test_verified_steps_do_not_directly_mark_goal_succeeded(self) -> None:
        agent = AgentKernel(registry=default_registry(), policy=PolicyEngine())
        goal = Goal("Echo", ["last echo observed"])
        plan = agent.build_plan(goal, [ActionStep("echo", {"message": "hi"}, "echo")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
        self.assertEqual(result.goal.status, GoalStatus.PARTIALLY_DONE)

    def test_network_access_tool_counts_against_network_budget_without_network_permission(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("o/r", "main", "README.md", "content")
        registry = ToolRegistry()
        registry.register(GitHubGetFileTool(client))
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            "Budget network access",
            ["file read"],
            criteria=({"key": "github_file", "op": "exists"},),
            stop_conditions=["blocked"],
            budget=ResourceBudget(max_network_requests=0),
        )
        plan = agent.build_plan(
            goal,
            [ActionStep("github_get_file", {"repo": "o/r", "path": "README.md", "ref": "main"}, "read")],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        budget_events = [event for event in agent.audit_log.events if event.event_type == "budget.exceeded"]
        self.assertEqual(budget_events[0].payload["limit"], "max_network_requests")

    def test_github_write_counts_against_network_budget_without_network_permission(self) -> None:
        client = InMemoryGitHubClient()
        old_sha = client.seed_file("o/r", "feature", "README.md", "old")
        registry = ToolRegistry()
        registry.register(GitHubUpdateFileTool(client))
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            "Budget github write",
            ["file updated"],
            criteria=({"key": "github_file_updated", "op": "exists"},),
            stop_conditions=["blocked"],
            budget=ResourceBudget(max_network_requests=0),
        )
        plan = agent.build_plan(
            goal,
            [
                ActionStep(
                    "github_update_file",
                    {
                        "repo": "o/r",
                        "path": "README.md",
                        "branch": "feature",
                        "content": "new",
                        "message": "update",
                        "expected_sha": old_sha,
                    },
                    "update",
                )
            ],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        budget_events = [event for event in agent.audit_log.events if event.event_type == "budget.exceeded"]
        self.assertEqual(budget_events[0].payload["limit"], "max_network_requests")

    def test_production_records_egress_allowed_audit_event(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("o/r", "main", "README.md", "content")
        registry = ToolRegistry()
        registry.register(GitHubGetFileTool(client))
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET",))
        agent = AgentKernel(registry=registry, policy=policy)
        goal = Goal(
            "Allowed egress",
            ["file read"],
            criteria=({"key": "github_file", "op": "exists"},),
            stop_conditions=["done"],
        )
        plan = agent.build_plan(
            goal,
            [ActionStep("github_get_file", {"repo": "o/r", "path": "README.md", "ref": "main"}, "read")],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
        event = next(event for event in agent.audit_log.events if event.event_type == "egress.allowed")
        self.assertEqual(event.payload["host"], "api.github.com")
        self.assertEqual(event.payload["forward_methods"], ["GET"])

    def test_production_records_egress_blocked_audit_event(self) -> None:
        client = InMemoryGitHubClient()
        registry = ToolRegistry()
        registry.register(GitHubGetFileTool(client))
        agent = AgentKernel(registry=registry, policy=PolicyEngine.from_profile("production_locked_down"))
        goal = Goal(
            "Blocked egress",
            ["file read"],
            criteria=({"key": "github_file", "op": "exists"},),
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(
            goal,
            [ActionStep("github_get_file", {"repo": "o/r", "path": "README.md", "ref": "main"}, "read")],
        )

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
        event = next(event for event in agent.audit_log.events if event.event_type == "egress.blocked")
        self.assertIn("without an explicit egress policy", event.payload["reason"])
        self.assertNotIn("token", repr(event.payload))


if __name__ == "__main__":
    unittest.main()
