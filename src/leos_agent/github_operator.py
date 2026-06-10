"""Operator-facing GitHub-only planning and approval file workflow."""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .approval import ApprovalDecision, ApprovalDecisionValue, ApprovalPacket, build_approval_packet
from .approval_exchange import sign_approval_decision, verify_approval_decision_signature
from .audit import AuditLog
from .causal import CausalGraph
from .enums import Reversibility, StepStatus
from .errors import LeosError, PolicyDenied
from .github_client import GitHubRESTClient
from .github_tools import (
    GitHubClient,
    GitHubCreateBranchTool,
    GitHubGetFileTool,
    GitHubOpenPRTool,
    GitHubReadIssueTool,
    GitHubUpdateFileTool,
)
from .goal_evaluator import GoalEvaluationStatus, GoalEvaluator
from .goals import Goal
from .kernel import AgentKernel
from .plans import ActionStep, TransactionPlan
from .policy import ApprovalGate, PolicyEngine
from .sanitization import assert_no_secrets, safe_json_dumps
from .state import TrustLevel, WorldState
from .tools import Secret, Tool, ToolRegistry

PLAN_SCHEMA = "leos.github_issue_plan"
APPROVAL_SCHEMA = "leos.approval_bundle"
DECISION_SCHEMA = "leos.approval_decisions"
RECEIPT_SCHEMA = "leos.approval_consumption_receipt"
SCHEMA_VERSION = 1
PROFILE = "production_github_only"
GITHUB_HOST = "api.github.com"


@dataclass(frozen=True)
class OperatorResult:
    ok: bool
    message: str
    data: dict[str, Any]


def production_github_doctor(profile: str) -> OperatorResult:
    if profile != PROFILE:
        return OperatorResult(False, f"doctor supports only {PROFILE}", {})
    policy = PolicyEngine.from_profile(profile)
    client = GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)
    registry = build_github_operator_registry(client)
    failures: list[str] = []
    if policy.egress_policy is None or policy.egress_policy.allowed_hosts != (GITHUB_HOST,):
        failures.append("egress host is not fixed to api.github.com")
    if not policy.network_default_deny:
        failures.append("network default deny is not enabled")
    if not policy.require_signed_approval:
        failures.append("signed approval is not required")
    if os.environ.get("LEOS_ENABLE_REAL_GITHUB_WRITES") == "1":
        failures.append("real GitHub writes are enabled in the current environment")
    for name in registry.names():
        tool = registry.get(name)
        if not tool.spec.network_access or tool.spec.egress_host != GITHUB_HOST:
            failures.append(f"{name} has invalid network metadata")
    data = {
        "profile": profile,
        "runtime_egress_enforced": client.runtime_egress_enforced,
        "runtime_egress_policy_configured": client.runtime_egress_policy_configured,
        "runtime_egress_host": client.runtime_egress_host,
        "signed_approval_required": policy.require_signed_approval,
        "real_writes_enabled": os.environ.get("LEOS_ENABLE_REAL_GITHUB_WRITES") == "1",
        "tool_count": len(registry.names()),
    }
    return OperatorResult(not failures, "; ".join(failures) if failures else "production GitHub doctor passed", data)


def github_issue_dry_run(
    repo: str,
    issue_number: int,
    *,
    token: Secret | None = None,
    client: GitHubClient | None = None,
    audit_path: Path | None = None,
) -> OperatorResult:
    _validate_repo(repo)
    if issue_number < 1:
        raise ValueError("issue number must be >= 1")
    policy = PolicyEngine.from_profile(PROFILE)
    rest_client = client or GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)
    registry = ToolRegistry()
    registry.register(GitHubReadIssueTool(rest_client))
    audit = AuditLog(path=audit_path) if audit_path is not None else AuditLog()
    kernel = AgentKernel(registry, policy, causal_model=CausalGraph(), audit_log=audit)
    goal = Goal(
        description=f"Observe GitHub issue {repo}#{issue_number}",
        success_criteria=["GitHub issue observed"],
        criteria=({"key": "github_issue", "op": "exists"},),
        stop_conditions=["Issue observed or request blocked"],
    )
    arguments: dict[str, Any] = {"repo": repo, "issue_number": issue_number}
    if token is not None:
        arguments["token"] = token
    executed = kernel.run(kernel.build_plan(goal, [ActionStep("github_read_issue", arguments, "Read issue safely.")]))
    step = executed.steps[0]
    if step.status is not StepStatus.VERIFIED:
        return OperatorResult(False, f"GitHub issue dry-run {step.status.value}", {"writes_performed": False})
    issue = dict(kernel.state.facts.get("github_issue", {}))
    return OperatorResult(
        True,
        "GitHub issue dry-run completed without writes",
        {
            "repo": repo,
            "issue_number": issue_number,
            "title": issue.get("title", ""),
            "state": issue.get("state", ""),
            "html_url": issue.get("html_url", ""),
            "writes_performed": False,
            "egress_host": GITHUB_HOST,
        },
    )


