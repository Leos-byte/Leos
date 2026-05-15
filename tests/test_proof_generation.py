from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent import proof
from leos_agent.proof import ProofCommand, ProofGitMetadata, generate_proofs, redact_secrets


class ProofGenerationTests(unittest.TestCase):
    def test_generate_proofs_records_pass_and_fail(self) -> None:
        commands = [
            ProofCommand("unit_tests", ["ok"], "ok"),
            ProofCommand("safety_evals", ["bad"], "bad"),
        ]

        def runner(command: ProofCommand) -> subprocess.CompletedProcess[str]:
            if command.name == "unit_tests":
                return subprocess.CompletedProcess(command.argv, 0, stdout="token=abc", stderr="")
            return subprocess.CompletedProcess(command.argv, 1, stdout="x" * 21000, stderr="failed")

        with tempfile.TemporaryDirectory() as tmp:
            manifest = generate_proofs(Path(tmp), commands=commands, runner=runner)
            manifest_path = Path(tmp) / "MANIFEST.json"
            index_path = Path(tmp) / "PROOF_INDEX.md"

            self.assertTrue(manifest_path.exists())
            self.assertTrue(index_path.exists())
            self.assertEqual(manifest.summary["failed"], 1)
            self.assertIn("TEST_RESULTS.md", index_path.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED]", (Path(tmp) / "TEST_RESULTS.md").read_text(encoding="utf-8"))
            self.assertIn("[truncated]", (Path(tmp) / "SAFETY_EVAL_RESULTS.md").read_text(encoding="utf-8"))

    def test_redact_secrets_handles_common_secret_names(self) -> None:
        self.assertNotIn("abc", redact_secrets("api_key=abc password: abc token=abc"))

    def test_require_clean_dirty_worktree_skips_commands_and_returns_failed_status(self) -> None:
        commands = [ProofCommand("unit_tests", ["should-not-run"], "should-not-run")]

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value=ProofGitMetadata("abc", "main", True),
            ),
        ):
            manifest = generate_proofs(Path(tmp), commands=commands, require_clean=True)
            index = (Path(tmp) / "PROOF_INDEX.md").read_text(encoding="utf-8")

        self.assertEqual(manifest.proof_status, "failed_dirty_worktree")
        self.assertFalse(manifest.release_grade)
        self.assertEqual(manifest.summary["skipped"], 1)
        self.assertIn("not release-grade evidence", index)

    def test_allow_dirty_records_precommit_status(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value=ProofGitMetadata("abc", "main", True),
            ),
        ):
            manifest = generate_proofs(Path(tmp), commands=[], allow_dirty=True)

        self.assertEqual(manifest.proof_status, "precommit_dirty")
        self.assertFalse(manifest.release_grade)

    def test_clean_worktree_records_release_grade(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value=ProofGitMetadata("abc", "main", False),
            ),
        ):
            manifest = generate_proofs(Path(tmp), commands=[])

        self.assertEqual(manifest.proof_status, "release_grade")
        self.assertTrue(manifest.release_grade)

    def test_git_unavailable_records_non_release_grade(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value=ProofGitMetadata(None, None, None),
            ),
        ):
            manifest = generate_proofs(Path(tmp), commands=[])

        self.assertEqual(manifest.proof_status, "git_unavailable")
        self.assertFalse(manifest.release_grade)

    def test_main_returns_two_for_require_clean_dirty_worktree(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "leos_agent.proof._git_metadata",
                return_value=ProofGitMetadata("abc", "main", True),
            ),
        ):
            code = proof.main(["--output", tmp, "--require-clean"])

        self.assertEqual(code, 2)

    def test_execute_records_runner_exceptions(self) -> None:
        command = ProofCommand("missing", ["missing"], "missing")

        def missing_runner(command: ProofCommand) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("missing binary")

        def broken_runner(command: ProofCommand) -> subprocess.CompletedProcess[str]:
            raise RuntimeError("boom")

        self.assertEqual(proof._execute(command, missing_runner).exit_code, 127)
        self.assertEqual(proof._execute(command, broken_runner).exit_code, 1)

    def test_environment_handles_missing_package_metadata(self) -> None:
        with mock.patch("leos_agent.proof.version", side_effect=proof.PackageNotFoundError):
            env = proof._environment()

        self.assertEqual(env.package_version, "unknown")

    def test_git_metadata_handles_missing_git(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            metadata = proof._git_metadata()

        self.assertIsNone(metadata.dirty_worktree)

    def test_command_doc_handles_missing_command(self) -> None:
        manifest = proof.ProofManifest(
            generated_at="now",
            git=ProofGitMetadata("abc", "main", False),
            environment=proof.ProofEnvironment("py", "linux", "0", "."),
        )

        self.assertIn("Command was not run", proof._command_doc("Missing", manifest, "missing"))


if __name__ == "__main__":
    unittest.main()
