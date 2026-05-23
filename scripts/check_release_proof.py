#!/usr/bin/env python
"""Check that release proof metadata matches the current clean commit."""

from __future__ import annotations

import json
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
    elif git.get("commit_sha") != current:
        failures.append("git.commit_sha does not match current HEAD")
    return failures


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


if __name__ == "__main__":
    raise SystemExit(main())
