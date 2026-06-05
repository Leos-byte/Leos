#!/usr/bin/env python3
"""Check scoped production readiness for bounded GitHub-only runtime."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from leos_agent import (  # noqa: E402
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
    GitHubRESTClient,
    GitHubUpdateFileTool,
    InMemoryGitHubClient,
    PolicyEngine,
    Reversibility,
    RiskLevel,
)
from leos_agent.policy import PRODUCTION_GITHUB_ONLY_TOOLS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check production_github_only readiness.")
    parser.add_argument("--profile", default="production_github_only")
    parser.add_argument("--require-smoke-evidence", action="store_true")
    parser.add_argument(
        "--smoke-evidence-path",
        default="docs/proofs/real_github_smoke_latest.json",
    )
    args = parser.parse_args()
    results = run_checks(
        ROOT,
        args.profile,
        include_release_proof=True,
        require_smoke_evidence=args.require_smoke_evidence,
        smoke_evidence_path=Path(args.smoke_evidence_path),
    )
    failed = [item for item in results if not item["ok"]]
    for item in results:
        status = "passed" if item["ok"] else "failed"
        print(f"{status}: {item['name']}")
        if not item["ok"]:
            print(f"  reason: {item['reason']}")
    print(f"production_readiness_status={'failed' if failed else 'passed'}")
    print(f"checks_passed={len(results) - len(failed)} checks_failed={len(failed)}")
    return 1 if failed else 0


def run_checks(
    root: Path,
    profile_name: str,
    *,
    include_release_proof: bool = True,
    require_smoke_evidence: bool = False,
    smoke_evidence_path: Path = Path("docs/proofs/real_github_smoke_latest.json"),
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if include_release_proof:
        checks.append(_release_proof_check(root))
    checks.extend(
        (
            _profile_check(profile_name),
            _allowed_tools_check(profile_name),
            _tool_metadata_check(),
            _runtime_surface_check(),
            _ci_check(root),
            _docs_check(root),
        )
    )
    if require_smoke_evidence:
        checks.append(_smoke_evidence_check(root, smoke_evidence_path))
    return checks


def _ok(name: str) -> dict[str, Any]:
    return {"name": name, "ok": True, "reason": None}


def _fail(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "ok": False, "reason": reason}


def _release_proof_check(root: Path) -> dict[str, Any]:
    proc = subprocess.run(  # nosec B603
        [sys.executable, "scripts/check_release_proof.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
    )
    if proc.returncode != 0:
        return _fail("release proof", (proc.stderr or proc.stdout).strip()[:500])
    return _ok("release proof")


def _profile_check(profile_name: str) -> dict[str, Any]:
    try:
        policy = PolicyEngine.from_profile(profile_name)
    except KeyError as exc:
        return _fail("policy profile", str(exc))
    if profile_name != "production_github_only":
        return _fail("policy profile", "only production_github_only is supported by this readiness check")
    if policy.egress_policy is None or policy.egress_policy.allowed_hosts != ("api.github.com",):
        return _fail("policy profile", "egress host must be exactly api.github.com")
    if not policy.network_default_deny:
        return _fail("policy profile", "network_default_deny must be true")
    if not policy.require_typed_goal_criteria:
        return _fail("policy profile", "typed criteria must be required")
    if not policy.require_signed_approval:
        return _fail("policy profile", "signed approval must be required")
    if tuple(policy.allowed_tools) != PRODUCTION_GITHUB_ONLY_TOOLS:
        return _fail("policy profile", "allowed tool list does not match bounded GitHub profile")
    return _ok("policy profile")


def _allowed_tools_check(profile_name: str) -> dict[str, Any]:
    policy = PolicyEngine.from_profile(profile_name)
    forbidden = [tool for tool in policy.allowed_tools if not (tool.startswith("github_") or tool == "echo")]
    if forbidden:
        return _fail("allowed tools", f"unexpected non-GitHub tools: {', '.join(forbidden)}")
    return _ok("allowed tools")


def _tool_metadata_check() -> dict[str, Any]:
    client = InMemoryGitHubClient()
    tools = (
        GitHubReadIssueTool(client),
        GitHubGetRepositoryTool(client),
        GitHubGetBranchTool(client),
        GitHubGetPRTool(client),
        GitHubGetFileTool(client),
        GitHubCreateBranchTool(client),
        GitHubUpdateFileTool(client),
        GitHubOpenPRTool(client),
        GitHubClosePRTool(client),
        GitHubDeleteBranchTool(client),
        GitHubCommentTool(client),
        GitHubCheckCIStatusTool(client),
    )
    for tool in tools:
        spec = tool.spec
        if not spec.network_access:
            return _fail("tool metadata", f"{spec.name} does not declare network_access")
        if spec.egress_host != "api.github.com":
            return _fail("tool metadata", f"{spec.name} egress_host is not api.github.com")
        if not spec.egress_methods:
            return _fail("tool metadata", f"{spec.name} has no forward egress methods")
        if spec.reversibility in {Reversibility.REVERSIBLE, Reversibility.COMPENSATABLE} and not (
            spec.rollback_egress_methods
        ):
            return _fail("tool metadata", f"{spec.name} has no rollback egress methods")
        if spec.default_risk in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}:
            if spec.causal_contract is None:
                return _fail("tool metadata", f"{spec.name} has no causal contract")
            if not spec.output_schema:
                return _fail("tool metadata", f"{spec.name} has no output schema")
            if spec.timeout_ms <= 0:
                return _fail("tool metadata", f"{spec.name} has no timeout")
    return _ok("tool metadata")


def _runtime_surface_check() -> dict[str, Any]:
    client = GitHubRESTClient(enforce_egress=True)
    required = (
        "runtime_egress_enforced",
        "runtime_egress_policy_configured",
        "runtime_egress_host",
        "runtime_allows_egress",
    )
    missing = [name for name in required if not hasattr(client, name)]
    if missing:
        return _fail("runtime surface", f"GitHubRESTClient missing {', '.join(missing)}")
    if not callable(getattr(GitHubGetFileTool(InMemoryGitHubClient()), "runtime_attestations", None)):
        return _fail("runtime surface", "GitHub tools do not expose runtime_attestations")
    return _ok("runtime surface")


def _ci_check(root: Path) -> dict[str, Any]:
    ci_path = root / ".github" / "workflows" / "ci.yml"
    real_write_path = root / ".github" / "workflows" / "github-real-write.yml"
    try:
        ci = ci_path.read_text(encoding="utf-8")
        real_write = real_write_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _fail("ci", str(exc))
    release_step = _workflow_step(ci, "Check release proof")
    if release_step is None or "github.ref == 'refs/heads/main'" not in release_step:
        return _fail("ci", "main-only release proof check is missing")
    readiness_step = _workflow_step(ci, "Check production readiness")
    if (
        readiness_step is None
        or "github.ref == 'refs/heads/main'" not in readiness_step
        or "check_production_readiness.py" not in readiness_step
        or "--profile production_github_only" not in readiness_step
        or "--require-smoke-evidence" not in readiness_step
        or "--smoke-evidence-path" not in readiness_step
        or "docs/proofs/real_github_smoke_latest.json" not in readiness_step
        or "download_smoke_evidence.py" not in readiness_step
    ):
        return _fail("ci", "main-only exact-HEAD production readiness check is missing")
    if "workflow_dispatch" not in real_write:
        return _fail("ci", "real-write workflow is not workflow_dispatch-only")
    forbidden_triggers = ("pull_request:", "push:")
    if any(trigger in real_write for trigger in forbidden_triggers):
        return _fail("ci", "real-write workflow must not run on push or pull_request")
    return _ok("ci")


def _workflow_step(workflow: str, name: str) -> str | None:
    match = re.search(
        rf"(?ms)^\s*-\s+name:\s*{re.escape(name)}\s*$.*?(?=^\s*-\s+(?:name:|uses:)|\Z)",
        workflow,
    )
    return match.group(0) if match else None


def _docs_check(root: Path) -> dict[str, Any]:
    candidates = (root / "README.md", root / "docs" / "ARCHITECTURE.md", root / "docs" / "THREAT_MODEL.md")
    text = "\n".join(path.read_text(encoding="utf-8") for path in candidates if path.exists()).lower()
    if "not a general open-world" not in text:
        return _fail("docs", "docs must state Leos is not a general open-world agent")
    if not (root / "docs" / "RELEASE.md").exists():
        return _fail("docs", "docs/RELEASE.md is missing")
    if not ((root / "docs" / "SECURITY.md").exists() or (root / "docs" / "THREAT_MODEL.md").exists()):
        return _fail("docs", "security or threat model docs are missing")
    return _ok("docs")


_SMOKE_EVIDENCE_TYPES = {
    "private_disposable_github_real_write_smoke",
    "disposable_github_real_write_smoke",
}
_SMOKE_FORBIDDEN_PATTERNS = {
    "github_classic_token": re.compile(r"ghp_[A-Za-z0-9_]+", re.IGNORECASE),
    "github_fine_grained_token": re.compile(r"github_pat_[A-Za-z0-9_]+", re.IGNORECASE),
    "authorization_marker": re.compile(r"authorization", re.IGNORECASE),
    "bearer_marker": re.compile(r"bearer\s", re.IGNORECASE),
    "github_token_secret_name": re.compile(r"LEOS_GITHUB_TOKEN", re.IGNORECASE),
    "approval_hmac_secret_name": re.compile(r"LEOS_APPROVAL_HMAC_SECRET", re.IGNORECASE),
    "raw_hmac_signature": re.compile(r"hmac-sha256:[0-9a-fA-F]{32,}", re.IGNORECASE),
}
_SMOKE_REQUIRED_CHECKS = (
    "private_repo_used",
    "disposable_repo_guard_passed",
    "runtime_attestation_verified",
    "runtime_egress_enforced",
    "runtime_egress_policy_configured",
    "signed_approval_required",
    "signed_approval_enforced",
    "approval_signature_verified",
    "branch_created",
    "file_updated",
    "pr_opened",
    "read_back_verified",
    "goal_evaluation_succeeded",
    "cleanup_requested",
    "pr_closed",
    "branch_deleted",
    "source_repo_unchanged",
    "token_redacted",
    "secret_scan_safe",
)


def _smoke_evidence_check(
    root: Path,
    evidence_path: Path,
    *,
    expected_head: str | None = None,
) -> dict[str, Any]:
    path = evidence_path if evidence_path.is_absolute() else root / evidence_path
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _fail("smoke evidence", f"could not read smoke evidence: {type(exc).__name__}")
    findings = [name for name, pattern in _SMOKE_FORBIDDEN_PATTERNS.items() if pattern.search(raw)]
    if findings:
        return _fail("smoke evidence", f"smoke evidence contains forbidden marker(s): {', '.join(findings)}")
    try:
        evidence = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _fail("smoke evidence", f"invalid smoke evidence JSON: {exc.msg}")
    if not isinstance(evidence, dict):
        return _fail("smoke evidence", "smoke evidence must be a JSON object")
    checks = evidence.get("checks")
    if not isinstance(checks, dict):
        return _fail("smoke evidence", "smoke evidence checks must be an object")
    current_head = expected_head or _git_head(root)
    if not current_head:
        return _fail("smoke evidence", "current git HEAD could not be determined")
    if evidence.get("leos_commit_sha") != current_head:
        return _fail("smoke evidence", "leos_commit_sha must match current git HEAD")
    if not str(evidence.get("workflow_run_id", "")).strip():
        return _fail("smoke evidence", "workflow_run_id is required")
    generated_at = str(evidence.get("generated_at", ""))
    if not generated_at or not generated_at.endswith("Z"):
        return _fail("smoke evidence", "generated_at must be a UTC timestamp")

    expected_fields: dict[str, object] = {
        "schema_version": 1,
        "profile": "production_github_only",
        "status": "passed",
        "repository_visibility": "private",
        "repository_disposable": True,
        "workflow_trigger": "workflow_dispatch",
        "work_branch_prefix": "leos/",
    }
    for key, expected in expected_fields.items():
        if evidence.get(key) != expected:
            return _fail("smoke evidence", f"{key} must be {expected!r}")
    if evidence.get("evidence_type") not in _SMOKE_EVIDENCE_TYPES:
        return _fail("smoke evidence", "evidence_type is not an accepted GitHub smoke type")
    repository = str(evidence.get("repository_under_test", ""))
    if "leos-smoke" not in repository.lower():
        return _fail("smoke evidence", "repository_under_test must be a disposable leos-smoke repository")
    if repository == "Leos-byte/Leos":
        return _fail("smoke evidence", "repository_under_test must not be the Leos source repository")
    if checks.get("runtime_egress_host") != "api.github.com":
        return _fail("smoke evidence", "checks.runtime_egress_host must be api.github.com")
    if checks.get("approval_signature_algorithm") != "hmac-sha256":
        return _fail("smoke evidence", "checks.approval_signature_algorithm must be hmac-sha256")
    missing = [name for name in _SMOKE_REQUIRED_CHECKS if checks.get(name) is not True]
    if missing:
        return _fail("smoke evidence", f"smoke evidence checks are not true: {', '.join(missing)}")
    return _ok("smoke evidence")


def _git_head(root: Path) -> str:
    result = subprocess.run(  # nosec B603
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


if __name__ == "__main__":
    raise SystemExit(main())
