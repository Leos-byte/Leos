#!/usr/bin/env python3
"""Download exact-HEAD production smoke evidence from a successful workflow run."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class SmokeEvidenceDownloadError(RuntimeError):
    """Exact-HEAD smoke evidence was unavailable or inconsistent."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workflow", default="GitHub Real Write Smoke")
    parser.add_argument("--artifact-prefix", default="production-smoke-evidence")
    parser.add_argument("--filename", default="real_github_smoke_latest.json")
    parser.add_argument(
        "--event",
        default="workflow_dispatch",
        help="Workflow event that produced the evidence (e.g. workflow_dispatch, push).",
    )
    args = parser.parse_args()
    try:
        download_exact_head_evidence(
            repo=args.repo,
            sha=args.sha,
            output=Path(args.output),
            workflow=args.workflow,
            artifact_prefix=args.artifact_prefix,
            filename=args.filename,
            event=args.event,
        )
    except SmokeEvidenceDownloadError as exc:
        print(f"smoke evidence download failed: {exc}", file=sys.stderr)
        return 1
    print(f"smoke evidence downloaded for commit {args.sha}")
    return 0


def download_exact_head_evidence(
    *,
    repo: str,
    sha: str,
    output: Path,
    workflow: str = "GitHub Real Write Smoke",
    artifact_prefix: str = "production-smoke-evidence",
    filename: str = "real_github_smoke_latest.json",
    event: str = "workflow_dispatch",
) -> None:
    runs = _gh_json(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            workflow,
            "--event",
            event,
            "--commit",
            sha,
            "--status",
            "success",
            "--limit",
            "20",
            "--json",
            "databaseId,headSha,conclusion,event",
        ]
    )
    if not isinstance(runs, list):
        raise SmokeEvidenceDownloadError("GitHub run query did not return a list")
    matching = [
        run
        for run in runs
        if isinstance(run, dict)
        and run.get("headSha") == sha
        and run.get("conclusion") == "success"
        and run.get("event") == event
    ]
    if not matching:
        raise SmokeEvidenceDownloadError(f"no successful {event} smoke run exists for current HEAD")
    run_id = str(matching[0].get("databaseId", ""))
    if not run_id:
        raise SmokeEvidenceDownloadError("matching smoke run did not include a run id")
    artifact_name = f"{artifact_prefix}-{sha}"
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        _gh(
            [
                "gh",
                "run",
                "download",
                run_id,
                "--repo",
                repo,
                "--name",
                artifact_name,
                "--dir",
                str(directory),
            ]
        )
        candidates = list(directory.rglob(filename))
        if len(candidates) != 1:
            raise SmokeEvidenceDownloadError("smoke artifact did not contain exactly one evidence JSON file")
        evidence = _load_evidence(candidates[0])
        if evidence.get("leos_commit_sha") != sha:
            raise SmokeEvidenceDownloadError("smoke evidence commit does not match current HEAD")
        if str(evidence.get("workflow_run_id", "")) != run_id:
            raise SmokeEvidenceDownloadError("smoke evidence run id does not match the downloaded workflow run")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidates[0], output)


def _load_evidence(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeEvidenceDownloadError("smoke evidence JSON could not be read") from exc
    if not isinstance(value, dict):
        raise SmokeEvidenceDownloadError("smoke evidence must be a JSON object")
    return value


def _gh_json(command: list[str]) -> Any:
    result = _gh(command)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeEvidenceDownloadError("GitHub CLI returned invalid JSON") from exc


def _gh(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
    )
    if result.returncode != 0:
        raise SmokeEvidenceDownloadError("GitHub CLI request failed")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
