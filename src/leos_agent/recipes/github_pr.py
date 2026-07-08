"""Recipe: bounded GitHub single-file change delivered as a reviewable PR.

Three calls wrap the validated operator pipeline end to end:

1. :func:`prepare_single_file_pr` — draft the plan from the issue (read-only),
   fill in the bounded change, validate it, and build the approval bundle.
2. :func:`approve_single_file_pr` — emit an HMAC-signed decision bundle.
3. :func:`apply_single_file_pr` — run the full signed-apply path.

The recipe adds **no gating logic**: validation is ``validate_operator_plan``,
approval is ``build_approval_bundle``/``build_signed_decision_bundle``, and
apply is ``apply_operator_plan`` — so the environment write gate, policy
profile, egress enforcement, signed consume-once approvals, dry-run, and
read-back verification all still apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..github_operator import (
    OperatorResult,
    apply_operator_plan,
    build_approval_bundle,
    build_signed_decision_bundle,
    create_draft_plan,
    validate_operator_plan,
)
from ..github_tools import GitHubClient
from ..tools import Secret


@dataclass(frozen=True)
class GitHubFileChange:
    """A bounded single-file change addressing one GitHub issue."""

    repo: str
    issue_number: int
    path: str
    content: str
    work_branch: str
    commit_message: str | None = None
    expected_sha: str | None = None
    expected_previous: str | None = None
    base_branch: str = "main"


@dataclass(frozen=True)
class PreparedChange:
    """A ready operator plan plus its approval packets."""

    plan: dict[str, Any]
    approval: dict[str, Any]


def prepare_single_file_pr(
    change: GitHubFileChange,
    *,
    token: Secret | None = None,
    client: GitHubClient | None = None,
    expires_in_seconds: float = 900.0,
) -> PreparedChange:
    """Draft, fill, validate, and build approval packets. Performs no writes.

    Raises ``ValueError`` listing every validation issue when the change does
    not satisfy the operator plan constraints (leos/ branch prefix, exactly
    one optimistic guard, no secret-like content, ...).
    """
    plan = create_draft_plan(change.repo, change.issue_number, token=token, client=client)
    plan["status"] = "ready"
    plan["work_branch"] = change.work_branch
    plan["base_branch"] = change.base_branch
    plan_change = dict(plan["change"])
    plan_change["path"] = change.path
    plan_change["content"] = change.content
    if change.commit_message is not None:
        plan_change["commit_message"] = change.commit_message
    plan_change["expected_sha"] = change.expected_sha
    plan_change["expected_previous"] = change.expected_previous
    plan["change"] = plan_change
    issues = validate_operator_plan(plan)
    if issues:
        raise ValueError("; ".join(issues))
    approval = build_approval_bundle(plan, expires_in_seconds=expires_in_seconds)
    return PreparedChange(plan=plan, approval=approval)


def approve_single_file_pr(
    prepared: PreparedChange,
    *,
    approver: str,
    signature_secret: str,
    decision: str = "approve",
    reason: str | None = None,
) -> dict[str, Any]:
    """Emit an HMAC-signed decision bundle for the prepared change."""
    return build_signed_decision_bundle(
        prepared.approval,
        decision_value=decision,
        approver=approver,
        signature_secret=signature_secret,
        reason=reason,
    )


def apply_single_file_pr(
    prepared: PreparedChange,
    decision_bundle: dict[str, Any],
    *,
    token_value: str,
    signature_secret: str,
    work_dir: Path,
    client: GitHubClient | None = None,
) -> OperatorResult:
    """Run the full signed-apply path for the prepared change.

    Requires ``LEOS_ENABLE_REAL_GITHUB_WRITES=1`` in the environment — the
    recipe inherits the write gate, it does not replace it. Audit logs and
    consume-once approval receipts land under ``work_dir``.
    """
    return apply_operator_plan(
        prepared.plan,
        prepared.approval,
        decision_bundle,
        token=Secret(token_value),
        signature_secret=signature_secret,
        audit_path=work_dir / "audit.jsonl",
        receipt_dir=work_dir / "receipts",
        client=client,
    )
