from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent import proof
from leos_agent.proof import exit_code_for_manifest, generate_proofs, redact_secrets


class ProofGenerationTests(unittest.TestCase):
    def test_allow_dirty_generates_precommit_manifest_and_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "proofs"
            with mock.patch(
                "leos_agent.proof._git_metadata",
                return_value={"available": True, "branch": "main", "commit_sha": "abc", "dirty_worktree": True},
            ):
                manifest = generate_proofs(out, allow_dirty=True, no_run=True, repo_root=Path.cwd())

            data = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
            index = (out / "PROOF_INDEX.md").read_text(encoding="utf-8")

        self.assertEqual(manifest.proof_status, "precommit_dirty")
        self.assertFalse(manifest.release_grade)
        self.assertEqual(data["proof_status"], "precommit_dirty")
        self.assertIn("SOURCE_SNAPSHOT.md", index)
        self.assertIn("TEST_INVENTORY.md", index)
        self.assertIn("dirty worktree", index)

    def test_require_clean_dirty_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value={"available": True, "branch": "main", "commit_sha": "abc", "dirty_worktree": True},
            ),
        ):
            manifest = generate_proofs(Path(tmp), require_clean=True, no_run=True, repo_root=Path.cwd())

        self.assertEqual(manifest.proof_status, "failed_dirty_worktree")
        self.assertEqual(exit_code_for_manifest(manifest), 2)

    def test_require_clean_dirty_skips_commands_without_running(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value={"available": True, "branch": "main", "commit_sha": "abc", "dirty_worktree": True},
            ),
            mock.patch("leos_agent.proof.subprocess.run") as run,
        ):
            manifest = generate_proofs(Path(tmp), require_clean=True, repo_root=Path.cwd())

        self.assertFalse(run.called)
        self.assertTrue(manifest.commands)
        self.assertTrue(all(command.status == "skipped" for command in manifest.commands))

    def test_clean_worktree_is_release_grade(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value={"available": True, "branch": "main", "commit_sha": "abc", "dirty_worktree": False},
            ),
        ):
            manifest = generate_proofs(Path(tmp), require_clean=True, no_run=True, repo_root=Path.cwd())

        self.assertEqual(manifest.proof_status, "release_grade")
        self.assertTrue(manifest.release_grade)
        self.assertEqual(manifest.package_version, "0.1.0b1")

    def test_git_unavailable_does_not_crash(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value={"available": False, "branch": None, "commit_sha": None, "dirty_worktree": None},
            ),
        ):
            manifest = generate_proofs(Path(tmp), no_run=True, repo_root=Path.cwd())

        self.assertEqual(manifest.proof_status, "git_unavailable")

    def test_source_snapshot_and_test_inventory_include_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = generate_proofs(Path(tmp), no_run=True, repo_root=Path.cwd())

        sources = {snapshot.path: snapshot for snapshot in manifest.source_snapshot}
        tests = {snapshot.path: snapshot for snapshot in manifest.test_inventory}
        self.assertIn("src/leos_agent/proof.py", sources)
        self.assertIn("tests/test_proof_generation.py", tests)
        self.assertRegex(sources["src/leos_agent/proof.py"].sha256 or "", r"^[0-9a-f]{64}$")

    def test_missing_snapshot_file_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = proof._snapshot_files(root, ["missing.py"])[0]

        self.assertFalse(missing.exists)
        self.assertIsNone(missing.sha256)

    def test_failed_command_recorded_as_failed(self) -> None:
        result = proof._run_command("false", ["python", "-c", "import sys; sys.exit(7)"], Path.cwd())

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(
            exit_code_for_manifest(proof.ProofManifest("now", "release_grade", True, False, [], {}, {}, [result])), 1
        )

    def test_missing_command_is_recorded_as_skipped(self) -> None:
        with mock.patch("leos_agent.proof.subprocess.run", side_effect=FileNotFoundError("missing binary")):
            result = proof._run_command("missing", ["missing"], Path.cwd())

        self.assertEqual(result.status, "skipped")
        self.assertIn("missing binary", result.reason or "")

    def test_timed_out_command_is_recorded_as_failed(self) -> None:
        timeout = subprocess.TimeoutExpired(["slow"], timeout=1, output=b"token=abc", stderr=b"slow")

        with mock.patch("leos_agent.proof.subprocess.run", side_effect=timeout):
            result = proof._run_command("slow", ["slow"], Path.cwd())

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "Command timed out")
        self.assertNotIn("abc", result.stdout)

    def test_output_is_truncated(self) -> None:
        text, truncated = proof._excerpt("x" * (proof.MAX_EXCERPT + 10))

        self.assertTrue(truncated)
        self.assertEqual(len(text), proof.MAX_EXCERPT)

    def test_secret_like_strings_are_redacted(self) -> None:
        redacted = redact_secrets("token=abc api_key: def password secret")

        self.assertNotRegex(redacted, re.compile("abc|def"))
        self.assertIn("<redacted>", redacted)

    def test_test_count_is_parsed_from_successful_unittest_output(self) -> None:
        result = proof.CommandProof(
            name="unit_tests",
            command=["python", "-m", "unittest"],
            exit_code=0,
            status="passed",
            started_at="now",
            finished_at="now",
            duration_seconds=1.0,
            stderr="Ran 819 tests in 2.1s\n\nOK\n",
        )

        self.assertEqual(proof._test_count([result]), 819)

    def test_test_count_is_missing_for_failed_or_unparseable_tests(self) -> None:
        failed = proof.CommandProof(
            name="unit_tests",
            command=["python", "-m", "unittest"],
            exit_code=1,
            status="failed",
            started_at="now",
            finished_at="now",
            duration_seconds=1.0,
            stderr="Ran 819 tests in 2.1s\n\nFAILED\n",
        )
        passed_without_count = proof.CommandProof(
            name="unit_tests",
            command=["python", "-m", "unittest"],
            exit_code=0,
            status="passed",
            started_at="now",
            finished_at="now",
            duration_seconds=1.0,
        )

        self.assertIsNone(proof._test_count([failed]))
        self.assertIsNone(proof._test_count([passed_without_count]))

    def test_package_version_rejects_stale_installed_metadata(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("leos_agent.proof.importlib.metadata.version", return_value="0.1.0"),
        ):
            root = Path(tmp)
            (root / "pyproject.toml").write_text('[project]\nversion = "0.1.0b1"\n', encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "does not match pyproject"):
                proof._package_version(root)

    def test_readme_does_not_hardcode_a_stale_unit_test_count(self) -> None:
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertNotRegex(readme, re.compile(r"\b\d+\s+unit tests\b", re.IGNORECASE))
        self.assertIn("docs/proofs/MANIFEST.json", readme)


if __name__ == "__main__":
    unittest.main()
