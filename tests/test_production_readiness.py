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


if __name__ == "__main__":
    unittest.main()