def create_draft_plan(
    repo: str,
    issue_number: int,
    *,
    token: Secret | None = None,
    client: GitHubClient | None = None,
) -> dict[str, Any]:
    observed = github_issue_dry_run(repo, issue_number, token=token, client=client)
    if not observed.ok:
        raise LeosError(observed.message)
    issue_title = str(observed.data.get("title") or f"Issue #{issue_number}")
    plan_id = str(uuid4())
    safe_repo = repo.replace("/", "-")
    return {
        "schema": PLAN_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": "draft",
        "profile": PROFILE,
        "goal_id": str(uuid4()),
        "plan_id": plan_id,
        "step_ids": {
            "create_branch": str(uuid4()),
            "update_file": str(uuid4()),
            "open_pr": str(uuid4()),
        },
        "repo": repo,
        "issue": {
            "number": issue_number,
            "title": issue_title,
            "html_url": observed.data.get("html_url", ""),
            "trust_level": "untrusted_external",
        },
        "base_branch": "main",
        "work_branch": "",
        "change": {
            "path": "",
            "content": "",
            "commit_message": f"Address issue #{issue_number}",
            "expected_sha": None,
            "expected_previous": None,
        },
        "pull_request": {
            "title": f"Fix #{issue_number}: {issue_title}",
            "body": f"Addresses #{issue_number}.\n\nPrepared by Leos for human-approved execution.",
            "idempotency_key": f"leos-{safe_repo}-issue-{issue_number}-{plan_id}",
        },
        "egress": {
            "host": GITHUB_HOST,
            "forward_methods": ["GET", "POST", "PUT"],
            "rollback_methods": ["DELETE", "GET", "PATCH", "PUT"],
        },
        "operator_instructions": [
            "Set status to ready.",
            "Set work_branch to a leos/ prefixed branch.",
            "Set change.path and change.content.",
            "Set exactly one optimistic guard: expected_sha or expected_previous.",
        ],
    }


def validate_operator_plan(data: dict[str, Any], *, require_ready: bool = True) -> list[str]:
    issues: list[str] = []
    if data.get("schema") != PLAN_SCHEMA or data.get("schema_version") != SCHEMA_VERSION:
        issues.append("unsupported GitHub operator plan schema")
    if data.get("profile") != PROFILE:
        issues.append(f"profile must be {PROFILE}")
    try:
        _validate_repo(str(data.get("repo", "")))
    except ValueError as exc:
        issues.append(str(exc))
    if require_ready and data.get("status") != "ready":
        issues.append("plan status must be ready")
    branch = str(data.get("work_branch", ""))
    if require_ready and (not branch.startswith("leos/") or branch in {"leos/", "leos/main"}):
        issues.append("work_branch must use a non-empty leos/ prefix")
    if str(data.get("base_branch", "")).lower() not in {"main", "master", "trunk", "release"}:
        issues.append("base_branch must be an explicit protected source branch")
    change = data.get("change")
    if not isinstance(change, dict):
        issues.append("change must be an object")
    elif require_ready:
        if not str(change.get("path", "")).strip():
            issues.append("change.path is required")
        if not isinstance(change.get("content"), str):
            issues.append("change.content must be a string")
        if not str(change.get("commit_message", "")).strip():
            issues.append("change.commit_message is required")
        has_sha = change.get("expected_sha") is not None
        has_previous = change.get("expected_previous") is not None
        if has_sha == has_previous:
            issues.append("exactly one of expected_sha or expected_previous is required")
    egress = data.get("egress")
    if not isinstance(egress, dict) or egress.get("host") != GITHUB_HOST:
        issues.append("egress host must be api.github.com")
    try:
        assert_no_secrets(data)
    except Exception as exc:
        issues.append(f"plan contains secret-like value: {type(exc).__name__}")
    return issues


