"""Tests for the deny-by-default policy wizard and `leos policy init`."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent.cli import main
from leos_agent.enums import Permission, RiskLevel
from leos_agent.policy import PolicyProfile, validate_policy_config
from leos_agent.policy_wizard import generate_policy_config


class GeneratePolicyConfigTests(unittest.TestCase):
    def test_default_config_is_deny_first_and_valid(self) -> None:
        config = generate_policy_config(name="my_profile")
        self.assertEqual(validate_policy_config(config), [])
        profile = PolicyProfile.from_mapping(config)
        self.assertEqual(profile.name, "my_profile")
        self.assertEqual(tuple(profile.granted_permissions), ())
        self.assertEqual(tuple(profile.allowed_tools), ())
        self.assertEqual(profile.max_auto_risk, RiskLevel.LOW)
        self.assertTrue(profile.network_default_deny)
        self.assertTrue(profile.require_signed_approval)
        self.assertTrue(profile.require_typed_goal_criteria)
        self.assertTrue(profile.require_strong_sandbox_for_execute)
        self.assertTrue(profile.require_causal_contract_for_medium_risk)
        self.assertTrue(profile.require_timeout_for_medium_risk)
        self.assertTrue(profile.require_output_schema_for_medium_risk)

    def test_ungranted_permissions_are_explicitly_denied(self) -> None:
        config = generate_policy_config(name="p", granted_permissions=("read_files",))
        profile = PolicyProfile.from_mapping(config)
        self.assertIn(Permission.READ_FILES, profile.granted_permissions)
        self.assertNotIn(Permission.READ_FILES, profile.deny_permissions)
        self.assertIn(Permission.DELETE, profile.deny_permissions)
        self.assertIn(Permission.FINANCIAL, profile.deny_permissions)

    def test_granted_write_permissions_require_human_approval(self) -> None:
        config = generate_policy_config(name="p", granted_permissions=("read_files", "write_files", "network"))
        profile = PolicyProfile.from_mapping(config)
        self.assertIn(Permission.WRITE_FILES, profile.require_human_for)
        self.assertIn(Permission.NETWORK, profile.require_human_for)
        self.assertNotIn(Permission.READ_FILES, profile.require_human_for)

    def test_egress_hosts_produce_allowlist_policy(self) -> None:
        config = generate_policy_config(name="p", egress_hosts=("api.github.com",))
        profile = PolicyProfile.from_mapping(config)
        assert profile.egress_policy is not None
        self.assertEqual(tuple(profile.egress_policy.allowed_hosts), ("api.github.com",))

    def test_no_egress_hosts_means_no_egress_policy(self) -> None:
        config = generate_policy_config(name="p")
        self.assertNotIn("egress_policy", config)

    def test_invalid_permission_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_policy_config(name="p", granted_permissions=("root_everything",))

    def test_invalid_risk_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_policy_config(name="p", max_auto_risk="apocalyptic")

    def test_empty_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_policy_config(name="")


def _run_cli(*argv: str) -> int:
    with mock.patch("sys.argv", ["leos", *argv]):
        return main()


class PolicyInitCliTests(unittest.TestCase):
    def test_non_interactive_init_writes_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "policy.json"
            code = _run_cli(
                "policy",
                "init",
                "--name",
                "team_profile",
                "--allow-tool",
                "echo",
                "--grant",
                "read_files",
                "--output",
                str(output),
                "--non-interactive",
            )
            self.assertEqual(code, 0)
            config = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(validate_policy_config(config), [])
            self.assertEqual(config["name"], "team_profile")
            self.assertEqual(config["allowed_tools"], ["echo"])

    def test_refuses_to_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "policy.json"
            output.write_text("{}", encoding="utf-8")
            code = _run_cli("policy", "init", "--name", "p", "--output", str(output), "--non-interactive")
            self.assertNotEqual(code, 0)

    def test_interactive_prompts_fill_missing_fields(self) -> None:
        answers = iter(["wizard_profile", "echo, safe_file_write", "read_files", ""])
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "policy.json"
            with mock.patch("builtins.input", side_effect=lambda _prompt: next(answers)):
                code = _run_cli("policy", "init", "--output", str(output))
            self.assertEqual(code, 0)
            config = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(config["name"], "wizard_profile")
            self.assertEqual(config["allowed_tools"], ["echo", "safe_file_write"])
            self.assertEqual(config["granted_permissions"], ["read_files"])
            self.assertEqual(validate_policy_config(config), [])

    def test_generated_profile_loads_into_policy_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "policy.json"
            _run_cli("policy", "init", "--name", "p", "--output", str(output), "--non-interactive")
            code = _run_cli("validate-policy", str(output))
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
