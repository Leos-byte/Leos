#!/usr/bin/env python3
"""Check scoped production readiness for bounded GitHub-only runtime."""

from __future__ import annotations

import argparse
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
    GitHubCommentTool,
    GitHubCreateBranchTool,
    GitHubGetFileTool,
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
    args = parser.parse_args()
    results = run_checks(ROOT, args.profile, include_release_proof=True)
    failed = [item for item in results if not item["ok"]]
    for item in results:
        status = "passed" if item["ok"] else "failed"
        print(f"{status}: {item['name']}")
        if not item["ok"]:
            print(f"  reason: {item['reason']}")
    print(f"production_readiness_status={'failed' if failed else 'passed'}")
    print(f"checks_passed={len(results) - len(failed)} checks_failed={len(failed)}")
    return 1 if failed else 0


def run_checks(root: Path, profile_name: str, *, include_release_proof: bool = True) -> list[dict[str, Any]]:
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
        GitHubGetFileTool(client),
        GitHubCreateBranchTool(client),
        GitHubUpdateFileTool(client),
        GitHubOpenPRTool(client),
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
    if "Check release proof" not in ci or "github.ref == 'refs/heads/main'" not in ci:
        return _fail("ci", "main-only release proof check is missing")
    if (
        "Check production readiness" not in ci
        or "check_production_readiness.py --profile production_github_only" not in ci
    ):
        return _fail("ci", "main-only production readiness check is missing")
    if "workflow_dispatch" not in real_write:
        return _fail("ci", "real-write workflow is not workflow_dispatch-only")
    forbidden_triggers = ("pull_request:", "push:")
    if any(trigger in real_write for trigger in forbidden_triggers):
        return _fail("ci", "real-write workflow must not run on push or pull_request")
    return _ok("ci")


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


if __name__ == "__main__":
    raise SystemExit(main())
