#!/usr/bin/env python
"""Check that release proof metadata matches the current release evidence flow."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    root = Path.cwd()
    manifest_path = root / "docs" / "proofs" / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"release proof check failed: could not read MANIFEST.json: {exc}", file=sys.stderr)
        return 1
    if not isinstance(manifest, dict):
        print("release proof check failed: MANIFEST.json must contain an object", file=sys.stderr)
        return 1

    failures = _proof_failures(manifest, root)
    if failures:
        for failure in failures:
            print(f"release proof check failed: {failure}", file=sys.stderr)
        return 1
    print("release proof check passed")
    return 0


def _proof_failures(manifest: dict[str, Any], root: Path) -> list[str]:
    failures: list[str] = []
    if manifest.get("proof_status") != "release_grade":
        failures.append("proof_status is not release_grade")
    if manifest.get("release_grade") is not True:
        failures.append("release_grade is not true")
    if manifest.get("dirty_worktree") is not False:
        failures.append("dirty_worktree is not false")
    expected_version = _pyproject_version(root)
    if expected_version is None:
        failures.append("project.version could not be read from pyproject.toml")
    elif manifest.get("package_version") != expected_version:
        failures.append("package_version does not match pyproject.toml")
    environment = manifest.get("environment")
    if not isinstance(environment, dict) or environment.get("package_version") != expected_version:
        failures.append("environment.package_version does not match pyproject.toml")
    test_count = manifest.get("test_count")
    if not isinstance(test_count, int) or isinstance(test_count, bool) or test_count < 1:
        failures.append("test_count must be a positive integer")
    git = manifest.get("git")
    if not isinstance(git, dict):
        failures.append("git metadata missing")
        return failures
    if git.get("dirty_worktree") is not False:
        failures.append("git.dirty_worktree is not false")
    if not git.get("branch"):
        failures.append("git branch is missing")
    current = _git(root, "rev-parse", "HEAD")
    if current is None:
        failures.append("current git commit unavailable")
    elif not _commit_matches_release_flow(root, str(git.get("commit_sha", "")), current):
        failures.append("git.commit_sha does not match current HEAD, proof-refresh parent, or proof-refresh merge flow")
    return failures


def _commit_matches_release_flow(root: Path, manifest_commit: str, current: str) -> bool:
    if manifest_commit == current:
        return True
    if _commit_matches_direct_proof_refresh(root, manifest_commit, current):
        return True
    return _commit_matches_merge_proof_refresh(root, manifest_commit, current)


def _commit_matches_direct_proof_refresh(root: Path, manifest_commit: str, current: str) -> bool:
    parent = _git(root, "rev-parse", f"{current}^")
    if manifest_commit != parent:
        return False
    return _only_docs_proofs_changed(root, parent, current)


def _commit_matches_merge_proof_refresh(root: Path, manifest_commit: str, current: str) -> bool:
    if _git_returncode(root, "merge-base", "--is-ancestor", manifest_commit, current) != 0:
        return False
    return _only_docs_proofs_changed(root, manifest_commit, current)


def _only_docs_proofs_changed(root: Path, base: str, head: str) -> bool:
    changed = _git(root, "diff", "--name-only", base, head)
    if changed is None:
        return False
    paths = [line.strip() for line in changed.splitlines() if line.strip()]
    return bool(paths) and all(path.startswith("docs/proofs/") for path in paths)


def _git(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(  # nosec B603,B607
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
    except Exception:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _git_returncode(root: Path, *args: str) -> int | None:
    try:
        proc = subprocess.run(  # nosec B603,B607
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
    except Exception:
        return None
    return proc.returncode


def _pyproject_version(root: Path) -> str | None:
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    project = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
    version = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project.group(1) if project else "")
    return version.group(1) if version else None


if __name__ == "__main__":
    raise SystemExit(main())
