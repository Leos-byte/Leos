"""Tests for the sandbox isolation smoke evidence pipeline."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent.sandbox import SandboxResult
from leos_agent.sanitization import SanitizationError
from scripts.check_production_readiness import _sandbox_evidence_check
from scripts.sandbox_smoke import (
    SandboxSmokeError,
    build_evidence,
    initial_checks,
    run_smoke,
    write_evidence,
)


class _FakeIsolatedRunner:
    """Canned responses matching a fully hardened container runtime."""

    pids_limit = 128
    memory_limit = "512m"

    def run(self, command):  # noqa: ANN001, ANN201 - SandboxRunner protocol
        argv = command.argv
        if argv == ["echo", "sandbox-smoke-sanity"]:
            return SandboxResult(True, 0, "sandbox-smoke-sanity\n", "")
        if argv == ["id", "-u"]:
            return SandboxResult(True, 0, "65532\n", "")
        if argv[0] == "wget":
            return SandboxResult(False, 1, "", "download failed")
        if argv == ["cat", "/sys/fs/cgroup/pids.max"]:
            return SandboxResult(True, 0, "128\n", "")
        if argv == ["cat", "/sys/fs/cgroup/memory.max"]:
            return SandboxResult(True, 0, "536870912\n", "")
        if argv[0] == "sleep":
            return SandboxResult(False, None, "", "", timed_out=True)
        script = argv[2] if argv[:2] == ["sh", "-c"] else ""
        if "/etc/probe" in script:
            return SandboxResult(False, 1, "", "read-only file system")
        if "/tmp/probe" in script:
            return SandboxResult(True, 0, "probe\n", "")
        if "seq 1 200" in script:
            return SandboxResult(False, 2, "", "can't fork")
        if "head -c 100000000" in script:
            return SandboxResult(False, 137, "", "killed")
        raise AssertionError(f"unexpected probe argv: {argv}")


class RunSmokeTests(unittest.TestCase):
    def test_all_checks_pass_with_isolated_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = run_smoke(_FakeIsolatedRunner(), workspace=Path(tmp))
        self.assertEqual(evidence["status"], "passed", evidence)
        self.assertTrue(all(value is True for value in evidence["checks"].values()), evidence["checks"])

    def test_weak_runner_fails_with_named_checks(self) -> None:
        class _LeakyRunner(_FakeIsolatedRunner):
            def run(self, command):  # noqa: ANN001, ANN201
                if command.argv == ["id", "-u"]:
                    return SandboxResult(True, 0, "0\n", "")
                return super().run(command)

        with tempfile.TemporaryDirectory() as tmp:
            evidence = run_smoke(_LeakyRunner(), workspace=Path(tmp))
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failure_type"], "isolation_check_failed")
        self.assertIn("non_root_user_enforced", str(evidence["failure_summary"]))

    def test_missing_runtime_is_reported_not_raised(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("scripts.sandbox_smoke.shutil.which", return_value=None),
        ):
            evidence = run_smoke(workspace=Path(tmp))
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failure_type"], "runtime_unavailable")


class WriteEvidenceTests(unittest.TestCase):
    def test_atomic_write_produces_clean_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            evidence = build_evidence()
            write_evidence(path, evidence)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["evidence_type"], "container_sandbox_isolation_smoke")
            self.assertTrue(loaded["generated_at"].endswith("Z"))
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_forbidden_marker_is_rejected(self) -> None:
        # assert_no_secrets catches token shapes first; the pattern scan is the
        # second line of defense. Either guard must block the write.
        with tempfile.TemporaryDirectory() as tmp:
            evidence = build_evidence()
            evidence["failure_summary"] = "leaked ghp_abcdefghijklmnop token"
            with self.assertRaises((SandboxSmokeError, SanitizationError)):
                write_evidence(Path(tmp) / "evidence.json", evidence)

    def test_credentialed_url_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = build_evidence()
            evidence["failure_summary"] = "postgresql://user:hunter2@db:5432/x"
            with self.assertRaises(SandboxSmokeError):
                write_evidence(Path(tmp) / "evidence.json", evidence)


def _valid_evidence(**overrides: object) -> dict[str, object]:
    evidence = build_evidence()
    evidence["status"] = "passed"
    evidence["leos_commit_sha"] = "abc123"
    evidence["workflow_run_id"] = "42"
    evidence["run_id"] = "42"
    evidence["checks"] = {name: True for name in initial_checks()}
    evidence.update(overrides)
    return evidence


def _check(evidence: dict[str, object]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sandbox_smoke_latest.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        return _sandbox_evidence_check(Path(tmp), path, expected_head="abc123")


class SandboxEvidenceCheckTests(unittest.TestCase):
    def test_valid_evidence_passes(self) -> None:
        result = _check(_valid_evidence())
        self.assertTrue(result["ok"], result)

    def test_missing_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _sandbox_evidence_check(Path(tmp), Path("missing.json"), expected_head="abc123")
        self.assertFalse(result["ok"])
        self.assertIn("could not read", str(result["reason"]))

    def test_wrong_commit_fails(self) -> None:
        result = _check(_valid_evidence(leos_commit_sha="other"))
        self.assertFalse(result["ok"])
        self.assertIn("leos_commit_sha", str(result["reason"]))

    def test_failed_status_fails(self) -> None:
        result = _check(_valid_evidence(status="failed"))
        self.assertFalse(result["ok"])
        self.assertIn("status", str(result["reason"]))

    def test_wrong_evidence_type_fails(self) -> None:
        result = _check(_valid_evidence(evidence_type="postgres_task_queue_concurrency_smoke"))
        self.assertFalse(result["ok"])
        self.assertIn("evidence_type", str(result["reason"]))

    def test_false_check_names_the_check(self) -> None:
        checks = {name: True for name in initial_checks()}
        checks["memory_limit_enforced"] = False
        result = _check(_valid_evidence(checks=checks))
        self.assertFalse(result["ok"])
        self.assertIn("memory_limit_enforced", str(result["reason"]))

    def test_forbidden_marker_in_file_fails(self) -> None:
        result = _check(_valid_evidence(runtime_version="bearer abc"))
        self.assertFalse(result["ok"])
        self.assertIn("forbidden marker", str(result["reason"]))


@unittest.skipUnless(shutil.which("podman"), "requires the podman runtime binary")
class RealRuntimeSandboxSmokeTests(unittest.TestCase):
    def test_real_smoke_passes_all_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = run_smoke(workspace=Path(tmp))
        self.assertEqual(evidence["status"], "passed", evidence)


if __name__ == "__main__":
    unittest.main()
