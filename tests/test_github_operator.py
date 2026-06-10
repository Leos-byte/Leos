from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from leos_agent.approval import ApprovalDecisionValue
from leos_agent.egress import EgressPolicy
from leos_agent.github_client import GitHubHTTPResponse, GitHubRESTClient
from leos_agent.github_operator import (
    apply_operator_plan,
    build_approval_bundle,
    build_signed_decision_bundle,
    create_draft_plan,
    github_issue_dry_run,
    validate_operator_plan,
)
from leos_agent.tools import Secret


class _StatefulGitHubTransport:
    def __init__(self) -> None:
        self.issue = {
            "number": 7,
            "title": "Bounded change",
            "body": "Update the smoke file.",
            "state": "open",
            "html_url": "https://github.com/acme/leos-smoke/issues/7",
        }
        self.branches = {"main": "base-sha"}
        self.files: dict[tuple[str, str], tuple[str, str]] = {}
        self.prs: list[dict[str, object]] = []
        self.write_methods: list[str] = []
        self.put_payloads: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers,
        body: bytes | None,
        timeout_seconds: float,
    ) -> GitHubHTTPResponse:
        del headers, timeout_seconds
        parsed = urlparse(url)
        path = parsed.path
        payload = json.loads(body) if body else {}
        if method != "GET":
            self.write_methods.append(method)
        if path.endswith("/issues/7") and method == "GET":
            return self._response(200, self.issue)
        if "/git/ref/heads/" in path and method == "GET":
            branch = path.split("/git/ref/heads/", 1)[1]
            sha = self.branches.get(branch)
            return self._response(200, {"object": {"sha": sha}}) if sha else self._response(404, {"message": "missing"})
        if path.endswith("/git/refs") and method == "POST":
            branch = str(payload["ref"]).removeprefix("refs/heads/")
            sha = str(payload["sha"])
            self.branches[branch] = sha
            return self._response(201, {"object": {"sha": sha}})
        if "/contents/" in path and method == "GET":
            file_path = path.split("/contents/", 1)[1]
            branch = parse_qs(parsed.query).get("ref", ["main"])[0]
            value = self.files.get((branch, file_path))
            if value is None:
                return self._response(404, {"message": "missing"})
            content, sha = value
            return self._response(
                200,
                {"content": base64.b64encode(content.encode()).decode(), "encoding": "base64", "sha": sha},
            )
        if "/contents/" in path and method == "PUT":
            self.put_payloads.append(dict(payload))
            file_path = path.split("/contents/", 1)[1]
            branch = str(payload["branch"])
            content = base64.b64decode(str(payload["content"])).decode()
            sha = f"sha-{len(self.files) + 1}"
            self.files[(branch, file_path)] = (content, sha)
            return self._response(200, {"content": {"sha": sha}, "commit": {"sha": "commit-sha"}})
        if path.endswith("/pulls") and method == "GET":
            return self._response(200, self.prs)
        if path.endswith("/pulls") and method == "POST":
            pr = {
                "number": len(self.prs) + 1,
                "state": "open",
                "head": {"ref": payload["head"]},
                "base": {"ref": payload["base"]},
                "body": payload["body"],
                "html_url": "https://github.com/acme/leos-smoke/pull/1",
            }
            self.prs.append(pr)
            return self._response(201, pr)
        return self._response(500, {"message": f"unexpected {method} {path}"})

    @staticmethod
    def _response(status: int, payload: object) -> GitHubHTTPResponse:
        return GitHubHTTPResponse(status, json.dumps(payload).encode(), {})


def _client(transport: _StatefulGitHubTransport) -> GitHubRESTClient:
    policy = EgressPolicy(allowed_hosts=("api.github.com",))
    return GitHubRESTClient(transport=transport, egress_policy=policy, enforce_egress=True)


def _ready_plan(transport: _StatefulGitHubTransport) -> dict[str, object]:
    plan = create_draft_plan("acme/leos-smoke", 7, client=_client(transport))
    plan["status"] = "ready"
    plan["work_branch"] = "leos/issue-7"
    change = plan["change"]
    assert isinstance(change, dict)
    change.update(
        {
            "path": "smoke.txt",
            "content": "bounded update\n",
            "expected_previous": "",
        }
    )
    return plan


