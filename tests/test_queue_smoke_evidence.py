"""Tests for the Postgres task-queue concurrency smoke evidence pipeline."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.check_production_readiness import _queue_evidence_check
from scripts.queue_smoke import (
    QueueSmokeError,
    build_evidence,
    initial_checks,
    run_smoke,
    write_evidence,
)


class RunSmokeGuardTests(unittest.TestCase):
    def test_missing_dsn_is_reported_not_raised(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            evidence = run_smoke(dsn=None, worker_count=2, task_count=5)
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failure_type"], "postgres_unavailable")

    def test_unreachable_server_is_reported_without_dsn_leak(self) -> None:
        evidence = run_smoke(dsn="postgresql://user:hunter2@localhost:1/none", worker_count=2, task_count=5)
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failure_type"], "postgres_unavailable")
        self.assertNotIn("hunter2", json.dumps(evidence))


class WriteEvidenceTests(unittest.TestCase):
    def test_atomic_write_produces_clean_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            evidence = build_evidence(worker_count=4, task_count=200)
            write_evidence(path, evidence)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["evidence_type"], "postgres_task_queue_concurrency_smoke")
            self.assertTrue(loaded["generated_at"].endswith("Z"))
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_credentialed_dsn_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = build_evidence(worker_count=4, task_count=200)
            evidence["failure_summary"] = "postgresql://postgres:postgres@localhost:5432/postgres"
            with self.assertRaises(QueueSmokeError):
                write_evidence(Path(tmp) / "evidence.json", evidence)


def _valid_evidence(**overrides: object) -> dict[str, object]:
    evidence = build_evidence(worker_count=4, task_count=200)
    evidence["status"] = "passed"
    evidence["leos_commit_sha"] = "abc123"
    evidence["workflow_run_id"] = "42"
    evidence["run_id"] = "42"
    evidence["checks"] = {name: True for name in initial_checks()}
    evidence.update(overrides)
    return evidence


def _check(evidence: dict[str, object]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "queue_smoke_latest.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        return _queue_evidence_check(Path(tmp), path, expected_head="abc123")


class QueueEvidenceCheckTests(unittest.TestCase):
    def test_valid_evidence_passes(self) -> None:
        result = _check(_valid_evidence())
        self.assertTrue(result["ok"], result)

    def test_missing_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _queue_evidence_check(Path(tmp), Path("missing.json"), expected_head="abc123")
        self.assertFalse(result["ok"])
        self.assertIn("could not read", str(result["reason"]))

    def test_wrong_commit_fails(self) -> None:
        result = _check(_valid_evidence(leos_commit_sha="other"))
        self.assertFalse(result["ok"])
        self.assertIn("leos_commit_sha", str(result["reason"]))

    def test_run_id_mismatch_fails(self) -> None:
        result = _check(_valid_evidence(run_id="43"))
        self.assertFalse(result["ok"])
        self.assertIn("run_id", str(result["reason"]))

    def test_wrong_evidence_type_fails(self) -> None:
        result = _check(_valid_evidence(evidence_type="container_sandbox_isolation_smoke"))
        self.assertFalse(result["ok"])
        self.assertIn("evidence_type", str(result["reason"]))

    def test_false_check_names_the_check(self) -> None:
        checks = {name: True for name in initial_checks()}
        checks["no_double_claim"] = False
        result = _check(_valid_evidence(checks=checks))
        self.assertFalse(result["ok"])
        self.assertIn("no_double_claim", str(result["reason"]))

    def test_credentialed_url_in_file_fails(self) -> None:
        result = _check(_valid_evidence(postgres_server_version="postgresql://u:p@h/db"))
        self.assertFalse(result["ok"])
        self.assertIn("forbidden marker", str(result["reason"]))


@unittest.skipUnless(os.environ.get("LEOS_TEST_POSTGRES_DSN"), "requires a live PostgreSQL server")
class RealServerQueueSmokeTests(unittest.TestCase):
    def test_real_smoke_passes_all_checks(self) -> None:
        evidence = run_smoke(os.environ["LEOS_TEST_POSTGRES_DSN"], worker_count=2, task_count=20)
        self.assertEqual(evidence["status"], "passed", evidence)


if __name__ == "__main__":
    unittest.main()
