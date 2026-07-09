"""Red-team tests for the web approval inbox and signed-apply surface.

Adversarial variants of the service-layer flows: tampering with plans after
approval, using expired packets, replaying consumed decisions, traversal-style
identifiers, and signing attempts without the HMAC secret. Every scenario must
be refused with no write side effects and no plaintext secrets on disk.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from leos_agent.approval import ApprovalPacket
from leos_agent.approval_exchange import write_approval_packet
from leos_agent.server import create_app
from tests.test_github_operator import _client, _ready_plan, _StatefulGitHubTransport

try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover - exercised only without fastapi installed
    HAVE_FASTAPI = False

_KEY = "redteam-service-key-0123456789abcdef"
_AUTH = {"x-leos-api-key": _KEY}
_APPLY_ENV = {
    "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
    "LEOS_APPROVAL_HMAC_SECRET": "approval-key",
    "LEOS_GITHUB_TOKEN": "token-value",
}


def _packet(approval_id: str = "rt-1") -> ApprovalPacket:
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
class _RedTeamBase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.data_dir = Path(self._dir.name)
        self.inbox_dir = self.data_dir / "inbox"
        self.packet_dir = self.inbox_dir / "packets"
        self.decision_dir = self.inbox_dir / "decisions"
        self.packet_dir.mkdir(parents=True)
        self.transport = _StatefulGitHubTransport()
        app = create_app(
            api_key=_KEY,
            data_dir=self.data_dir,
            github_client=_client(self.transport),
            inbox_dir=self.inbox_dir,
        )
        self.http = TestClient(app)

    def _approved_bundle(
        self, plan: dict[str, Any], *, expires_in_seconds: float | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        body: dict[str, Any] = {"plan": plan}
        if expires_in_seconds is not None:
            body["expires_in_seconds"] = expires_in_seconds
        approval = self.http.post("/approvals", json=body, headers=_AUTH).json()
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "approval-key"}):
            decision = self.http.post(
                "/approvals/decide",
                json={"approval": approval, "decision": "approve", "approver": "operator"},
                headers=_AUTH,
            ).json()
        return approval, decision

    def _apply(self, plan: dict[str, Any], approval: dict[str, Any], decision: dict[str, Any]) -> Any:
        with mock.patch.dict(os.environ, _APPLY_ENV, clear=True):
            return self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )


class TamperedPlanRedTeamTests(_RedTeamBase):
    def test_plan_tampered_after_approval_is_refused_without_writes(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        plan["change"]["content"] = "attacker-controlled payload\n"

        response = self._apply(plan, approval, decision)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.write_methods, [])

    def test_step_swap_between_approved_plans_is_refused(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        other = _ready_plan(self.transport)
        other["plan_id"] = plan["plan_id"]

        response = self._apply(other, approval, decision)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.write_methods, [])


class ExpiredApprovalRedTeamTests(_RedTeamBase):
    def test_expired_approval_is_refused_without_writes(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan, expires_in_seconds=0.05)
        time.sleep(0.2)

        response = self._apply(plan, approval, decision)

        # The refusal message is deliberately generic (sanitized); the
        # invariant is the 403 and the absence of any write.
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.write_methods, [])


class ReplayRedTeamTests(_RedTeamBase):
    def test_second_inbox_decision_is_refused_and_first_is_preserved(self) -> None:
        write_approval_packet(_packet("rt-replay"), self.packet_dir / "rt-replay.json")
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "inbox-secret"}):
            first = self.http.post(
                "/inbox/rt-replay/decide", json={"decision": "approve", "approver": "op"}, headers=_AUTH
            )
            original = (self.decision_dir / "rt-replay.json").read_bytes()
            second = self.http.post(
                "/inbox/rt-replay/decide", json={"decision": "deny", "approver": "attacker"}, headers=_AUTH
            )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual((self.decision_dir / "rt-replay.json").read_bytes(), original)

    def test_consumed_apply_decision_cannot_be_replayed(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        first = self._apply(plan, approval, decision)
        replay = self._apply(plan, approval, decision)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 403)


class TraversalRedTeamTests(_RedTeamBase):
    def test_traversal_identifiers_are_rejected_everywhere(self) -> None:
        for path in ("/inbox/..%2F..%2Fetc%2Fpasswd", "/inbox/..%2Fescape/decide"):
            response = (
                self.http.get(path, headers=_AUTH)
                if "decide" not in path
                else self.http.post(path, json={"decision": "approve"}, headers=_AUTH)
            )
            self.assertIn(response.status_code, (400, 404), path)
        for path in ("/audit/..%2Fsecrets", "/trace/..%2F..%2Fetc"):
            response = self.http.get(path, headers=_AUTH)
            self.assertIn(response.status_code, (400, 404), path)
        self.assertEqual(list(self.decision_dir.glob("*")) if self.decision_dir.is_dir() else [], [])

    def test_traversal_decision_leaves_no_file_outside_inbox(self) -> None:
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "inbox-secret"}):
            self.http.post("/inbox/..%2Foutside/decide", json={"decision": "approve"}, headers=_AUTH)
        self.assertFalse((self.inbox_dir / "outside.json").exists())
        self.assertFalse((self.data_dir / "outside.json").exists())


class MissingSecretRedTeamTests(_RedTeamBase):
    def test_decide_without_hmac_secret_is_refused_with_no_disk_side_effects(self) -> None:
        write_approval_packet(_packet("rt-nosecret"), self.packet_dir / "rt-nosecret.json")
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.http.post(
                "/inbox/rt-nosecret/decide", json={"decision": "approve", "approver": "op"}, headers=_AUTH
            )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.decision_dir.exists())

    def test_unauthenticated_inbox_is_refused(self) -> None:
        write_approval_packet(_packet("rt-auth"), self.packet_dir / "rt-auth.json")
        self.assertEqual(self.http.get("/inbox").status_code, 401)
        self.assertEqual(
            self.http.post("/inbox/rt-auth/decide", json={"decision": "approve"}).status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()
