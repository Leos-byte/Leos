from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.download_smoke_evidence import SmokeEvidenceDownloadError, download_exact_head_evidence


class DownloadSmokeEvidenceTests(unittest.TestCase):
    def test_downloads_exact_head_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evidence.json"
            with self._patched_download("abc123", "42"):
                download_exact_head_evidence(repo="o/r", sha="abc123", output=output)

            evidence = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(evidence["leos_commit_sha"], "abc123")
        self.assertEqual(evidence["workflow_run_id"], "42")

    def test_missing_successful_run_fails(self) -> None:
        with (
            mock.patch("scripts.download_smoke_evidence._gh_json", return_value=[]),
            self.assertRaisesRegex(SmokeEvidenceDownloadError, "no successful"),
        ):
            download_exact_head_evidence(repo="o/r", sha="abc123", output=Path("unused.json"))

    def test_evidence_commit_mismatch_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            self._patched_download("different", "42", run_sha="abc123"),
            self.assertRaisesRegex(SmokeEvidenceDownloadError, "commit does not match"),
        ):
            download_exact_head_evidence(
                repo="o/r",
                sha="abc123",
                output=Path(tmp) / "evidence.json",
            )

    def test_evidence_run_id_mismatch_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            self._patched_download("abc123", "99", run_id="42"),
            self.assertRaisesRegex(SmokeEvidenceDownloadError, "run id does not match"),
        ):
            download_exact_head_evidence(
                repo="o/r",
                sha="abc123",
                output=Path(tmp) / "evidence.json",
            )

    def test_artifact_without_single_evidence_file_fails(self) -> None:
        run = {
            "databaseId": 42,
            "headSha": "abc123",
            "conclusion": "success",
            "event": "workflow_dispatch",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("scripts.download_smoke_evidence._gh_json", return_value=[run]),
            mock.patch(
                "scripts.download_smoke_evidence._gh",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            self.assertRaisesRegex(SmokeEvidenceDownloadError, "exactly one"),
        ):
            download_exact_head_evidence(
                repo="o/r",
                sha="abc123",
                output=Path(tmp) / "evidence.json",
            )

    def _patched_download(
        self,
        evidence_sha: str,
        evidence_run_id: str,
        *,
        run_sha: str | None = None,
        run_id: str = "42",
    ):
        run = {
            "databaseId": int(run_id),
            "headSha": run_sha or evidence_sha,
            "conclusion": "success",
            "event": "workflow_dispatch",
        }

        def fake_download(command: list[str]) -> subprocess.CompletedProcess[str]:
            directory = Path(command[command.index("--dir") + 1])
            evidence = {
                "leos_commit_sha": evidence_sha,
                "workflow_run_id": evidence_run_id,
            }
            (directory / "real_github_smoke_latest.json").write_text(
                json.dumps(evidence),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        return _Patches(
            mock.patch("scripts.download_smoke_evidence._gh_json", return_value=[run]),
            mock.patch("scripts.download_smoke_evidence._gh", side_effect=fake_download),
        )


class _Patches:
    def __init__(self, *patches) -> None:
        self.patches = patches

    def __enter__(self):
        for patcher in self.patches:
            patcher.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()


if __name__ == "__main__":
    unittest.main()
