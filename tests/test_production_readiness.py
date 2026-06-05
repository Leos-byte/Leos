from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from scripts.check_production_readiness import (
    _ci_check,
    _profile_check,
    _tool_metadata_check,
    run_checks,
)


class ProductionReadinessTests(unittest.TestCase):
    def test_readiness_config_passes_without_release_proof(self) -> None:
        results = run_checks(Path.cwd(), "production_github_only", include_release_proof=False)

        self.assertFalse([result for result in results if not result["ok"]], results)

    def test_profile_missing_fails(self) -> None:
        result = _profile_check("missing")

        self.assertFalse(result["ok"])
        self.assertIn("Unknown policy profile", str(result["reason"]))

    def test_tool_metadata_failure_is_reported(self) -> None:
        with mock.patch("scripts.check_production_readiness.GitHubUpdateFileTool") as tool_cls:
            tool = tool_cls.return_value
            tool.spec.name = "github_update_file"
            tool.spec.network_access = True
            tool.spec.egress_host = "evil.example"
            tool.spec.egress_methods = ("GET", "PUT")
            tool.spec.rollback_egress_methods = ("GET", "PUT")
            tool.spec.reversibility = "compensatable"
            tool.spec.default_risk = "medium"
            tool.spec.causal_contract = object()
            tool.spec.output_schema = {"type": "object"}
            tool.spec.timeout_ms = 3000

            result = _tool_metadata_check()

        self.assertFalse(result["ok"])
        self.assertIn("egress_host", str(result["reason"]))

    def test_ci_check_fails_without_main_only_proof_check(self) -> None:
        with mock.patch.object(Path, "read_text", return_value="name: ci\non: push\n"):
            result = _ci_check(Path.cwd())

        self.assertFalse(result["ok"])
        self.assertIn("release proof", str(result["reason"]))

    def test_ci_check_fails_without_main_only_production_readiness_check(self) -> None:
        ci = "name: ci\n- name: Check release proof\n  if: github.ref == 'refs/heads/main'\n"
        real_write = "on:\n  workflow_dispatch:\n"

        def fake_read_text(path: Path, *args, **kwargs) -> str:
            del args, kwargs
            return real_write if path.name == "github-real-write.yml" else ci

        with mock.patch.object(Path, "read_text", fake_read_text):
            result = _ci_check(Path.cwd())

        self.assertFalse(result["ok"])
        self.assertIn("production readiness", str(result["reason"]))

    def test_ci_check_does_not_borrow_main_condition_from_another_step(self) -> None:
        ci = """
- name: Check release proof
  if: github.ref == 'refs/heads/main'
  run: python scripts/check_release_proof.py
- name: Check production readiness
  run: |
    python scripts/download_smoke_evidence.py
    python scripts/check_production_readiness.py --profile production_github_only
    --require-smoke-evidence --smoke-evidence-path docs/proofs/real_github_smoke_latest.json
"""
        real_write = "on:\n  workflow_dispatch:\n"

        def fake_read_text(path: Path, *args, **kwargs) -> str:
            del args, kwargs
            return real_write if path.name == "github-real-write.yml" else ci

        with mock.patch.object(Path, "read_text", fake_read_text):
            result = _ci_check(Path.cwd())

        self.assertFalse(result["ok"])
        self.assertIn("production readiness", str(result["reason"]))


if __name__ == "__main__":
    unittest.main()