class GitHubOperatorTests(unittest.TestCase):
    def test_dry_run_reads_issue_without_writes(self) -> None:
        transport = _StatefulGitHubTransport()
        result = github_issue_dry_run("acme/leos-smoke", 7, client=_client(transport))
        self.assertTrue(result.ok)
        self.assertEqual(transport.write_methods, [])
        self.assertFalse(result.data["writes_performed"])

    def test_plan_rejects_missing_optimistic_guard(self) -> None:
        plan = _ready_plan(_StatefulGitHubTransport())
        change = plan["change"]
        assert isinstance(change, dict)
        change["expected_previous"] = None
        self.assertIn(
            "exactly one of expected_sha or expected_previous is required",
            validate_operator_plan(plan),
        )

    def test_plan_rejects_token_like_values(self) -> None:
        plan = _ready_plan(_StatefulGitHubTransport())
        change = plan["change"]
        assert isinstance(change, dict)
        change["content"] = "github_pat_must_not_cross_boundary"
        self.assertTrue(any("secret-like" in issue for issue in validate_operator_plan(plan)))

    def test_apply_requires_explicit_real_write_flag(self) -> None:
        transport = _StatefulGitHubTransport()
        plan = _ready_plan(transport)
        approval = build_approval_bundle(plan)
        decisions = build_signed_decision_bundle(
            approval,
            decision_value="approve",
            approver="operator",
            signature_secret="approval-key",
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            result = apply_operator_plan(
                plan,
                approval,
                decisions,
                token=Secret("token-value"),
                signature_secret="approval-key",
                audit_path=Path(tmp) / "audit.jsonl",
                receipt_dir=Path(tmp) / "receipts",
                client=_client(transport),
            )
        self.assertFalse(result.ok)
        self.assertEqual(transport.write_methods, [])

    def test_signed_apply_succeeds_and_replay_is_rejected(self) -> None:
        transport = _StatefulGitHubTransport()
        plan = _ready_plan(transport)
        approval = build_approval_bundle(plan)
        decisions = build_signed_decision_bundle(
            approval,
            decision_value=ApprovalDecisionValue.APPROVE.value,
            approver="operator",
            signature_secret="approval-key",
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LEOS_ENABLE_REAL_GITHUB_WRITES": "1"}):
            root = Path(tmp)
            first = apply_operator_plan(
                plan,
                approval,
                decisions,
                token=Secret("token-value"),
                signature_secret="approval-key",
                audit_path=root / "first.jsonl",
                receipt_dir=root / "receipts",
                client=_client(transport),
            )
            second = apply_operator_plan(
                plan,
                approval,
                decisions,
                token=Secret("token-value"),
                signature_secret="approval-key",
                audit_path=root / "second.jsonl",
                receipt_dir=root / "receipts",
                client=_client(transport),
            )
            combined = (root / "first.jsonl").read_text() + (root / "second.jsonl").read_text()
        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(first.data["evaluation_status"], "succeeded")
        self.assertNotIn("sha", transport.put_payloads[0])
        self.assertNotIn("token-value", combined)

    def test_expired_packet_wrong_profile_and_changed_hash_are_rejected(self) -> None:
        transport = _StatefulGitHubTransport()
        plan = _ready_plan(transport)
        approval = build_approval_bundle(plan, expires_in_seconds=0.01)
        decisions = build_signed_decision_bundle(
            approval,
            decision_value="approve",
            approver="operator",
            signature_secret="approval-key",
        )
        time.sleep(0.02)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LEOS_ENABLE_REAL_GITHUB_WRITES": "1"}):
            expired = apply_operator_plan(
                plan,
                approval,
                decisions,
                token=Secret("token-value"),
                signature_secret="approval-key",
                audit_path=Path(tmp) / "expired.jsonl",
                receipt_dir=Path(tmp) / "receipts-expired",
                client=_client(transport),
            )
            wrong_profile = dict(approval)
            wrong_profile["profile"] = "developer_local"
            profile_result = apply_operator_plan(
                plan,
                wrong_profile,
                decisions,
                token=Secret("token-value"),
                signature_secret="approval-key",
                audit_path=Path(tmp) / "profile.jsonl",
                receipt_dir=Path(tmp) / "receipts-profile",
                client=_client(transport),
            )
            changed = json.loads(json.dumps(plan))
            changed["work_branch"] = "leos/changed"
            changed_result = apply_operator_plan(
                changed,
                approval,
                decisions,
                token=Secret("token-value"),
                signature_secret="approval-key",
                audit_path=Path(tmp) / "changed.jsonl",
                receipt_dir=Path(tmp) / "receipts-changed",
                client=_client(transport),
            )
        self.assertFalse(expired.ok)
        self.assertFalse(profile_result.ok)
        self.assertFalse(changed_result.ok)


if __name__ == "__main__":
    unittest.main()
