"""Tests for the thin FastAPI service layer.

The service is a pure transport shell over the existing operator flow: every
gate (policy, signed approval, egress, dry-run/verify) stays on the
``apply_operator_plan`` path. These tests assert the shell adds boundary auth
without substituting for the ``ApprovalGate``, and that no endpoint bypasses
the kernel gates. FastAPI is an optional dependency; tests are skipped when it
is absent.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from leos_agent.server import ServerConfigurationError, ServerUnavailable, create_app
from tests.test_github_operator import _client, _ready_plan, _StatefulGitHubTransport

try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover - exercised only without fastapi installed
    HAVE_FASTAPI = False

_KEY = "test-service-key"
_AUTH = {"x-leos-api-key": _KEY}


@unittest.skipUnless(HAVE_FASTAPI, "requires the optional 'fastapi' package")
class _ServerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.data_dir = Path(self._dir.name)
        self.transport = _StatefulGitHubTransport()
        app = create_app(api_key=_KEY, data_dir=self.data_dir, github_client=_client(self.transport))
        self.http = TestClient(app)

    def _approved_bundle(self, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        approval = self.http.post("/approvals", json={"plan": plan}, headers=_AUTH).json()
        with mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "approval-key"}):
            decision = self.http.post(
                "/approvals/decide",
                json={"approval": approval, "decision": "approve", "approver": "operator"},
                headers=_AUTH,
            ).json()
        return approval, decision


class HealthTests(_ServerTestBase):
    def test_healthz_needs_no_auth(self) -> None:
        response = self.http.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_readyz_reports_profile_and_write_gate(self) -> None:
        response = self.http.get("/readyz")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ready")
        self.assertIn("writes_enabled", body)


class AuthTests(_ServerTestBase):
    def test_missing_api_key_is_unauthorized(self) -> None:
        response = self.http.post("/plans", json={"repo": "acme/leos-smoke", "issue_number": 7})
        self.assertEqual(response.status_code, 401)

    def test_wrong_api_key_is_unauthorized(self) -> None:
        response = self.http.post(
            "/plans",
            json={"repo": "acme/leos-smoke", "issue_number": 7},
            headers={"x-leos-api-key": "wrong"},
        )
        self.assertEqual(response.status_code, 401)

    def test_create_app_without_api_key_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ServerConfigurationError):
            create_app(data_dir=self.data_dir)


class PlanEndpointTests(_ServerTestBase):
    def test_post_plans_returns_draft(self) -> None:
        response = self.http.post("/plans", json={"repo": "acme/leos-smoke", "issue_number": 7}, headers=_AUTH)
        self.assertEqual(response.status_code, 200)
        draft = response.json()
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["profile"], "production_github_only")
        self.assertEqual(self.transport.write_methods, [])

    def test_validate_reports_issues_for_draft(self) -> None:
        draft = self.http.post("/plans", json={"repo": "acme/leos-smoke", "issue_number": 7}, headers=_AUTH).json()
        response = self.http.post("/plans/validate", json={"plan": draft}, headers=_AUTH)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ready"])
        self.assertTrue(body["issues"])

    def test_validate_passes_ready_plan(self) -> None:
        plan = _ready_plan(self.transport)
        response = self.http.post("/plans/validate", json={"plan": plan}, headers=_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])
        self.assertEqual(response.json()["issues"], [])


class ApprovalEndpointTests(_ServerTestBase):
    def test_post_approvals_returns_signed_packets_bundle(self) -> None:
        plan = _ready_plan(self.transport)
        response = self.http.post("/approvals", json={"plan": plan}, headers=_AUTH)
        self.assertEqual(response.status_code, 200)
        bundle = response.json()
        self.assertEqual(bundle["plan_id"], plan["plan_id"])
        self.assertEqual(len(bundle["packets"]), 3)
        self.assertIn("plan_digest", bundle)

    def test_post_approvals_rejects_invalid_plan(self) -> None:
        response = self.http.post("/approvals", json={"plan": {"schema": "nope"}}, headers=_AUTH)
        self.assertEqual(response.status_code, 400)

    def test_decide_requires_hmac_secret(self) -> None:
        plan = _ready_plan(self.transport)
        approval = self.http.post("/approvals", json={"plan": plan}, headers=_AUTH).json()
        with mock.patch.dict(os.environ, {}, clear=True):
            response = self.http.post(
                "/approvals/decide",
                json={"approval": approval, "decision": "approve", "approver": "operator"},
                headers=_AUTH,
            )
        self.assertEqual(response.status_code, 403)


class ApplyEndpointTests(_ServerTestBase):
    """Every /apply request goes through apply_operator_plan — no gate is inlined."""

    def test_apply_refused_when_real_writes_disabled(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        env = {"LEOS_APPROVAL_HMAC_SECRET": "approval-key", "LEOS_GITHUB_TOKEN": "token-value"}
        with mock.patch.dict(os.environ, env, clear=True):
            response = self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.write_methods, [])

    def test_apply_refused_without_github_token(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        env = {"LEOS_ENABLE_REAL_GITHUB_WRITES": "1", "LEOS_APPROVAL_HMAC_SECRET": "approval-key"}
        with mock.patch.dict(os.environ, env, clear=True):
            response = self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.write_methods, [])

    def test_signed_apply_succeeds_then_replay_is_refused(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        env = {
            "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
            "LEOS_APPROVAL_HMAC_SECRET": "approval-key",
            "LEOS_GITHUB_TOKEN": "token-value",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            first = self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )
            second = self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )
        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["evaluation_status"], "succeeded")
        # Replayed decisions are consumed exactly once by the approval gate.
        self.assertEqual(second.status_code, 403)

    def test_apply_with_tampered_signature_is_blocked_without_writes(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        for item in decision["decisions"]:
            item["signature"] = "0" * 64
        env = {
            "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
            "LEOS_APPROVAL_HMAC_SECRET": "approval-key",
            "LEOS_GITHUB_TOKEN": "token-value",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            response = self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.write_methods, [])

    def test_apply_with_wrong_profile_is_refused(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        decision["profile"] = "developer_local"
        env = {
            "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
            "LEOS_APPROVAL_HMAC_SECRET": "approval-key",
            "LEOS_GITHUB_TOKEN": "token-value",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            response = self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.write_methods, [])

    def test_apply_with_mismatched_digest_is_refused(self) -> None:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        change = plan["change"]
        assert isinstance(change, dict)
        change["content"] = "tampered after approval\n"
        env = {
            "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
            "LEOS_APPROVAL_HMAC_SECRET": "approval-key",
            "LEOS_GITHUB_TOKEN": "token-value",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            response = self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.write_methods, [])


class AuditTraceEndpointTests(_ServerTestBase):
    def _applied_plan(self) -> dict[str, Any]:
        plan = _ready_plan(self.transport)
        approval, decision = self._approved_bundle(plan)
        env = {
            "LEOS_ENABLE_REAL_GITHUB_WRITES": "1",
            "LEOS_APPROVAL_HMAC_SECRET": "approval-key",
            "LEOS_GITHUB_TOKEN": "token-value",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            response = self.http.post(
                "/apply", json={"plan": plan, "approval": approval, "decision": decision}, headers=_AUTH
            )
        self.assertEqual(response.status_code, 200)
        return plan

    def test_audit_endpoint_returns_recorded_events(self) -> None:
        plan = self._applied_plan()
        response = self.http.get(f"/audit/{plan['plan_id']}", headers=_AUTH)
        self.assertEqual(response.status_code, 200)
        events = response.json()["events"]
        self.assertTrue(events)
        self.assertTrue(any("approval" in str(e.get("event_type", "")) for e in events))

    def test_audit_events_contain_no_token(self) -> None:
        plan = self._applied_plan()
        response = self.http.get(f"/audit/{plan['plan_id']}", headers=_AUTH)
        self.assertNotIn("token-value", response.text)

    def test_trace_endpoint_renders_html(self) -> None:
        plan = self._applied_plan()
        response = self.http.get(f"/trace/{plan['plan_id']}", headers=_AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("<html", response.text.lower())

    def test_audit_unknown_plan_is_404(self) -> None:
        response = self.http.get("/audit/does-not-exist", headers=_AUTH)
        self.assertEqual(response.status_code, 404)

    def test_audit_rejects_unsafe_plan_id(self) -> None:
        response = self.http.get("/audit/..%2Fescape", headers=_AUTH)
        self.assertIn(response.status_code, (400, 404))


class MissingDependencyTests(unittest.TestCase):
    def test_missing_fastapi_raises_server_unavailable(self) -> None:
        real_import = __import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "fastapi" or name.startswith("fastapi."):
                raise ImportError("no fastapi")
            return real_import(name, *args, **kwargs)

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("builtins.__import__", side_effect=fake_import),
            self.assertRaises(ServerUnavailable),
        ):
            create_app(api_key="k", data_dir=Path(tmp))


if __name__ == "__main__":
    unittest.main()
