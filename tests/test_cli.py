"""Tests for CLI operator subcommands."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from leos_agent.audit import AuditLog
from leos_agent.cli import (
    _approval_create,
    _approval_decide,
    _approval_render,
    _audit_check,
    _check_file_exists,
    _dry_run,
    _eval,
    _github_apply,
    _github_dry_run,
    _github_plan,
    _inspect_audit,
    _list_tools,
    _load_json_file,
    _manifest,
    _proof_generate,
    _queue_demo,
    _run,
    _sign_policy,
    _validate_policy,
    _validate_task,
    main,
)
from leos_agent.github_operator import OperatorResult


class ValidateTaskTests(unittest.TestCase):
    def test_valid_task_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            path.write_text(
                json.dumps(
                    {
                        "goal": {"description": "t", "success_criteria": ["ok"]},
                        "steps": [{"tool_name": "echo", "arguments": {"message": "hi"}, "reason": "test"}],
                    }
                )
            )
            self.assertEqual(_validate_task(str(path), tmp), 0)

    def test_invalid_schema_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            path.write_text(json.dumps({"goal": "not_an_object"}))
            self.assertEqual(_validate_task(str(path), tmp), 1)

    def test_unknown_tool_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            path.write_text(
                json.dumps(
                    {
                        "goal": {"description": "t", "success_criteria": ["ok"]},
                        "steps": [{"tool_name": "nonexistent", "arguments": {}, "reason": "test"}],
                    }
                )
            )
            self.assertEqual(_validate_task(str(path), tmp), 1)


class ManifestTests(unittest.TestCase):
    def test_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_manifest(tmp), 0)


class InspectAuditTests(unittest.TestCase):
    def test_on_small_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            log = AuditLog(path=path)
            log.record("step.executed", "ok", observed={"key": "val"})
            self.assertEqual(_inspect_audit(str(path)), 0)


class QueueDemoTests(unittest.TestCase):
    def test_exits_zero(self) -> None:
        self.assertEqual(_queue_demo(), 0)


class EvalCliTests(unittest.TestCase):
    def test_eval_safety_exits_zero(self) -> None:
        self.assertEqual(_eval("safety", output_format="text"), 0)


class PolicyCliTests(unittest.TestCase):
    def test_validate_builtin_production_profile(self) -> None:
        self.assertEqual(_validate_policy(None, profile="production_locked_down"), 0)


class ApprovalCliTests(unittest.TestCase):
    def test_render_approval_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packet.json"
            out = Path(tmp) / "packet.md"
            path.write_text(
                json.dumps(
                    {
                        "approval_id": "a",
                        "goal_id": "g",
                        "plan_id": "p",
                        "step_id": "s",
                        "step_hash": "h",
                        "tool_name": "tool",
                        "risk_level": "medium",
                        "required_permissions": [],
                        "causal_contract_summary": "none",
                        "dry_run_summary": "dry",
                        "rollback_summary": "none",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(_approval_render(str(path), output_format="markdown", output=str(out)), 0)
            self.assertIn("Approval Packet", out.read_text(encoding="utf-8"))

    def test_decide_requires_hmac_secret(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                _approval_decide(
                    "packet.json",
                    "decision.json",
                    approver="operator",
                    decision="approve",
                    reason=None,
                ),
                2,
            )

    def test_create_and_decide_write_private_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "plan.json"
            packet = root / "approval.json"
            decision = root / "approval.decision.json"
            plan.write_text("{}")
            with (
                mock.patch("leos_agent.cli.build_approval_bundle", return_value={"schema": "approval"}),
                mock.patch(
                    "leos_agent.cli.build_signed_decision_bundle",
                    return_value={"schema": "decision"},
                ),
                mock.patch.dict(os.environ, {"LEOS_APPROVAL_HMAC_SECRET": "approval-key"}, clear=True),
            ):
                self.assertEqual(_approval_create(str(plan), str(packet), expires_in=60), 0)
                self.assertEqual(
                    _approval_decide(
                        str(packet),
                        str(decision),
                        approver="operator",
                        decision="approve",
                        reason="reviewed",
                    ),
                    0,
                )
            self.assertEqual(json.loads(packet.read_text()), {"schema": "approval"})
            self.assertEqual(json.loads(decision.read_text()), {"schema": "decision"})
            if os.name != "nt":
                self.assertEqual(packet.stat().st_mode & 0o777, 0o600)
                self.assertEqual(decision.stat().st_mode & 0o777, 0o600)


class GitHubOperatorCliTests(unittest.TestCase):
    def test_doctor_command_is_wired(self) -> None:
        stdout = StringIO()
        with (
            mock.patch("sys.argv", ["leos", "doctor", "--profile", "production_github_only"]),
            redirect_stdout(stdout),
        ):
            code = main()
        self.assertEqual(code, 0)
        self.assertIn('"status": "passed"', stdout.getvalue())

    def test_github_apply_requires_hmac_secret(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                _github_apply(
                    "plan.json",
                    "approval.json",
                    decision_path=None,
                    audit_path=None,
                    receipt_dir=None,
                ),
                2,
            )

    def test_dry_run_and_plan_commands_are_wired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plan.json"
            with (
                mock.patch(
                    "leos_agent.cli.github_issue_dry_run",
                    return_value=OperatorResult(True, "observed", {"writes_performed": False}),
                ),
                mock.patch(
                    "leos_agent.cli.create_draft_plan",
                    return_value={"schema": "leos.github_issue_plan", "status": "draft"},
                ),
                mock.patch.dict(os.environ, {}, clear=True),
            ):
                self.assertEqual(_github_dry_run("owner/repo", 1, audit=None), 0)
                self.assertEqual(_github_plan("owner/repo", 1, str(output)), 0)
            self.assertEqual(json.loads(output.read_text())["status"], "draft")

    def test_github_apply_prints_sanitized_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("plan.json", "approval.json", "approval.decision.json"):
                (root / name).write_text("{}")
            stdout = StringIO()
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "LEOS_APPROVAL_HMAC_SECRET": "approval-key",
                        "LEOS_GITHUB_TOKEN": "token-value",
                    },
                    clear=True,
                ),
                mock.patch(
                    "leos_agent.cli.apply_operator_plan",
                    return_value=OperatorResult(True, "applied", {"automatic_merge": False}),
                ),
                redirect_stdout(stdout),
            ):
                code = _github_apply(
                    str(root / "plan.json"),
                    str(root / "approval.json"),
                    decision_path=str(root / "approval.decision.json"),
                    audit_path=str(root / "audit.jsonl"),
                    receipt_dir=str(root / "receipts"),
                )
        self.assertEqual(code, 0)
        self.assertIn('"automatic_merge": false', stdout.getvalue())
        self.assertNotIn("token-value", stdout.getvalue())


class ProofCliTests(unittest.TestCase):
    def test_proof_generate_no_run_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = _proof_generate(tmp, require_clean=False, allow_dirty=True, no_run=True)

            self.assertIn(code, {0, 2})
            self.assertTrue((Path(tmp) / "MANIFEST.json").exists())


class CliHelperTests(unittest.TestCase):
    def test_check_file_exists_returns_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exists.txt"
            path.write_text("hi")
            result = _check_file_exists(str(path))
            self.assertIsInstance(result, Path)
            self.assertEqual(result, path)

    def test_check_file_exists_returns_none_for_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _check_file_exists(str(Path(tmp) / "nonexistent.txt"))
            self.assertIsNone(result)

    def test_load_json_file_returns_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "valid.json"
            path.write_text('{"key": "value"}')
            data, code = _load_json_file(str(path))
            self.assertEqual(code, 0)
            self.assertEqual(data, {"key": "value"})

    def test_load_json_file_not_found(self) -> None:
        data, code = _load_json_file("/nonexistent/notfound.json")
        self.assertIsNone(data)
        self.assertEqual(code, 2)

    def test_load_json_file_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("not json")
            data, code = _load_json_file(str(path))
            self.assertIsNone(data)
            self.assertEqual(code, 2)

    def test_list_tools_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_list_tools(tmp), 0)

    def test_dry_run_valid_tool_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_dry_run("echo", '{"message": "hi"}', tmp), 0)

    def test_dry_run_unknown_tool_returns_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_dry_run("nonexistent", "{}", tmp), 1)

    def test_dry_run_bad_json_args_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_dry_run("echo", "not json", tmp), 2)

    def test_run_missing_goal_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            path.write_text(json.dumps({"steps": []}))
            self.assertEqual(_run(str(path), tmp, auto_approve=False, profile="developer_local"), 2)

    def test_run_invalid_goal_not_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            path.write_text(json.dumps({"goal": "not_a_dict", "steps": []}))
            self.assertEqual(_run(str(path), tmp, auto_approve=False, profile="developer_local"), 2)

    def test_run_bad_secret_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.json"
            path.write_text(
                json.dumps(
                    {
                        "goal": {
                            "description": "t",
                            "success_criteria": ["ok"],
                            "stop_conditions": ["done"],
                        },
                        "steps": [
                            {
                                "tool_name": "echo",
                                "arguments": {},
                                "reason": "test",
                            }
                        ],
                    }
                )
            )
            self.assertEqual(
                _run(
                    str(path),
                    tmp,
                    auto_approve=True,
                    profile="developer_local",
                    cli_secrets=["badformat"],
                ),
                2,
            )

    def test_validate_policy_with_both_none(self) -> None:
        self.assertEqual(_validate_policy(None, profile=None), 2)

    def test_sign_policy_writes_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import io
            from contextlib import redirect_stdout

            policy_path = Path(tmp) / "policy.json"
            policy_path.write_text(
                json.dumps({"max_risk": "high", "require_human_for": []}),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = _sign_policy(str(policy_path), "secret", output=None)
            self.assertEqual(code, 0)
            self.assertIn('"policy"', stdout.getvalue())

    def test_audit_check_clean_log_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            log = AuditLog(path=path)
            log.record("step.executed", "ok", observed={"key": "val"})
            self.assertEqual(_audit_check(str(path)), 0)


if __name__ == "__main__":
    unittest.main()