def compile_operator_plan(data: dict[str, Any], *, token: Secret | None = None) -> TransactionPlan:
    issues = validate_operator_plan(data)
    if issues:
        raise ValueError("; ".join(issues))
    repo = str(data["repo"])
    branch = str(data["work_branch"])
    base = str(data["base_branch"])
    change = dict(data["change"])
    pull_request = dict(data["pull_request"])
    token_args = {"token": token or Secret("approval-placeholder")}
    steps = [
        ActionStep(
            "github_create_branch",
            {"repo": repo, "branch": branch, "base": base, **token_args},
            "Create the bounded working branch.",
            step_id=str(data["step_ids"]["create_branch"]),
        ),
        ActionStep(
            "github_update_file",
            _without_none(
                {
                    "repo": repo,
                    "path": str(change["path"]),
                    "branch": branch,
                    "content": str(change["content"]),
                    "message": str(change["commit_message"]),
                    "expected_sha": change.get("expected_sha"),
                    "expected_previous": change.get("expected_previous"),
                    **token_args,
                }
            ),
            "Apply one operator-authored file update with an optimistic guard.",
            step_id=str(data["step_ids"]["update_file"]),
        ),
        ActionStep(
            "github_open_pr",
            {
                "repo": repo,
                "title": str(pull_request["title"]),
                "body": str(pull_request["body"]),
                "head": branch,
                "base": base,
                "idempotency_key": str(pull_request["idempotency_key"]),
                **token_args,
            },
            "Open an idempotent pull request for human review.",
            idempotency_key=str(pull_request["idempotency_key"]),
            step_id=str(data["step_ids"]["open_pr"]),
        ),
    ]
    goal = Goal(
        description=f"Prepare a reviewed pull request for {repo} issue #{data['issue']['number']}",
        success_criteria=(),
        criteria=(
            {"key": "github_branch", "op": "exists"},
            {"key": "github_file_updated", "op": "exists"},
            {"key": "github_pr", "op": "exists"},
            {"key": "read_back_verified", "op": "equals", "value": True},
        ),
        stop_conditions=["PR opened and read-back verified or execution blocked"],
        goal_id=str(data["goal_id"]),
    )
    return TransactionPlan(goal=goal, steps=steps, plan_id=str(data["plan_id"]))


def build_approval_bundle(data: dict[str, Any], *, expires_in_seconds: float = 900.0) -> dict[str, Any]:
    plan = compile_operator_plan(data)
    policy = PolicyEngine.from_profile(PROFILE)
    client = GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)
    registry = build_github_operator_registry(client)
    packets: list[dict[str, Any]] = []
    for step in plan.steps:
        tool = registry.get(step.tool_name)
        _hydrate_step(step, tool, policy)
        packet = build_approval_packet(
            plan=plan,
            step=step,
            tool=tool,
            dry_run_summary=tool.dry_run(step.arguments, _empty_state()).message,
            profile=PROFILE,
            expires_in_seconds=expires_in_seconds,
        )
        packets.append(packet.as_dict())
    bundle = {
        "schema": APPROVAL_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "plan_id": plan.plan_id,
        "goal_id": plan.goal.goal_id,
        "plan_digest": operator_plan_digest(data),
        "packets": packets,
    }
    assert_no_secrets(bundle)
    return bundle


def build_signed_decision_bundle(
    approval_bundle: dict[str, Any],
    *,
    decision_value: str,
    approver: str,
    signature_secret: str,
    reason: str | None = None,
) -> dict[str, Any]:
    if approval_bundle.get("schema") != APPROVAL_SCHEMA:
        raise ValueError("invalid approval bundle schema")
    decisions = []
    for packet_data in approval_bundle.get("packets", []):
        packet = ApprovalPacket.from_mapping(dict(packet_data))
        decision = ApprovalDecision(
            packet.approval_id,
            packet.step_hash,
            ApprovalDecisionValue(decision_value),
            approver=approver,
            reason=reason,
        )
        decisions.append({**decision.as_dict(), "signature": sign_approval_decision(decision, signature_secret)})
    return {
        "schema": DECISION_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "profile": approval_bundle.get("profile"),
        "plan_id": approval_bundle.get("plan_id"),
        "plan_digest": approval_bundle.get("plan_digest"),
        "decisions": decisions,
    }


