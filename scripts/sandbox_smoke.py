#!/usr/bin/env python3
"""Real-container sandbox isolation smoke producing release evidence.

Runs a hardened rootless container through the production sandbox backends and
observes every isolation property end to end: network egress denial, non-root
uid, read-only rootfs with a writable tmpfs ``/tmp``, pids and memory limits
(both configured in the cgroup and enforced by triggering them), timeout kill,
and the fail-closed microVM path. Evidence follows the same model as the
GitHub real-write smoke: gitignored JSON bound to the current commit, uploaded
as a CI artifact, validated by ``check_production_readiness.py``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B603 B404 - fixed argv, pinned public test image
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from leos_agent.enums import SandboxPolicy  # noqa: E402
from leos_agent.sandbox import SandboxCommand, SandboxResult, SandboxUnavailable  # noqa: E402
from leos_agent.sandbox_backends import RootlessPodmanSandboxRunner, resolve_sandbox_runner  # noqa: E402
from leos_agent.sanitization import assert_no_secrets  # noqa: E402

EVIDENCE_TYPE = "container_sandbox_isolation_smoke"
DEFAULT_IMAGE = "docker.io/library/alpine:3.21"
DEFAULT_EVIDENCE_OUT = "docs/proofs/sandbox_smoke_latest.json"

_FORBIDDEN_EVIDENCE_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9_]+", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9_]+", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"bearer\s", re.IGNORECASE),
    re.compile(r"hmac-sha256:[0-9a-fA-F]{32,}", re.IGNORECASE),
    re.compile(r"://[^\s\"]+:[^\s\"]+@", re.IGNORECASE),
)


class SandboxSmokeError(RuntimeError):
    """A sandbox smoke invariant was violated."""


def initial_checks() -> dict[str, object]:
    return {
        "runtime_available": False,
        "non_root_user_enforced": False,
        "network_egress_blocked": False,
        "read_only_rootfs_enforced": False,
        "tmpfs_tmp_writable": False,
        "pids_limit_configured": False,
        "pids_limit_enforced": False,
        "memory_limit_configured": False,
        "memory_limit_enforced": False,
        "timeout_kill_enforced": False,
        "microvm_fails_closed": False,
    }


def build_evidence() -> dict[str, Any]:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    return {
        "schema_version": 1,
        "evidence_type": EVIDENCE_TYPE,
        "status": "failed",
        "runtime": None,
        "runtime_version": None,
        "image": DEFAULT_IMAGE,
        "leos_commit_sha": os.environ.get("GITHUB_SHA") or _git_head(),
        "workflow_run_id": run_id,
        "run_id": run_id,
        "workflow_trigger": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "failure_type": None,
        "failure_summary": None,
        "generated_at": _utc_now(),
        "checks": initial_checks(),
    }


def _memory_bytes(limit: str) -> int:
    units = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if limit[-1].lower() in units:
        return int(limit[:-1]) * units[limit[-1].lower()]
    return int(limit)


def run_smoke(
    runner: Any | None = None,
    *,
    oom_runner: Any | None = None,
    workspace: Path | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the isolation probes and return the evidence document.

    ``runner`` (and the low-memory ``oom_runner`` used to trigger the OOM kill
    quickly) are injectable for unit tests; when omitted, real
    ``RootlessPodmanSandboxRunner`` instances are constructed and the image is
    pre-pulled so probe timeouts measure the command, not the pull.
    """
    evidence = evidence if evidence is not None else build_evidence()
    checks = evidence["checks"]
    workspace = workspace or Path(os.environ.get("LEOS_SANDBOX_SMOKE_WORKSPACE", ROOT / ".sandbox-smoke-ws"))
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        if runner is None:
            podman = shutil.which("podman")
            if podman is None:
                raise SandboxUnavailable("podman runtime is not available")
            _pull_image(podman, DEFAULT_IMAGE)
            runner = RootlessPodmanSandboxRunner(workspace, runtime=podman, image=DEFAULT_IMAGE)
            oom_runner = RootlessPodmanSandboxRunner(workspace, runtime=podman, image=DEFAULT_IMAGE, memory_limit="64m")
            evidence["runtime"] = "podman"
            evidence["runtime_version"] = _runtime_version(podman)
        else:
            evidence["runtime"] = evidence.get("runtime") or "injected"
        oom_runner = oom_runner if oom_runner is not None else runner
        checks["runtime_available"] = True

        # A failing container start would make every expect-failure probe pass
        # vacuously; prove the container actually runs before probing denials.
        sanity = _run(runner, ["echo", "sandbox-smoke-sanity"])
        if not sanity.ok or "sandbox-smoke-sanity" not in sanity.stdout:
            raise SandboxSmokeError(f"container sanity run failed: {(sanity.stderr or sanity.message).strip()[:200]}")

        _probe_identity(runner, checks)
        _probe_filesystem(runner, checks)
        _probe_limits(runner, oom_runner, checks)
        _probe_timeout(runner, checks)
        _probe_microvm_fails_closed(workspace, checks)

        failed = [name for name, value in checks.items() if value is not True]
        if failed:
            raise SandboxSmokeError(f"checks did not pass: {', '.join(failed)}")
        evidence["status"] = "passed"
    except SandboxUnavailable as exc:
        evidence["failure_type"] = "runtime_unavailable"
        evidence["failure_summary"] = str(exc)
    except SandboxSmokeError as exc:
        evidence["failure_type"] = "isolation_check_failed"
        evidence["failure_summary"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - evidence must always be writable
        evidence["failure_type"] = "unexpected_error"
        evidence["failure_summary"] = type(exc).__name__
    return evidence


def _probe_identity(runner: Any, checks: dict[str, object]) -> None:
    result = _run(runner, ["id", "-u"])
    checks["non_root_user_enforced"] = result.ok and result.stdout.strip() == "65532"
    egress = _run(runner, ["wget", "-q", "-T", "2", "-O-", "http://example.com"])
    checks["network_egress_blocked"] = (not egress.ok) and egress.returncode not in (0, None)


def _probe_filesystem(runner: Any, checks: dict[str, object]) -> None:
    denied = _run(runner, ["sh", "-c", "echo probe > /etc/probe"])
    checks["read_only_rootfs_enforced"] = not denied.ok
    allowed = _run(runner, ["sh", "-c", "echo probe > /tmp/probe && cat /tmp/probe"])
    checks["tmpfs_tmp_writable"] = allowed.ok and "probe" in allowed.stdout


def _probe_limits(runner: Any, oom_runner: Any, checks: dict[str, object]) -> None:
    pids = _run(runner, ["cat", "/sys/fs/cgroup/pids.max"])
    checks["pids_limit_configured"] = pids.ok and pids.stdout.strip() == str(runner.pids_limit)
    fork_bomb = _run(runner, ["sh", "-c", "for i in $(seq 1 200); do sleep 2 & done; wait"], timeout=30.0)
    checks["pids_limit_enforced"] = (not fork_bomb.ok) and not fork_bomb.timed_out

    memory = _run(runner, ["cat", "/sys/fs/cgroup/memory.max"])
    expected = str(_memory_bytes(runner.memory_limit))
    checks["memory_limit_configured"] = memory.ok and memory.stdout.strip() == expected
    # Allocate ~100MB in a container capped well below it; the OOM kill lands
    # in seconds where allocating past the default 512m limit is too slow.
    oom = _run(oom_runner, ["sh", "-c", 'x=$(head -c 100000000 /dev/zero | tr "\\0" "a"); echo survived'])
    checks["memory_limit_enforced"] = (not oom.ok) and not oom.timed_out and "survived" not in oom.stdout


def _probe_timeout(runner: Any, checks: dict[str, object]) -> None:
    result = _run(runner, ["sleep", "30"], timeout=3.0)
    checks["timeout_kill_enforced"] = result.timed_out and result.returncode is None


def _probe_microvm_fails_closed(workspace: Path, checks: dict[str, object]) -> None:
    try:
        resolve_sandbox_runner(SandboxPolicy.MICROVM, workspace)
    except SandboxUnavailable:
        checks["microvm_fails_closed"] = True


def _run(runner: Any, argv: list[str], *, timeout: float = 60.0) -> SandboxResult:
    return runner.run(SandboxCommand(argv=argv, timeout_seconds=timeout))


def _pull_image(runtime: str, image: str) -> None:
    proc = subprocess.run(  # nosec B603 - fixed argv, pinned public test image
        [runtime, "pull", "--quiet", image],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise SandboxUnavailable(f"cannot pull {image}: {proc.stderr.strip()[:200]}")


def _runtime_version(runtime: str) -> str:
    proc = subprocess.run(  # nosec B603 - fixed argv
        [runtime, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout.strip()


def write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    evidence["generated_at"] = _utc_now()
    assert_no_secrets(evidence)
    serialized = json.dumps(evidence, indent=2, sort_keys=True)
    if any(pattern.search(serialized) for pattern in _FORBIDDEN_EVIDENCE_PATTERNS):
        raise SandboxSmokeError("sanitized sandbox smoke evidence contained a forbidden marker")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized + "\n", encoding="utf-8")
    temporary.replace(path)


def _git_head() -> str:
    result = subprocess.run(  # nosec B603 - fixed argv
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    evidence_out = Path(os.environ.get("LEOS_SANDBOX_SMOKE_EVIDENCE_OUT", DEFAULT_EVIDENCE_OUT))
    evidence = run_smoke()
    write_evidence(evidence_out, evidence)
    print(json.dumps({k: evidence[k] for k in ("status", "runtime", "failure_summary", "checks")}, indent=2))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
