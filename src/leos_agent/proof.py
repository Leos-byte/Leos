"""Proof document generation for repository-local safety evidence."""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess  # nosec B404 - proof generation runs explicit argv commands
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

MAX_EXCERPT = 20000
SECRET_PATTERNS = (
    re.compile(r"(?i)(token|api[_-]?key|password|secret)(\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"eval-secret-token"),
)


@dataclass(frozen=True)
class ProofCommand:
    name: str
    argv: list[str]
    display: str


@dataclass(frozen=True)
class ProofCommandResult:
    name: str
    command: str
    exit_code: int
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass(frozen=True)
class ProofEnvironment:
    python_version: str
    platform: str
    package_version: str
    working_directory: str


@dataclass(frozen=True)
class ProofGitMetadata:
    commit_sha: str | None
    branch: str | None
    dirty_worktree: bool | None


@dataclass(frozen=True)
class ProofManifest:
    generated_at: str
    git: ProofGitMetadata
    environment: ProofEnvironment
    proof_status: str = "release_grade"
    release_grade: bool = True
    dirty_worktree: bool | None = False
    warnings: list[str] = field(default_factory=list)
    commands: list[ProofCommandResult] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


Runner = Callable[[ProofCommand], subprocess.CompletedProcess[str]]


def default_proof_commands() -> list[ProofCommand]:
    return [
        ProofCommand(
            "unit_tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            "python -m unittest discover -s tests",
        ),
        ProofCommand("safety_evals", ["leos", "eval", "--suite", "safety"], "leos eval --suite safety"),
        ProofCommand(
            "coverage_run",
            ["coverage", "run", "-m", "unittest", "discover", "-s", "tests"],
            "coverage run -m unittest discover -s tests",
        ),
        ProofCommand("coverage_report", ["coverage", "report"], "coverage report"),
        ProofCommand("ruff_check", ["ruff", "check", "."], "ruff check ."),
        ProofCommand("ruff_format_check", ["ruff", "format", "--check", "."], "ruff format --check ."),
        ProofCommand("mypy", ["mypy", "src"], "mypy src"),
        ProofCommand("bandit", ["bandit", "-r", "src"], "bandit -r src"),
        ProofCommand("leos_help", ["leos", "--help"], "leos --help"),
        ProofCommand("leos_eval_help", ["leos", "eval", "--help"], "leos eval --help"),
        ProofCommand("leos_trace_help", ["leos", "trace", "--help"], "leos trace --help"),
    ]


def generate_proofs(
    output_dir: Path,
    *,
    commands: Sequence[ProofCommand] | None = None,
    runner: Runner | None = None,
    require_clean: bool = False,
    allow_dirty: bool = False,
) -> ProofManifest:
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = list(default_proof_commands() if commands is None else commands)
    run = runner or _run_command
    git = _git_metadata()
    proof_status, release_grade, warnings = _proof_status(git, require_clean=require_clean)
    if proof_status == "failed_dirty_worktree":
        results = [_skipped(command, "require-clean refused dirty worktree") for command in selected]
    else:
        results = [_execute(command, run) for command in selected]
    manifest = ProofManifest(
        generated_at=_now(),
        git=git,
        environment=_environment(),
        proof_status=proof_status,
        release_grade=release_grade,
        dirty_worktree=git.dirty_worktree,
        warnings=warnings,
        commands=results,
        summary=_summary(results),
    )
    _write_json(output_dir / "MANIFEST.json", _manifest_dict(manifest))
    _write_markdown_files(output_dir, manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Leos proof documents.")
    parser.add_argument("--output", default="docs/proofs", help="Output directory for proof documents.")
    parser.add_argument("--require-clean", action="store_true", help="Fail if the Git worktree is dirty.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow dirty worktree proofs for local review.")
    args = parser.parse_args(argv)
    manifest = generate_proofs(Path(args.output), require_clean=args.require_clean, allow_dirty=args.allow_dirty)
    print(f"proof_status={manifest.proof_status} release_grade={manifest.release_grade}")
    if manifest.proof_status == "failed_dirty_worktree":
        return 2
    return 0 if manifest.summary.get("failed", 0) == 0 else 1


def _execute(command: ProofCommand, runner: Runner) -> ProofCommandResult:
    started = _now()
    start = time.monotonic()
    try:
        completed = runner(command)
        exit_code = int(completed.returncode)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except FileNotFoundError as exc:
        exit_code = 127
        stdout = ""
        stderr = str(exc)
    except Exception as exc:  # noqa: BLE001 - proof records command failures
        exit_code = 1
        stdout = ""
        stderr = str(exc)
    finished = _now()
    duration = time.monotonic() - start
    redacted_stdout = redact_secrets(stdout)
    redacted_stderr = redact_secrets(stderr)
    stdout_excerpt, stdout_truncated = _excerpt(redacted_stdout)
    stderr_excerpt, stderr_truncated = _excerpt(redacted_stderr)
    return ProofCommandResult(
        name=command.name,
        command=command.display,
        exit_code=exit_code,
        status="passed" if exit_code == 0 else "failed",
        started_at=started,
        finished_at=finished,
        duration_seconds=duration,
        stdout=stdout_excerpt,
        stderr=stderr_excerpt,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _skipped(command: ProofCommand, reason: str) -> ProofCommandResult:
    now = _now()
    return ProofCommandResult(
        name=command.name,
        command=command.display,
        exit_code=0,
        status="skipped",
        started_at=now,
        finished_at=now,
        duration_seconds=0.0,
        stdout="",
        stderr=reason,
    )


def _run_command(command: ProofCommand) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command.argv, capture_output=True, text=True, timeout=300)  # nosec B603 - explicit argv


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if pattern.pattern == "eval-secret-token":
            redacted = pattern.sub("[REDACTED]", redacted)
        else:
            redacted = pattern.sub(r"\1\2[REDACTED]", redacted)
    return redacted


def _excerpt(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_EXCERPT:
        return text, False
    return text[:MAX_EXCERPT] + "\n[truncated]", True


def _summary(results: Sequence[ProofCommandResult]) -> dict[str, int]:
    passed = sum(1 for result in results if result.status == "passed")
    failed = sum(1 for result in results if result.status == "failed")
    skipped = sum(1 for result in results if result.status == "skipped")
    return {"total": len(results), "passed": passed, "failed": failed, "skipped": skipped}


def _proof_status(git: ProofGitMetadata, *, require_clean: bool) -> tuple[str, bool, list[str]]:
    if git.dirty_worktree is None:
        return "git_unavailable", False, ["Git metadata unavailable; proof cannot be release-grade."]
    if git.dirty_worktree:
        warning = "This proof was generated from a dirty worktree and is not release-grade evidence."
        if require_clean:
            return "failed_dirty_worktree", False, [warning, "--require-clean refused dirty worktree."]
        return "precommit_dirty", False, [warning]
    return "release_grade", True, []


def _environment() -> ProofEnvironment:
    try:
        package_version = version("leos-agent")
    except PackageNotFoundError:
        package_version = "unknown"
    return ProofEnvironment(
        python_version=sys.version.replace("\n", " "),
        platform=platform.platform(),
        package_version=package_version,
        working_directory=str(Path.cwd()),
    )


def _git_metadata() -> ProofGitMetadata:
    def git(args: list[str]) -> str | None:
        git_bin = shutil.which("git")
        if not git_bin:
            return None
        try:
            proc = subprocess.run([git_bin, *args], capture_output=True, text=True, timeout=5)  # nosec B603
        except Exception:  # noqa: BLE001
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    status = git(["status", "--short"])
    return ProofGitMetadata(
        commit_sha=git(["rev-parse", "HEAD"]),
        branch=git(["branch", "--show-current"]),
        dirty_worktree=None if status is None else bool(status),
    )


def _manifest_dict(manifest: ProofManifest) -> dict[str, object]:
    return {
        "generated_at": manifest.generated_at,
        "proof_status": manifest.proof_status,
        "release_grade": manifest.release_grade,
        "dirty_worktree": manifest.dirty_worktree,
        "warnings": manifest.warnings,
        "git": asdict(manifest.git),
        "environment": asdict(manifest.environment),
        "commands": [
            {
                "name": result.name,
                "command": result.command,
                "exit_code": result.exit_code,
                "status": result.status,
                "duration_seconds": result.duration_seconds,
                "stdout_path": None,
                "stderr_path": None,
            }
            for result in manifest.commands
        ],
        "summary": manifest.summary,
    }


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _write_markdown_files(output_dir: Path, manifest: ProofManifest) -> None:
    _write(output_dir / "README.md", "# Leos Proof Documents\n\nGenerated proof artifacts for local safety review.\n")
    _write(output_dir / "PROOF_INDEX.md", _proof_index(manifest))
    _write(output_dir / "TEST_RESULTS.md", _command_doc("Unit Tests", manifest, "unit_tests"))
    _write(output_dir / "SAFETY_EVAL_RESULTS.md", _command_doc("Safety Eval Results", manifest, "safety_evals"))
    _write(output_dir / "COVERAGE_SUMMARY.md", _coverage_doc(manifest))
    _write(
        output_dir / "STATIC_ANALYSIS.md",
        _multi_command_doc("Static Analysis", manifest, ("ruff_check", "ruff_format_check", "mypy")),
    )
    _write(output_dir / "SECURITY_SCAN.md", _multi_command_doc("Security Scan", manifest, ("bandit",)))
    _write(output_dir / "ARCHITECTURE_CLAIMS.md", _architecture_claims())
    _write(output_dir / "PRODUCTION_READINESS.md", _production_readiness())
    _write(output_dir / "KNOWN_LIMITATIONS.md", _known_limitations())


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _proof_index(manifest: ProofManifest) -> str:
    links = [
        "TEST_RESULTS.md",
        "SAFETY_EVAL_RESULTS.md",
        "COVERAGE_SUMMARY.md",
        "STATIC_ANALYSIS.md",
        "SECURITY_SCAN.md",
        "ARCHITECTURE_CLAIMS.md",
        "PRODUCTION_READINESS.md",
        "KNOWN_LIMITATIONS.md",
    ]
    warnings = "\n".join(f"- WARNING: {warning}" for warning in manifest.warnings) or "- none"
    if manifest.dirty_worktree:
        warnings += (
            "\n\n**WARNING: This proof was generated from a dirty worktree. "
            "It is useful for local review but not release-grade evidence.**"
        )
    return (
        "# Proof Index\n\n"
        f"Proof status: `{manifest.proof_status}`\n\n"
        f"Release grade: `{manifest.release_grade}`\n\n"
        f"Generated: {manifest.generated_at}\n\n"
        f"Commit: {manifest.git.commit_sha}\n\n"
        f"Branch: {manifest.git.branch}\n\n"
        f"Dirty worktree: {manifest.git.dirty_worktree}\n\n"
        f"Summary: {manifest.summary}\n\n"
        f"## Warnings\n\n{warnings}\n\n"
        "## Documents\n\n" + "\n".join(f"- [{link}]({link})" for link in links) + "\n"
    )


def _command_doc(title: str, manifest: ProofManifest, name: str) -> str:
    result = next((item for item in manifest.commands if item.name == name), None)
    if result is None:
        return f"# {title}\n\nCommand was not run.\n"
    return _render_command(title, result)


def _multi_command_doc(title: str, manifest: ProofManifest, names: Sequence[str]) -> str:
    parts = [f"# {title}", ""]
    for name in names:
        result = next((item for item in manifest.commands if item.name == name), None)
        if result:
            parts.append(_render_command(result.name, result))
    return "\n".join(parts)


def _coverage_doc(manifest: ProofManifest) -> str:
    return _multi_command_doc("Coverage Summary", manifest, ("coverage_run", "coverage_report"))


def _render_command(title: str, result: ProofCommandResult) -> str:
    return (
        f"## {title}\n\n"
        f"- Command: `{result.command}`\n"
        f"- Exit code: `{result.exit_code}`\n"
        f"- Status: `{result.status}`\n"
        f"- Duration seconds: `{result.duration_seconds:.3f}`\n"
        f"- Started: `{result.started_at}`\n"
        f"- Finished: `{result.finished_at}`\n\n"
        "### stdout\n\n"
        f"```text\n{result.stdout}\n```\n\n"
        "### stderr\n\n"
        f"```text\n{result.stderr}\n```\n"
    )


def _architecture_claims() -> str:
    return """# Architecture Claims

| Claim | Code path | Test path | Proof status |
|---|---|---|---|
| Workspace path escape is blocked | `src/leos_agent/dev_tools.py` | `tests/test_dev_tools.py` | covered by tests |
| High risk actions require approval | `src/leos_agent/policy.py` | `tests/evals` / safety eval | covered by eval |
| Secrets are not leaked to untrusted tools | `src/leos_agent/tools.py` | safety eval | covered by eval |
| Network observations are untrusted | `src/leos_agent/network_tools.py` | network tests | covered by tests |
| Audit logs have hash-chain integrity | `src/leos_agent/audit.py` | `tests/test_replay.py` | covered by tests |
"""


def _production_readiness() -> str:
    return """# Production Readiness

Leos is currently suitable for local development and safety evaluation.

Not production-ready without:
- hardened container or microVM execution
- deployment-level egress proxy for network tools
- operational secret management
- stronger persistence and concurrency testing for long-running workloads
- expanded safety eval coverage
"""


def _known_limitations() -> str:
    return """# Known Limitations

- Workspace subprocess sandbox is not a production isolation boundary.
- Docker sandbox support is initial and command-construction focused.
- Causal model is not a full structural causal model.
- LLM planner quality depends on the configured model.
- Network fetch requires deployment egress controls before production use.
- TaskQueue has SQLite persistence; AuditLog and MemoryStore SQLite backends remain future work.
- Safety eval suite is a minimum regression suite, not a formal proof.
"""