class PreparedFileApprovalGate(ApprovalGate):
    """Consume pre-created signed approval packets exactly once."""

    signed_approval_enforced = True

    def __init__(
        self,
        *,
        approval_bundle: dict[str, Any],
        decision_bundle: dict[str, Any],
        signature_secret: str,
        receipt_dir: Path,
    ) -> None:
        super().__init__()
        if approval_bundle.get("schema") != APPROVAL_SCHEMA:
            raise ValueError("invalid approval bundle schema")
        if decision_bundle.get("schema") != DECISION_SCHEMA:
            raise ValueError("invalid approval decision schema")
        if approval_bundle.get("plan_digest") != decision_bundle.get("plan_digest"):
            raise ValueError("approval decision plan digest mismatch")
        self.packets = {
            packet.step_id: packet
            for packet in (ApprovalPacket.from_mapping(dict(item)) for item in approval_bundle.get("packets", []))
        }
        self.decisions = {str(item["approval_id"]): dict(item) for item in decision_bundle.get("decisions", [])}
        self.signature_secret = signature_secret
        self.receipt_dir = receipt_dir
        self.last_decision_signature_valid = False
        self.last_decision_signature_algorithm: str | None = None

    def prepare_packet(self, packet: ApprovalPacket, step: ActionStep) -> ApprovalPacket:
        stored = self.packets.get(step.step_id)
        if stored is None:
            raise PolicyDenied("missing approval packet for step")
        if (
            stored.step_hash != packet.step_hash
            or stored.profile != packet.profile
            or stored.plan_id != packet.plan_id
            or stored.goal_id != packet.goal_id
            or stored.tool_name != packet.tool_name
        ):
            raise PolicyDenied("pre-created approval packet does not match current step")
        return stored

    def request_packet(self, packet: ApprovalPacket, step: ActionStep) -> ApprovalDecision:
        del step
        self.last_decision_signature_valid = False
        self.last_decision_signature_algorithm = None
        data = self.decisions.get(packet.approval_id)
        if data is None:
            return ApprovalDecision(
                packet.approval_id,
                packet.step_hash,
                ApprovalDecisionValue.DENY,
                reason="approval decision missing",
            )
        decision = ApprovalDecision(
            approval_id=str(data["approval_id"]),
            step_hash=str(data["step_hash"]),
            decision=ApprovalDecisionValue(str(data["decision"])),
            decided_at=float(data["decided_at"]),
            approver=str(data["approver"]) if data.get("approver") is not None else None,
            reason=str(data["reason"]) if data.get("reason") is not None else None,
        )
        signature = data.get("signature")
        if not isinstance(signature, str) or not verify_approval_decision_signature(
            decision, self.signature_secret, signature
        ):
            return ApprovalDecision(
                packet.approval_id,
                packet.step_hash,
                ApprovalDecisionValue.DENY,
                approver=decision.approver,
                reason="approval decision signature invalid",
            )
        self.last_decision_signature_valid = True
        self.last_decision_signature_algorithm = "hmac-sha256"
        return decision

    def consume_approval(
        self,
        packet: ApprovalPacket,
        decision: ApprovalDecision,
        step: ActionStep,
    ) -> str | None:
        self.receipt_dir.mkdir(parents=True, exist_ok=True)
        path = self.receipt_dir / f"{_safe_identifier(packet.approval_id)}.json"
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "approval_id": packet.approval_id,
            "step_hash": packet.step_hash,
            "step_id": step.step_id,
            "tool_name": step.tool_name,
            "decision": decision.decision.value,
            "consumed_at": time.time(),
            "profile": packet.profile,
        }
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return "approval decision was already consumed"
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(safe_json_dumps(receipt))
            handle.write("\n")
        return None


