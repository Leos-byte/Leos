from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_production_readiness import _smoke_evidence_check, run_checks


class ProductionSmokeEvidenceTests(unittest.TestCase):
    def test_valid_private_smoke_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _write_evidence(root, _valid_evidence())

            result = _smoke_evidence_check(root, path, expected_head="abc123")

        self.assertTrue(result["ok"], result)

    def test_missing_evidence_fails_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _smoke_evidence_check(Path(tmp), Path("missing.json"))

        self.assertFalse(result["ok"])
        self.assertIn("could not read", str(result["reason"]))

    def test_public_repo_evidence_fails(self) -> None:
        evidence = _valid_evidence(repository_visibility="public")

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("repository_visibility", str(result["reason"]))

    def test_leos_source_repo_evidence_fails(self) -> None:
        evidence = _valid_evidence(repository_under_test="Leos-byte/Leos")

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("leos-smoke", str(result["reason"]))

    def test_wrong_profile_fails(self) -> None:
        result = _check(_valid_evidence(profile="production_locked_down"))

        self.assertFalse(result["ok"])
        self.assertIn("profile", str(result["reason"]))

    def test_failed_status_fails(self) -> None:
        result = _check(_valid_evidence(status="failed"))

        self.assertFalse(result["ok"])
        self.assertIn("status", str(result["reason"]))

    def test_wrong_workflow_trigger_fails(self) -> None:
        result = _check(_valid_evidence(workflow_trigger="push"))

        self.assertFalse(result["ok"])
        self.assertIn("workflow_trigger", str(result["reason"]))

    def test_commit_sha_must_match_current_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _write_evidence(root, _valid_evidence())

            result = _smoke_evidence_check(root, path, expected_head="different")

        self.assertFalse(result["ok"])
        self.assertIn("current git HEAD", str(result["reason"]))

    def test_workflow_run_id_is_required(self) -> None:
        result = _check(_valid_evidence(workflow_run_id=""))

        self.assertFalse(result["ok"])
        self.assertIn("workflow_run_id", str(result["reason"]))

    def test_generated_at_is_required(self) -> None:
        result = _check(_valid_evidence(generated_at=""))

        self.assertFalse(result["ok"])
        self.assertIn("generated_at", str(result["reason"]))

    def test_wrong_runtime_egress_host_fails(self) -> None:
        evidence = _valid_evidence()
        evidence["checks"]["runtime_egress_host"] = "evil.example"  # type: ignore[index]

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("runtime_egress_host", str(result["reason"]))

    def test_missing_signed_approval_fails(self) -> None:
        evidence = _valid_evidence()
        evidence["checks"]["signed_approval_enforced"] = False  # type: ignore[index]

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("signed_approval_enforced", str(result["reason"]))

    def test_read_back_not_verified_fails(self) -> None:
        evidence = _valid_evidence()
        evidence["checks"]["read_back_verified"] = False  # type: ignore[index]

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("read_back_verified", str(result["reason"]))

    def test_cleanup_checks_are_required(self) -> None:
        for check in ("cleanup_requested", "pr_closed", "branch_deleted", "source_repo_unchanged"):
            with self.subTest(check=check):
                evidence = _valid_evidence()
                evidence["checks"][check] = False

                result = _check(evidence)

                self.assertFalse(result["ok"])
                self.assertIn(check, str(result["reason"]))

    def test_github_classic_token_marker_fails(self) -> None:
        evidence = _valid_evidence(notes=["ghp_should_not_be_here"])

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("github_classic_token", str(result["reason"]))

    def test_github_fine_grained_token_marker_fails(self) -> None:
        evidence = _valid_evidence(notes=["github_pat_should_not_be_here"])

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("github_fine_grained_token", str(result["reason"]))

    def test_authorization_marker_fails(self) -> None:
        evidence = _valid_evidence(notes=["Authorization: basic redacted"])

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("authorization_marker", str(result["reason"]))

    def test_bearer_marker_fails_case_insensitively(self) -> None:
        result = _check(_valid_evidence(notes=["bEaReR redacted"]))

        self.assertFalse(result["ok"])
        self.assertIn("bearer_marker", str(result["reason"]))

    def test_secret_environment_names_fail(self) -> None:
        for marker in ("LEOS_GITHUB_TOKEN", "LEOS_APPROVAL_HMAC_SECRET"):
            with self.subTest(marker=marker):
                result = _check(_valid_evidence(notes=[marker]))

                self.assertFalse(result["ok"])
                self.assertIn("secret_name", str(result["reason"]))

    def test_raw_hmac_signature_marker_fails(self) -> None:
        evidence = _valid_evidence(notes=["hmac-sha256:" + "a" * 64])

        result = _check(evidence)

        self.assertFalse(result["ok"])
        self.assertIn("raw_hmac_signature", str(result["reason"]))

    def test_default_production_readiness_does_not_require_smoke_evidence(self) -> None:
        results = run_checks(Path.cwd(), "production_github_only", include_release_proof=False)

        self.assertFalse([result for result in results if not result["ok"]], results)
        self.assertNotIn("smoke evidence", [result["name"] for result in results])


def _check(evidence: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _write_evidence(root, evidence)
        return _smoke_evidence_check(
            root,
            path,
            expected_head=str(evidence.get("leos_commit_sha", "")),
        )


def _write_evidence(root: Path, evidence: dict) -> Path:
    path = root / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def _valid_evidence(**overrides) -> dict:
    evidence = {
        "schema_version": 1,
        "evidence_type": "private_disposable_github_real_write_smoke",
        "profile": "production_github_only",
        "status": "passed",
        "repository_under_test": "Leos-byte/leos-smoke-private-test",
        "repository_visibility": "private",
        "repository_disposable": True,
        "leos_repository": "Leos-byte/Leos",
        "leos_commit_sha": "abc123",
        "workflow_name": "GitHub Real Write Smoke",
        "workflow_run_id": "123",
        "workflow_trigger": "workflow_dispatch",
        "base_branch": "main",
        "work_branch_prefix": "leos/",
        "created_branch": "leos/smoke",
        "pr_number": 1,
        "pr_url": "https://github.com/Leos-byte/leos-smoke-private-test/pull/1",
        "checks": {
            "private_repo_used": True,
            "disposable_repo_guard_passed": True,
            "runtime_attestation_verified": True,
            "runtime_egress_enforced": True,
            "runtime_egress_policy_configured": True,
            "runtime_egress_host": "api.github.com",
            "signed_approval_required": True,
            "signed_approval_enforced": True,
            "approval_signature_verified": True,
            "approval_signature_algorithm": "hmac-sha256",
            "branch_created": True,
            "file_updated": True,
            "pr_opened": True,
            "read_back_verified": True,
            "goal_evaluation_succeeded": True,
            "cleanup_requested": True,
            "pr_closed": True,
            "branch_deleted": True,
            "source_repo_unchanged": True,
            "token_redacted": True,
            "secret_scan_safe": True,
        },
        "generated_at": "2026-06-04T00:00:00Z",
        "generated_by": "manual workflow_dispatch smoke",
        "post_run_required_actions": ["Revoke token."],
        "notes": ["No secrets stored."],
    }
    evidence.update(overrides)
    return evidence


if __name__ == "__main__":
    unittest.main()
