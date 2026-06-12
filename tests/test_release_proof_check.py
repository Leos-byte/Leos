from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT = Path("scripts/check_release_proof.py").resolve()
_SPEC = importlib.util.spec_from_file_location("check_release_proof", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
check_release_proof = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_release_proof)


class ReleaseProofCheckTests(unittest.TestCase):
    def test_matching_release_grade_manifest_passes(self) -> None:
        manifest = _manifest()
        with mock.patch.object(check_release_proof, "_git", return_value="abc"):
            self.assertEqual(check_release_proof._proof_failures(manifest, Path.cwd()), [])

    def test_proof_refresh_commit_parent_passes_when_only_proofs_changed(self) -> None:
        manifest = _manifest()

        def fake_git(_root: Path, *args: str) -> str:
            if args == ("rev-parse", "HEAD"):
                return "proof-refresh"
            if args == ("rev-parse", "proof-refresh^"):
                return "abc"
            if args == ("diff", "--name-only", "abc", "proof-refresh"):
                return "docs/proofs/MANIFEST.json\ndocs/proofs/PROOF_INDEX.md"
            raise AssertionError(args)

        with mock.patch.object(check_release_proof, "_git", side_effect=fake_git):
            self.assertEqual(check_release_proof._proof_failures(manifest, Path.cwd()), [])

    def test_merge_commit_passes_when_manifest_ancestor_and_only_proofs_changed(self) -> None:
        manifest = _manifest()

        def fake_git(_root: Path, *args: str) -> str:
            if args == ("rev-parse", "HEAD"):
                return "merge-commit"
            if args == ("rev-parse", "merge-commit^"):
                return "main-parent"
            if args == ("diff", "--name-only", "abc", "merge-commit"):
                return "docs/proofs/MANIFEST.json\ndocs/proofs/TEST_RESULTS.md"
            raise AssertionError(args)

        def fake_returncode(_root: Path, *args: str) -> int:
            if args == ("merge-base", "--is-ancestor", "abc", "merge-commit"):
                return 0
            raise AssertionError(args)

        with (
            mock.patch.object(check_release_proof, "_git", side_effect=fake_git),
            mock.patch.object(check_release_proof, "_git_returncode", side_effect=fake_returncode),
        ):
            self.assertEqual(check_release_proof._proof_failures(manifest, Path.cwd()), [])

    def test_precommit_dirty_fails(self) -> None:
        manifest = _manifest(proof_status="precommit_dirty")
        with mock.patch.object(check_release_proof, "_git", return_value="abc"):
            failures = check_release_proof._proof_failures(manifest, Path.cwd())
        self.assertTrue(any("proof_status" in failure for failure in failures))

    def test_release_grade_false_and_dirty_fail(self) -> None:
        manifest = _manifest(release_grade=False, dirty_worktree=True)
        manifest["git"]["dirty_worktree"] = True
        with mock.patch.object(check_release_proof, "_git", return_value="abc"):
            failures = check_release_proof._proof_failures(manifest, Path.cwd())
        self.assertTrue(any("release_grade" in failure for failure in failures))
        self.assertTrue(any("dirty_worktree" in failure for failure in failures))

    def test_commit_mismatch_fails(self) -> None:
        manifest = _manifest()
        with mock.patch.object(check_release_proof, "_git", return_value="different"):
            failures = check_release_proof._proof_failures(manifest, Path.cwd())
        self.assertTrue(any("commit_sha" in failure for failure in failures))

    def test_proof_refresh_parent_fails_when_non_proof_files_changed(self) -> None:
        manifest = _manifest()

        def fake_git(_root: Path, *args: str) -> str:
            if args == ("rev-parse", "HEAD"):
                return "proof-refresh"
            if args == ("rev-parse", "proof-refresh^"):
                return "abc"
            if args == ("diff", "--name-only", "abc", "proof-refresh"):
                return "docs/proofs/MANIFEST.json\nsrc/leos_agent/core.py"
            raise AssertionError(args)

        with mock.patch.object(check_release_proof, "_git", side_effect=fake_git):
            failures = check_release_proof._proof_failures(manifest, Path.cwd())
        self.assertTrue(any("commit_sha" in failure for failure in failures))

    def test_missing_git_metadata_fails_cleanly(self) -> None:
        manifest = _manifest()
        manifest["git"] = None
        failures = check_release_proof._proof_failures(manifest, Path.cwd())
        self.assertIn("git metadata missing", failures)

    def test_package_version_mismatch_fails(self) -> None:
        manifest = _manifest(package_version="0.1.0")

        with mock.patch.object(check_release_proof, "_git", return_value="abc"):
            failures = check_release_proof._proof_failures(manifest, Path.cwd())

        self.assertTrue(any("package_version" in failure for failure in failures))

    def test_environment_package_version_mismatch_fails(self) -> None:
        manifest = _manifest()
        manifest["environment"]["package_version"] = "0.1.0"

        with mock.patch.object(check_release_proof, "_git", return_value="abc"):
            failures = check_release_proof._proof_failures(manifest, Path.cwd())

        self.assertTrue(any("environment.package_version" in failure for failure in failures))

    def test_missing_or_invalid_test_count_fails(self) -> None:
        for count in (None, 0, -1, True, "819"):
            with self.subTest(count=count):
                manifest = _manifest(test_count=count)
                with mock.patch.object(check_release_proof, "_git", return_value="abc"):
                    failures = check_release_proof._proof_failures(manifest, Path.cwd())
                self.assertTrue(any("test_count" in failure for failure in failures))

    def test_malformed_pyproject_fails_cleanly(self) -> None:
        manifest = _manifest()
        with (
            mock.patch.object(check_release_proof, "_pyproject_version", return_value=None),
            mock.patch.object(check_release_proof, "_git", return_value="abc"),
        ):
            failures = check_release_proof._proof_failures(manifest, Path.cwd())

        self.assertTrue(any("project.version" in failure for failure in failures))


def _manifest(**overrides):
    data = {
        "proof_status": "release_grade",
        "release_grade": True,
        "dirty_worktree": False,
        "package_version": "0.1.0b1",
        "test_count": 819,
        "environment": {"package_version": "0.1.0b1"},
        "git": {"branch": "main", "commit_sha": "abc", "dirty_worktree": False},
    }
    data.update(overrides)
    return data


if __name__ == "__main__":
    unittest.main()