def apply_operator_plan(
    plan_data: dict[str, Any],
    approval_bundle: dict[str, Any],
    decision_bundle: dict[str, Any],
    *,
    token: Secret,
    signature_secret: str,
    audit_path: Path,
    receipt_dir: Path,
    client: GitHubClient | None = None,
) -> OperatorResult:
    if os.environ.get("LEOS_ENABLE_REAL_GITHUB_WRITES") != "1":
        return OperatorResult(False, "real GitHub writes are disabled", {})
    issues = validate_operator_plan(plan_data)
    if issues:
        return OperatorResult(False, "; ".join(issues), {})
    if approval_bundle.get("profile") != PROFILE or decision_bundle.get("profile") != PROFILE:
        return OperatorResult(False, "approval profile does not match production_github_only", {})
    if approval_bundle.get("plan_id") != plan_data.get("plan_id"):
        return OperatorResult(False, "approval plan_id does not match the current plan", {})
    if decision_bundle.get("plan_id") != plan_data.get("plan_id"):
        return OperatorResult(False, "approval decision plan_id does not match the current plan", {})
    digest = operator_plan_digest(plan_data)
    if approval_bundle.get("plan_digest") != digest or decision_bundle.get("plan_digest") != digest:
        return OperatorResult(False, "approval does not match the current plan", {})
    policy = PolicyEngine.from_profile(PROFILE)
    rest_client = client or GitHubRESTClient(egress_policy=policy.egress_policy, enforce_egress=True)
    registry = build_github_operator_registry(rest_client)
    gate = PreparedFileApprovalGate(
        approval_bundle=approval_bundle,
        decision_bundle=decision_bundle,
        signature_secret=signature_secret,
        receipt_dir=receipt_dir,
    )
    audit = AuditLog(path=audit_path)
    kernel = AgentKernel(registry, policy, causal_model=CausalGraph(), audit_log=audit, approval_gate=gate)
    plan = compile_operator_plan(plan_data, token=token)
    executed = kernel.run(plan)
    if not all(step.status is StepStatus.VERIFIED for step in executed.steps):
        return OperatorResult(False, "GitHub apply was blocked or failed", {"audit_path": str(audit_path)})
    change = dict(plan_data["change"])
    read_goal = Goal(
        description="Verify the GitHub file update",
        success_criteria=["updated file read back"],
        criteria=({"key": "github_file", "op": "exists"},),
        stop_conditions=["File observed or blocked"],
    )
    read_step = ActionStep(
        "github_get_file",
        {
            "repo": str(plan_data["repo"]),
            "path": str(change["path"]),
            "ref": str(plan_data["work_branch"]),
            "token": token,
        },
        "Read back the updated file through the tool boundary.",
    )
    read_result = kernel.run(kernel.build_plan(read_goal, [read_step]))
    file_fact = kernel.state.facts.get("github_file", {})
    verified = (
        read_result.steps[0].status is StepStatus.VERIFIED
        and isinstance(file_fact, dict)
        and file_fact.get("content") == change["content"]
    )
    kernel.state.observe({"read_back_verified": verified}, trust_level=TrustLevel.VERIFIED)
    evaluation = GoalEvaluator().evaluate(executed.goal, kernel.state, kernel.transactions.track_progress(executed))
    audit.record(
        "github.operator.goal_evaluated",
        "GitHub operator goal evaluated after read-back verification",
        plan_id=plan.plan_id,
        goal_id=plan.goal.goal_id,
        evaluation_status=evaluation.status.value,
    )
    if not verified or evaluation.status is not GoalEvaluationStatus.SUCCEEDED:
        return OperatorResult(False, "post-action verification failed", {"audit_path": str(audit_path)})
    pr = dict(kernel.state.facts.get("github_pr", {}))
    return OperatorResult(
        True,
        "GitHub plan applied and verified; pull request remains open for human review",
        {
            "repo": plan_data["repo"],
            "branch": plan_data["work_branch"],
            "pr_number": pr.get("number"),
            "pr_url": pr.get("html_url"),
            "audit_path": str(audit_path),
            "evaluation_status": evaluation.status.value,
            "automatic_merge": False,
        },
    )


def build_github_operator_registry(client: GitHubClient) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        GitHubReadIssueTool(client),
        GitHubGetFileTool(client),
        GitHubCreateBranchTool(client),
        GitHubUpdateFileTool(client),
        GitHubOpenPRTool(client),
    ):
        registry.register(tool)
    return registry


def operator_plan_digest(data: dict[str, Any]) -> str:
    assert_no_secrets(data)
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def write_private_json(path: Path, data: dict[str, Any]) -> None:
    assert_no_secrets(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with suppress(OSError):
        path.chmod(0o600)


def load_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON file must contain an object")
    return data


def decision_path_for(approval_path: Path) -> Path:
    if approval_path.suffix == ".json":
        return approval_path.with_name(f"{approval_path.stem}.decision.json")
    return approval_path.with_name(f"{approval_path.name}.decision.json")


def _hydrate_step(step: ActionStep, tool: Tool, policy: PolicyEngine) -> None:
    step.required_permissions = tuple(tool.spec.permissions)
    step.risk = policy.assess(tool, step.arguments)
    step.reversibility = tool.spec.reversibility or Reversibility.IRREVERSIBLE
    step.compensation_strategy = tool.spec.compensation_strategy
    step.rollback_reliability = tool.spec.rollback_reliability


def _validate_repo(repo: str) -> None:
    parts = repo.split("/")
    if len(parts) != 2 or not all(part and part.replace("-", "").replace("_", "").isalnum() for part in parts):
        raise ValueError("repo must be OWNER/REPO")


def _safe_identifier(value: str) -> str:
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
    if not safe or safe != value:
        raise ValueError("identifier contains unsafe path characters")
    return safe


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _empty_state() -> WorldState:
    return WorldState()
