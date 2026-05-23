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
    Reversibility,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from leos_agent.enums import GoalStatus, StepStatus
from leos_agent.tools import default_registry


class _RollbackNetworkTool:
    spec = ToolSpec(
        "rollback_network",
        "network rollback test",
        (),
        network_access=True,
        egress_host="api.github.com",
        egress_methods=("POST",),
        rollback_egress_methods=("DELETE",),
        reversibility=Reversibility.REVERSIBLE,
    )

    def __init__(self) -> None:
        self.rollback_called = False

    def dry_run(self, arguments, state):
        return ToolResult(True, "dry")

    def execute(self, arguments, state):
        return ToolResult(True, "exec", observed_state_delta={"ok": True}, rollback_token={"token": "must-not-leak"})

    def rollback(self, token, state):
        self.rollback_called = True
        return ToolResult(True, "rollback")


class _FailingSecondTool:
    spec = ToolSpec("failing_second", "fails", (), output_schema={"type": "object", "required": ["missing"]})

    def dry_run(self, arguments, state):
        return ToolResult(True, "dry")

    def execute(self, arguments, state):
        return ToolResult(True, "bad", observed_state_delta={})

    def rollback(self, token, state):
        return ToolResult(True, "rollback")


class _EgressPolicyBlocksRollbackAfterPlanning(EgressPolicy):
    def __init__(self) -> None:
        super().__init__(allowed_hosts=("api.github.com",), allowed_methods=("POST", "DELETE"))
        object.__setattr__(self, "calls", 0)

    def allows(self, host: str, method: str = "GET") -> bool:
        object.__setattr__(self, "calls", self.calls + 1)
        if self.calls <= 4:
            return super().allows(host, method)
        return False


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

    def test_network_rollback_emits_egress_allowed_and_calls_rollback(self) -> None:
        tool = _RollbackNetworkTool()
        registry = ToolRegistry()
        registry.register(tool)
        registry.register(_FailingSecondTool())
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("POST", "DELETE"))
        agent = AgentKernel(registry=registry, policy=policy)
        goal = Goal(
            "rollback",
            ["blocked"],
            criteria=({"key": "ok", "op": "exists"},),
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(
            goal,
            [ActionStep("rollback_network", {}, "net"), ActionStep("failing_second", {}, "fail")],
        )

        agent.run(plan)

        self.assertTrue(tool.rollback_called)
        self.assertTrue(any(event.event_type == "rollback.egress_allowed" for event in agent.audit_log.events))

    def test_network_rollback_blocked_creates_recovery_packet_without_token(self) -> None:
        tool = _RollbackNetworkTool()
        registry = ToolRegistry()
        registry.register(tool)
        registry.register(_FailingSecondTool())
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = _EgressPolicyBlocksRollbackAfterPlanning()
        agent = AgentKernel(registry=registry, policy=policy)
        goal = Goal(
            "rollback",
            ["blocked"],
            criteria=({"key": "ok", "op": "exists"},),
            stop_conditions=["blocked"],
        )
        plan = agent.build_plan(
            goal,
            [ActionStep("rollback_network", {}, "net"), ActionStep("failing_second", {}, "fail")],
        )

        agent.run(plan)

        self.assertFalse(tool.rollback_called)
        event_types = [event.event_type for event in agent.audit_log.events]
        self.assertIn("rollback.egress_blocked", event_types)
        self.assertIn("recovery.packet_created", event_types)
        self.assertIn("recovery.manual_action_required", event_types)
        self.assertNotIn("must-not-leak", repr(agent.audit_log.records()))


if __name__ == "__main__":
    unittest.main()
