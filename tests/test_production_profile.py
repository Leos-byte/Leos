from __future__ import annotations

import json
import unittest

from leos_agent import (
    ActionStep,
    AgentKernel,
    ApprovalDecision,
    ApprovalDecisionValue,
    ApprovalGate,
    CausalContract,
    EgressPolicy,
    GitHubCheckCIStatusTool,
    GitHubCommentTool,
    GitHubCreateBranchTool,
    GitHubGetFileTool,
    GitHubHTTPResponse,
    GitHubOpenPRTool,
    GitHubReadIssueTool,
    GitHubRESTClient,
    GitHubUpdateFileTool,
    Goal,
    InMemoryGitHubClient,
    Permission,
    PolicyConfigurationError,
    PolicyDenied,
    PolicyEngine,
    PolicyRule,
    Reversibility,
    RiskLevel,
    SandboxPolicy,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


class _Tool:
    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec
        self.executed = False

    def dry_run(self, arguments, state):
        return ToolResult(True, "dry")

    def execute(self, arguments, state):
        self.executed = True
        return ToolResult(True, "exec", observed_state_delta={"ok": True})

    def rollback(self, token, state):
        return ToolResult(True, "rollback")

    def runtime_attestations(self):
        return {
            "runtime_egress_enforced": True,
            "runtime_egress_policy_configured": True,
            "runtime_egress_mode": "in_memory",
            "runtime_egress_host": self.spec.egress_host or "api.github.com",
        }


class _FakeGitHubTransport:
    def __init__(self, responses: list[GitHubHTTPResponse] | None = None) -> None:
        self.responses = responses or []
        self.requests = []

    def request(self, method, url, *, headers, body, timeout_seconds):
        self.requests.append((method, url, body, timeout_seconds))
        if not self.responses:
            raise AssertionError("unexpected GitHub transport request")
        return self.responses.pop(0)


def _github_response(payload, status: int = 200) -> GitHubHTTPResponse:
    return GitHubHTTPResponse(status, json.dumps(payload).encode("utf-8"), {})


class _SignedApprovalGate(ApprovalGate):
    signed_approval_enforced = True

    def request_packet(self, packet, step):
        del step
        return ApprovalDecision(packet.approval_id, packet.step_hash, ApprovalDecisionValue.APPROVE)


def _contract() -> CausalContract:
    return CausalContract("tool", sets=("ok",), required_observations=("ok",))


def _run_tool(
    tool,
    *,
    profile: str = "production_locked_down",
    approve: bool = True,
    policy: PolicyEngine | None = None,
    arguments: dict | None = None,
):
    registry = ToolRegistry()
    registry.register(tool)
    kernel = AgentKernel(
        registry,
        policy or PolicyEngine.from_profile(profile),
        approval_gate=ApprovalGate(lambda step: approve),
    )
    goal = Goal(
        "g",
        ["ok"],
        criteria=({"key": "ok", "op": "equals", "value": True},),
        stop_conditions=["done"],
    )
    plan = kernel.build_plan(goal, [ActionStep(tool.spec.name, arguments or {}, "run")])
    return kernel, kernel.run(plan)


class ProductionProfileTests(unittest.TestCase):
    def test_workspace_subprocess_runner_blocked(self) -> None:
        tool = _Tool(
            ToolSpec(
                "exec_tool",
                "execute",
                (Permission.EXECUTE_CODE,),
                default_risk=RiskLevel.MEDIUM,
                sandbox_policy=SandboxPolicy.WORKSPACE,
                filesystem_scope="workspace",
                output_schema={"type": "object"},
                causal_contract=_contract(),
            )
        )
        kernel, plan = _run_tool(tool)
        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)
        self.assertTrue(
            any("workspace subprocess" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events)
        )

    def test_network_tool_blocked_without_egress_policy(self) -> None:
        tool = _Tool(
            ToolSpec(
                "net_tool",
                "network",
                (Permission.NETWORK,),
                default_risk=RiskLevel.LOW,
                network_access=True,
                egress_methods=("GET",),
            )
        )
        kernel, plan = _run_tool(tool)
        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)
        self.assertTrue(any("network" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events))

    def test_network_tool_blocked_when_egress_host_not_allowed(self) -> None:
        tool = _Tool(
            ToolSpec(
                "net_tool",
                "network",
                (Permission.NETWORK,),
                default_risk=RiskLevel.LOW,
                network_access=True,
                egress_methods=("GET",),
            )
        )
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",))

        kernel, plan = _run_tool(tool, policy=policy, arguments={"host": "example.com"})

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)
        self.assertTrue(
            any("egress policy" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events)
        )

    def test_network_tool_with_allowed_egress_reaches_human_gate(self) -> None:
        tool = _Tool(
            ToolSpec(
                "net_tool",
                "network",
                (Permission.NETWORK,),
                default_risk=RiskLevel.LOW,
                network_access=True,
                egress_methods=("GET",),
            )
        )
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",))

        kernel, plan = _run_tool(tool, policy=policy, approve=False, arguments={"host": "api.github.com"})

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)
        self.assertFalse(
            any("egress policy" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events)
        )

    def test_egress_policy_rejects_local_private_and_wildcard_hosts(self) -> None:
        policy = EgressPolicy(allowed_hosts=("localhost", "127.0.0.1", "10.0.0.1", "192.168.1.10", "*"))

        self.assertFalse(policy.allows("localhost"))
        self.assertFalse(policy.allows("127.0.0.1"))
        self.assertFalse(policy.allows("10.0.0.1"))
        self.assertFalse(policy.allows("172.16.0.1"))
        self.assertFalse(policy.allows("192.168.1.10"))
        self.assertFalse(policy.allows("*"))

    def test_github_tools_declare_network_egress_metadata(self) -> None:
        client = InMemoryGitHubClient()
        tools = (
            GitHubReadIssueTool(client),
            GitHubCreateBranchTool(client),
            GitHubGetFileTool(client),
            GitHubUpdateFileTool(client),
            GitHubOpenPRTool(client),
            GitHubCommentTool(client),
            GitHubCheckCIStatusTool(client),
        )

        for tool in tools:
            self.assertTrue(tool.spec.network_access, tool.spec.name)
            self.assertEqual(tool.spec.egress_host, "api.github.com")
            self.assertTrue(tool.spec.egress_methods, tool.spec.name)

    def test_production_blocks_github_rest_client_without_runtime_egress(self) -> None:
        client = GitHubRESTClient(enforce_egress=False)
        tool = GitHubCreateBranchTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(
            allowed_hosts=("api.github.com",), allowed_methods=("GET", "POST", "DELETE")
        )

        kernel, plan = _run_tool(
            tool,
            approve=False,
            policy=policy,
            arguments={"repo": "o/r", "branch": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any(event.event_type == "runtime.attestation_failed" for event in kernel.audit_log.events))
        self.assertTrue(
            any(
                "runtime egress enforcement" in str(event.payload.get("reason", ""))
                for event in kernel.audit_log.events
            )
        )

    def test_production_allows_github_rest_client_with_runtime_egress(self) -> None:
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(
            allowed_hosts=("api.github.com",), allowed_methods=("GET", "POST", "DELETE")
        )
        client = GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)
        tool = GitHubCreateBranchTool(client)

        kernel, plan = _run_tool(
            tool,
            approve=False,
            policy=policy,
            arguments={"repo": "o/r", "branch": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any(event.event_type == "runtime.attestation_checked" for event in kernel.audit_log.events))
        self.assertFalse(any(event.event_type == "runtime.attestation_failed" for event in kernel.audit_log.events))
        self.assertTrue(any(event.event_type == "approval.rejected" for event in kernel.audit_log.events))

    def test_production_blocks_network_tool_without_runtime_attestation_method(self) -> None:
        class UnattestedTool:
            spec = ToolSpec(
                "unattested_net",
                "network",
                (),
                network_access=True,
                egress_host="api.github.com",
                egress_methods=("GET",),
            )

            def dry_run(self, arguments, state):
                return ToolResult(True, "dry")

            def execute(self, arguments, state):
                return ToolResult(True, "exec", observed_state_delta={"ok": True})

            def rollback(self, token, state):
                return ToolResult(True, "rollback")

        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET",))

        kernel, plan = _run_tool(UnattestedTool(), policy=policy)

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any(event.event_type == "runtime.attestation_failed" for event in kernel.audit_log.events))

    def test_developer_local_does_not_require_runtime_attestation(self) -> None:
        class UnattestedTool:
            spec = ToolSpec("unattested_net", "network", (), network_access=True, egress_methods=("GET",))

            def __init__(self) -> None:
                self.executed = False

            def dry_run(self, arguments, state):
                return ToolResult(True, "dry")

            def execute(self, arguments, state):
                self.executed = True
                return ToolResult(True, "exec", observed_state_delta={"ok": True})

            def rollback(self, token, state):
                return ToolResult(True, "rollback")

        tool = UnattestedTool()
        registry = ToolRegistry()
        registry.register(tool)
        kernel = AgentKernel(registry, PolicyEngine.from_profile("developer_local"), allow_network_tools=True)
        goal = Goal(
            "g",
            ["ok"],
            criteria=({"key": "ok", "op": "equals", "value": True},),
            stop_conditions=["done"],
        )
        plan = kernel.build_plan(goal, [ActionStep(tool.spec.name, {}, "run")])
        result = kernel.run(plan)

        self.assertEqual(result.steps[0].status.value, "verified")
        self.assertTrue(tool.executed)
        self.assertFalse(any(event.event_type == "runtime.attestation_failed" for event in kernel.audit_log.events))

    def test_github_update_requires_all_declared_forward_methods(self) -> None:
        client = InMemoryGitHubClient()
        old_sha = client.seed_file("o/r", "feature", "README.md", "old")
        tool = GitHubUpdateFileTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET",))

        _, plan = _run_tool(
            tool,
            approve=True,
            policy=policy,
            arguments={
                "repo": "o/r",
                "path": "README.md",
                "branch": "feature",
                "content": "new",
                "message": "update",
                "expected_sha": old_sha,
                "method": "GET",
            },
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any("forward PUT" in str(event.payload.get("reason", "")) for event in _.audit_log.events))

    def test_github_update_passes_egress_layer_with_get_and_put(self) -> None:
        client = InMemoryGitHubClient()
        old_sha = client.seed_file("o/r", "feature", "README.md", "old")
        tool = GitHubUpdateFileTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET", "PUT"))

        kernel, plan = _run_tool(
            tool,
            approve=False,
            policy=policy,
            arguments={
                "repo": "o/r",
                "path": "README.md",
                "branch": "feature",
                "content": "new",
                "message": "update",
                "expected_sha": old_sha,
            },
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any(event.event_type == "egress.allowed" for event in kernel.audit_log.events))
        self.assertFalse(any(event.event_type == "egress.blocked" for event in kernel.audit_log.events))

    def test_github_create_branch_requires_rollback_delete_method(self) -> None:
        client = InMemoryGitHubClient()
        tool = GitHubCreateBranchTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET", "POST"))

        _, plan = _run_tool(
            tool,
            approve=True,
            policy=policy,
            arguments={"repo": "o/r", "branch": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any("rollback DELETE" in str(event.payload.get("reason", "")) for event in _.audit_log.events))

    def test_github_create_branch_passes_egress_layer_with_delete_rollback(self) -> None:
        client = InMemoryGitHubClient()
        tool = GitHubCreateBranchTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(
            allowed_hosts=("api.github.com",), allowed_methods=("GET", "POST", "DELETE")
        )

        kernel, plan = _run_tool(
            tool,
            approve=False,
            policy=policy,
            arguments={"repo": "o/r", "branch": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any(event.event_type == "egress.allowed" for event in kernel.audit_log.events))
        self.assertTrue(any(event.event_type == "approval.rejected" for event in kernel.audit_log.events))

    def test_github_open_pr_requires_patch_rollback_method(self) -> None:
        client = InMemoryGitHubClient()
        tool = GitHubOpenPRTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET", "POST"))

        _, plan = _run_tool(
            tool,
            approve=True,
            policy=policy,
            arguments={"repo": "o/r", "title": "t", "body": "b", "head": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any("rollback PATCH" in str(event.payload.get("reason", "")) for event in _.audit_log.events))

    def test_read_only_github_get_file_passes_with_get_only(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("o/r", "main", "README.md", "content")
        tool = GitHubGetFileTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET",))

        kernel, plan = _run_tool(
            tool,
            policy=policy,
            arguments={"repo": "o/r", "path": "README.md", "ref": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "verified")
        self.assertTrue(any(event.event_type == "egress.allowed" for event in kernel.audit_log.events))

    def test_network_access_with_empty_egress_methods_blocks_in_production(self) -> None:
        tool = _Tool(
            ToolSpec(
                "empty_methods",
                "network",
                (),
                network_access=True,
                egress_host="api.github.com",
            )
        )
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",))

        _, plan = _run_tool(tool, policy=policy)

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(
            any(
                "declared forward egress methods" in str(event.payload.get("reason", ""))
                for event in _.audit_log.events
            )
        )

    def test_reversible_network_tool_requires_declared_rollback_methods(self) -> None:
        tool = _Tool(
            ToolSpec(
                "reversible_net",
                "network",
                (),
                network_access=True,
                egress_host="api.github.com",
                egress_methods=("POST",),
                reversibility=Reversibility.REVERSIBLE,
                output_schema={"type": "object"},
                causal_contract=_contract(),
            )
        )
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("POST",))

        _, plan = _run_tool(tool, policy=policy)

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(
            any(
                "requires rollback egress methods" in str(event.payload.get("reason", ""))
                for event in _.audit_log.events
            )
        )

    def test_irreversible_network_tool_does_not_require_rollback_methods(self) -> None:
        tool = _Tool(
            ToolSpec(
                "irreversible_net",
                "network",
                (),
                network_access=True,
                egress_host="api.github.com",
                egress_methods=("POST",),
            )
        )
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("POST",))

        kernel, plan = _run_tool(tool, policy=policy)

        self.assertEqual(plan.steps[0].status.value, "verified")
        self.assertTrue(tool.executed)
        self.assertTrue(any(event.event_type == "egress.allowed" for event in kernel.audit_log.events))

    def test_rollback_egress_methods_included_in_manifest(self) -> None:
        manifest = GitHubCreateBranchTool(InMemoryGitHubClient()).spec.manifest()

        self.assertEqual(tuple(manifest.rollback_egress_methods), ("DELETE",))

    def test_production_blocks_github_tool_without_egress_policy(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("o/r", 1, title="t", body="b")
        tool = GitHubReadIssueTool(client)

        kernel, plan = _run_tool(tool, arguments={"repo": "o/r", "issue_number": 1})

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(
            any("egress policy" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events)
        )

    def test_production_blocks_github_tool_with_wrong_egress_host(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_issue("o/r", 1, title="t", body="b")
        tool = GitHubReadIssueTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("example.com",))

        kernel, plan = _run_tool(tool, policy=policy, arguments={"repo": "o/r", "issue_number": 1})

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(
            any("api.github.com" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events)
        )

    def test_medium_tool_without_causal_contract_blocked_in_production(self) -> None:
        tool = _Tool(ToolSpec("medium", "m", (), default_risk=RiskLevel.MEDIUM, output_schema={"type": "object"}))
        kernel, plan = _run_tool(tool)
        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)
        self.assertTrue(any(event.event_type == "causal_contract.missing" for event in kernel.audit_log.events))

    def test_medium_tool_without_timeout_blocked(self) -> None:
        tool = _Tool(
            ToolSpec(
                "no_timeout",
                "m",
                (),
                default_risk=RiskLevel.MEDIUM,
                timeout_ms=0,
                output_schema={"type": "object"},
                causal_contract=_contract(),
            )
        )
        _, plan = _run_tool(tool)
        self.assertEqual(plan.steps[0].status.value, "blocked")

    def test_unknown_tool_blocked(self) -> None:
        registry = ToolRegistry()
        kernel = AgentKernel(registry, PolicyEngine.from_profile("production_locked_down"))
        goal = Goal(
            "g",
            ["ok"],
            criteria=({"key": "ok", "op": "exists"},),
            stop_conditions=["done"],
        )
        plan = kernel.build_plan(goal, [ActionStep("missing", {}, "run")])
        result = kernel.run(plan)
        self.assertEqual(result.steps[0].status.value, "blocked")
        self.assertTrue(any("unknown tool" in event.message.lower() for event in kernel.audit_log.events))

    def test_policy_as_code_auto_approval_rejected(self) -> None:
        with self.assertRaises(PolicyConfigurationError):
            PolicyRule.from_mapping({"name": "bad", "when": {"tool": "x"}, "decision": "approved"})

    def test_high_risk_action_cannot_auto_run(self) -> None:
        tool = _Tool(
            ToolSpec(
                "high",
                "h",
                (),
                default_risk=RiskLevel.HIGH,
                output_schema={"type": "object"},
                causal_contract=_contract(),
            )
        )
        _, plan = _run_tool(tool, approve=False)
        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)

    def test_approval_cannot_bypass_missing_causal_contract(self) -> None:
        tool = _Tool(ToolSpec("medium", "m", (), default_risk=RiskLevel.MEDIUM, output_schema={"type": "object"}))
        _, plan = _run_tool(tool, approve=True)
        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)

    def test_github_create_branch_has_production_causal_contract(self) -> None:
        client = InMemoryGitHubClient()
        tool = GitHubCreateBranchTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",))

        kernel, plan = _run_tool(
            tool,
            approve=True,
            policy=policy,
            arguments={"repo": "o/r", "branch": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "verified")
        self.assertIn(("o/r", "feature"), client.branches)
        self.assertFalse(any(event.event_type == "causal_contract.missing" for event in kernel.audit_log.events))

    def test_production_still_requires_approval_for_github_write_after_egress(self) -> None:
        client = InMemoryGitHubClient()
        tool = GitHubCreateBranchTool(client)
        policy = PolicyEngine.from_profile("production_locked_down")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",))

        kernel, plan = _run_tool(
            tool,
            approve=False,
            policy=policy,
            arguments={"repo": "o/r", "branch": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertNotIn(("o/r", "feature"), client.branches)
        self.assertTrue(any(event.event_type == "approval.rejected" for event in kernel.audit_log.events))

    def test_developer_local_warns_for_missing_causal_contract(self) -> None:
        tool = _Tool(ToolSpec("medium", "m", (), default_risk=RiskLevel.MEDIUM))
        kernel, plan = _run_tool(tool, profile="developer_local")
        self.assertEqual(plan.steps[0].status.value, "verified")
        self.assertTrue(tool.executed)
        self.assertTrue(
            any(event.event_type == "step.causal_contract_missing_warning" for event in kernel.audit_log.events)
        )

    def test_production_requires_typed_goal_criteria(self) -> None:
        registry = ToolRegistry()
        kernel = AgentKernel(registry, PolicyEngine.from_profile("production_locked_down"))
        with self.assertRaises(PolicyDenied):
            kernel.build_plan(Goal("g", ["ok"], stop_conditions=["done"]), [])

    def test_production_github_only_profile_exists_with_github_egress(self) -> None:
        policy = PolicyEngine.from_profile("production_github_only")

        self.assertEqual(policy.max_auto_risk, RiskLevel.LOW)
        self.assertTrue(policy.network_default_deny)
        self.assertTrue(policy.require_typed_goal_criteria)
        self.assertTrue(policy.require_signed_approval)
        self.assertIsNotNone(policy.egress_policy)
        self.assertEqual(policy.egress_policy.allowed_hosts, ("api.github.com",))
        self.assertIn("github_update_file", policy.allowed_tools)

    def test_production_github_only_blocks_non_allowlisted_file_tool(self) -> None:
        tool = _Tool(
            ToolSpec(
                "safe_file_write",
                "write",
                (Permission.WRITE_FILES,),
                default_risk=RiskLevel.MEDIUM,
                output_schema={"type": "object"},
                causal_contract=_contract(),
            )
        )
        kernel, plan = _run_tool(tool, profile="production_github_only")

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)
        self.assertTrue(
            any("bounded GitHub tools" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events)
        )

    def test_production_github_only_blocks_generic_network_tool(self) -> None:
        tool = _Tool(
            ToolSpec(
                "fetch_url",
                "network",
                (Permission.NETWORK,),
                network_access=True,
                egress_host="api.github.com",
                egress_methods=("GET",),
            )
        )
        kernel, plan = _run_tool(tool, profile="production_github_only")

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertFalse(tool.executed)

    def test_production_github_only_requires_signed_approval_gate(self) -> None:
        policy = PolicyEngine.from_profile("production_github_only")
        client = GitHubRESTClient(
            transport=_FakeGitHubTransport(),
            egress_policy=policy.egress_policy,
            enforce_egress=True,
        )
        tool = GitHubCreateBranchTool(client)

        kernel, plan = _run_tool(
            tool,
            policy=policy,
            approve=True,
            arguments={"repo": "o/r", "branch": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any(event.event_type == "approval.signature_required" for event in kernel.audit_log.events))

    def test_production_github_only_rejects_in_memory_runtime_attestation_by_default(self) -> None:
        client = InMemoryGitHubClient()
        tool = GitHubGetFileTool(client)
        registry = ToolRegistry()
        registry.register(tool)
        kernel = AgentKernel(
            registry,
            PolicyEngine.from_profile("production_github_only"),
            approval_gate=_SignedApprovalGate(),
        )
        goal = Goal(
            "read file",
            ["file read"],
            criteria=({"key": "github_file", "op": "exists"},),
            stop_conditions=["done"],
        )
        plan = kernel.build_plan(
            goal,
            [ActionStep("github_get_file", {"repo": "o/r", "path": "README.md", "ref": "main"}, "run")],
        )

        result = kernel.run(plan)

        self.assertEqual(result.steps[0].status.value, "blocked")
        self.assertTrue(any("in-memory" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events))

    def test_production_github_only_accepts_signed_approval_gate_for_github_tool(self) -> None:
        policy = PolicyEngine.from_profile("production_github_only")
        transport = _FakeGitHubTransport(
            [
                _github_response({"object": {"sha": "base-sha"}}),
                _github_response({"object": {"sha": "base-sha"}}),
            ]
        )
        client = GitHubRESTClient(
            transport=transport,
            egress_policy=policy.egress_policy,
            enforce_egress=True,
        )
        tool = GitHubCreateBranchTool(client)
        registry = ToolRegistry()
        registry.register(tool)
        kernel = AgentKernel(
            registry,
            policy,
            approval_gate=_SignedApprovalGate(),
        )
        goal = Goal(
            "create branch",
            ["branch created"],
            criteria=({"key": "github_branch", "op": "exists"},),
            stop_conditions=["done"],
        )
        plan = kernel.build_plan(
            goal,
            [ActionStep("github_create_branch", {"repo": "o/r", "branch": "feature", "base": "main"}, "run")],
        )

        result = kernel.run(plan)

        self.assertEqual(result.steps[0].status.value, "verified")
        self.assertEqual([request[0] for request in transport.requests], ["GET", "POST"])

    def test_runtime_attestation_blocks_wrong_base_url_host(self) -> None:
        policy = PolicyEngine.from_profile("production_github_only")
        client = GitHubRESTClient(
            base_url="https://evil.example",
            egress_policy=policy.egress_policy,
            enforce_egress=True,
        )
        tool = GitHubGetFileTool(client)

        kernel, plan = _run_tool(
            tool,
            profile="production_github_only",
            arguments={"repo": "o/r", "path": "README.md", "ref": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any("egress host" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events))

    def test_runtime_attestation_blocks_missing_forward_method(self) -> None:
        policy = PolicyEngine.from_profile("production_github_only")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET",))
        client = GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)
        tool = GitHubUpdateFileTool(client)

        kernel, plan = _run_tool(
            tool,
            policy=policy,
            arguments={
                "repo": "o/r",
                "path": "README.md",
                "branch": "main",
                "content": "x",
                "message": "msg",
                "expected_previous": "",
            },
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(
            any("forward methods" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events)
        )

    def test_runtime_attestation_blocks_missing_rollback_method(self) -> None:
        policy = PolicyEngine.from_profile("production_github_only")
        policy.egress_policy = EgressPolicy(allowed_hosts=("api.github.com",), allowed_methods=("GET", "POST"))
        client = GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)
        tool = GitHubCreateBranchTool(client)

        kernel, plan = _run_tool(
            tool,
            policy=policy,
            arguments={"repo": "o/r", "branch": "feature", "base": "main"},
        )

        self.assertEqual(plan.steps[0].status.value, "blocked")
        self.assertTrue(any("rollback" in str(event.payload.get("reason", "")) for event in kernel.audit_log.events))

    def test_validate_policy_profile_cli_target_exists(self) -> None:
        self.assertIsInstance(PolicyEngine.from_profile("production_locked_down"), PolicyEngine)
        self.assertIsInstance(PolicyEngine.from_profile("production_github_only"), PolicyEngine)


if __name__ == "__main__":
    unittest.main()
