"""Tests for one-call recipes over the validated operator path.

Recipes assemble the existing draft -> validate -> approval -> signed decision
-> apply pipeline; they add no gating logic of their own, so every kernel gate
(policy, signed approval, egress, dry-run/verify) still applies.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent.recipes import (
    GitHubFileChange,
    apply_single_file_pr,
    approve_single_file_pr,
    prepare_single_file_pr,
)
from tests.test_github_operator import _client, _StatefulGitHubTransport


def _change(**overrides: object) -> GitHubFileChange:
    values: dict[str, object] = {
        "repo": "acme/leos-smoke",
        "issue_number": 7,
        "path": "smoke.txt",
        "content": "bounded update\n",
        "work_branch": "leos/issue-7",
        "expected_previous": "",
    }
    values.update(overrides)
    return GitHubFileChange(**values)  # type: ignore[arg-type]


class PrepareRecipeTests(unittest.TestCase):
    def test_prepare_returns_ready_plan_and_approval_bundle(self) -> None:
        transport = _StatefulGitHubTransport()
        prepared = prepare_single_file_pr(_change(), client=_client(transport))
        self.assertEqual(prepared.plan["status"], "ready")
        self.assertEqual(prepared.plan["work_branch"], "leos/issue-7")
        self.assertEqual(len(prepared.approval["packets"]), 3)
        self.assertEqual(prepared.approval["plan_id"], prepared.plan["plan_id"])
        # Preparation performs no writes.
        self.assertEqual(transport.write_methods, [])

    def test_prepare_rejects_invalid_branch(self) -> None:
        transport = _StatefulGitHubTransport()
        with self.assertRaises(ValueError):
            prepare_single_file_pr(_change(work_branch="main"), client=_client(transport))

    def test_prepare_rejects_both_guards(self) -> None:
        transport = _StatefulGitHubTransport()
        with self.assertRaises(ValueError):
            prepare_single_file_pr(_change(expected_previous="", expected_sha="abc"), client=_client(transport))

    def test_prepare_rejects_secret_like_content(self) -> None:
        transport = _StatefulGitHubTransport()
        with self.assertRaises(ValueError):
            prepare_single_file_pr(_change(content="github_pat_must_not_cross_boundary"), client=_client(transport))


class ApplyRecipeTests(unittest.TestCase):
    def test_full_recipe_applies_through_all_gates(self) -> None:
        transport = _StatefulGitHubTransport()
        prepared = prepare_single_file_pr(_change(), client=_client(transport))
        decision = approve_single_file_pr(prepared, approver="operator", signature_secret="approval-key")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"LEOS_ENABLE_REAL_GITHUB_WRITES": "1"}),
        ):
            result = apply_single_file_pr(
                prepared,
                decision,
                token_value="token-value",
                signature_secret="approval-key",
                work_dir=Path(tmp),
                client=_client(transport),
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["evaluation_status"], "succeeded")

    def test_recipe_apply_respects_real_write_gate(self) -> None:
        transport = _StatefulGitHubTransport()
        prepared = prepare_single_file_pr(_change(), client=_client(transport))
        decision = approve_single_file_pr(prepared, approver="operator", signature_secret="approval-key")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            result = apply_single_file_pr(
                prepared,
                decision,
                token_value="token-value",
                signature_secret="approval-key",
                work_dir=Path(tmp),
                client=_client(transport),
            )
        self.assertFalse(result.ok)
        self.assertEqual(transport.write_methods, [])

    def test_denied_decision_blocks_apply(self) -> None:
        transport = _StatefulGitHubTransport()
        prepared = prepare_single_file_pr(_change(), client=_client(transport))
        decision = approve_single_file_pr(
            prepared, approver="operator", signature_secret="approval-key", decision="deny"
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"LEOS_ENABLE_REAL_GITHUB_WRITES": "1"}),
        ):
            result = apply_single_file_pr(
                prepared,
                decision,
                token_value="token-value",
                signature_secret="approval-key",
                work_dir=Path(tmp),
                client=_client(transport),
            )
        self.assertFalse(result.ok)
        self.assertEqual(transport.write_methods, [])


if __name__ == "__main__":
    unittest.main()
