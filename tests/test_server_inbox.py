"""Tests for the web approval inbox on the service layer.

The inbox is file-exchange compatible with ``FileApprovalGate``: it lists
pending packets from the packet directory, renders them, and emits signed
decisions into the decision directory — reusing ``approval_exchange`` signing
and verification, never reimplementing it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent.approval import ApprovalPacket
from leos_agent.approval_exchange import (
    read_approval_decision,
    verify_approval_decision_signature,
    write_approval_packet,
)
from leos_agent.server import create_app

try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover - exercised only without fastapi installed
    HAVE_FASTAPI = False

_KEY = "test-service-key-0123456789abcdef"
_AUTH = {"x-leos-api-key": _KEY}


def _packet(approval_id: str = "ap-1") -> ApprovalPacket:
    return ApprovalPacket(
        approval_id=approval_id,
        goal_id="g1",
        plan_id="p1",
        step_id="s1",
        step_hash="hash-1",
        tool_name="github_update_file",
        action_summary="Update smoke.txt",
        risk_level="high",
        required_permissions=["network"],
        causal_contract_summary="expects github_file_updated",
        dry_run_summary="would update smoke.txt",
        rollback_summary="delete branch",
        profile="production_github_only",
    )


@unittest.skipUnless(HAVE_FASTAPI, "requires the optional 'fastapi' package")
class InboxTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.data_dir = Path(self._dir.name)
        self.inbox_dir = self.data_dir / "inbox"
        self.packet_dir = self.inbox_dir / "packets"
        self.decision_dir = self.inbox_dir / "decisions"
        self.packet_dir.mkdir(parents=True)
        self.decision_dir.mkdir(parents=True)
        app = create_app(api_key=_KEY, data_dir=self.data_dir, inbox_dir=self.inbox_dir)
        self.http = TestClient(app)


class InboxListTests(InboxTestBase):
    def test_lists_pending_packets(self) -> None:
        write_approval_packet(_packet("ap-1"), self.packet_dir / "ap-1.json")
        write_approval_packet(_packet("ap-2"), self.packet_dir / "ap-2.json")
        response = self.http.get("/inbox", headers=_AUTH)
        self.assertEqual(response.status_code, 200)
        pending = response.json()["pending"]
        self.assertEqual({item["approval_id"] for item in pending}, {"ap-1", "ap-2"})
        self.assertEqual(pending[0]["tool_name"], "github_update_file")
        self.assertEqual(pending[0]["risk_level"], "high")

    def test_decided_packets_leave_the_pending_list(self) -> None:
        write_approval_packet(_packet("ap-1"), self.packet_dir / "ap-1.json")
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "approval-key"}):
            self.http.post(
                "/inbox/ap-1/decide",
                json={"decision": "approve", "approver": "operator"},
                headers=_AUTH,
            )
        response = self.http.get("/inbox", headers=_AUTH)
        self.assertEqual(response.json()["pending"], [])

    def test_requires_auth(self) -> None:
        self.assertEqual(self.http.get("/inbox").status_code, 401)


class InboxRenderTests(InboxTestBase):
    def test_renders_packet_with_risk_and_rollback_scope(self) -> None:
        write_approval_packet(_packet("ap-1"), self.packet_dir / "ap-1.json")
        response = self.http.get("/inbox/ap-1", headers=_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("github_update_file", response.text)
        self.assertIn("delete branch", response.text)

    def test_unknown_packet_is_404(self) -> None:
        response = self.http.get("/inbox/nope", headers=_AUTH)
        self.assertEqual(response.status_code, 404)

    def test_unsafe_approval_id_is_rejected(self) -> None:
        response = self.http.get("/inbox/..%2Fescape", headers=_AUTH)
        self.assertIn(response.status_code, (400, 404))


class InboxDecideTests(InboxTestBase):
    def test_decide_writes_verifiable_signed_decision(self) -> None:
        write_approval_packet(_packet("ap-1"), self.packet_dir / "ap-1.json")
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "approval-key"}):
            response = self.http.post(
                "/inbox/ap-1/decide",
                json={"decision": "approve", "approver": "operator", "reason": "looks bounded"},
                headers=_AUTH,
            )
        self.assertEqual(response.status_code, 200)
        decision_path = self.decision_dir / "ap-1.json"
        self.assertTrue(decision_path.exists())
        decision = read_approval_decision(decision_path)
        self.assertEqual(decision.approval_id, "ap-1")
        self.assertEqual(decision.step_hash, "hash-1")
        self.assertEqual(decision.approver, "operator")
        import json

        signature = json.loads(decision_path.read_text(encoding="utf-8"))["signature"]
        self.assertTrue(verify_approval_decision_signature(decision, "approval-key", signature))
        self.assertFalse(verify_approval_decision_signature(decision, "wrong-key", signature))

    def test_decide_requires_hmac_secret(self) -> None:
        write_approval_packet(_packet("ap-1"), self.packet_dir / "ap-1.json")
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.http.post(
                "/inbox/ap-1/decide",
                json={"decision": "approve", "approver": "operator"},
                headers=_AUTH,
            )
        self.assertEqual(response.status_code, 403)
        self.assertFalse((self.decision_dir / "ap-1.json").exists())

    def test_second_decision_is_refused(self) -> None:
        write_approval_packet(_packet("ap-1"), self.packet_dir / "ap-1.json")
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "approval-key"}):
            first = self.http.post("/inbox/ap-1/decide", json={"decision": "approve", "approver": "a"}, headers=_AUTH)
            second = self.http.post("/inbox/ap-1/decide", json={"decision": "deny", "approver": "b"}, headers=_AUTH)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        # The original decision is untouched.
        decision = read_approval_decision(self.decision_dir / "ap-1.json")
        self.assertEqual(decision.approver, "a")

    def test_invalid_decision_value_is_rejected(self) -> None:
        write_approval_packet(_packet("ap-1"), self.packet_dir / "ap-1.json")
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "approval-key"}):
            response = self.http.post("/inbox/ap-1/decide", json={"decision": "yolo", "approver": "a"}, headers=_AUTH)
        self.assertEqual(response.status_code, 400)

    def test_decide_unknown_packet_is_404(self) -> None:
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "approval-key"}):
            response = self.http.post(
                "/inbox/nope/decide", json={"decision": "approve", "approver": "a"}, headers=_AUTH
            )
        self.assertEqual(response.status_code, 404)


class InboxDisabledTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_FASTAPI, "requires the optional 'fastapi' package")
    def test_inbox_routes_absent_without_inbox_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(api_key=_KEY, data_dir=Path(tmp))
            http = TestClient(app)
            self.assertEqual(http.get("/inbox", headers=_AUTH).status_code, 404)


if __name__ == "__main__":
    unittest.main()
