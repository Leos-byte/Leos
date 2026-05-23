from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from leos_agent import EchoTool, Permission, RiskLevel, ToolManifest
from leos_agent.enums import CompensationStrategy, Reversibility
from leos_agent.tool_manifest_registry import ToolManifestRegistry, ToolManifestRegistryError


def _manifest(**overrides: object) -> ToolManifest:
    data = {
        "name": "echo",
        "version": "0.1.0",
        "permissions": (),
        "risk": RiskLevel.LOW,
        "reversibility": Reversibility.IRREVERSIBLE,
        "input_schema": {},
        "output_schema": {},
        "compensation_strategy": CompensationStrategy.NONE,
    }
    data.update(overrides)
    return ToolManifest(**data)  # type: ignore[arg-type]


def _manifest_json(name: str) -> dict[str, object]:
    return {
        "name": name,
        "version": "0.1.0",
        "permissions": [],
        "risk": "low",
        "reversibility": "irreversible",
        "input_schema": {},
    }


class ToolManifestRegistryTests(unittest.TestCase):
    def test_register_valid_manifest_succeeds(self) -> None:
        registry = ToolManifestRegistry()

        registry.register(_manifest())

        self.assertEqual(registry.names(), ["echo"])

    def test_duplicate_name_rejected(self) -> None:
        registry = ToolManifestRegistry()
        registry.register(_manifest())

        with self.assertRaises(ToolManifestRegistryError):
            registry.register(_manifest())

    def test_invalid_permission_rejected(self) -> None:
        with self.assertRaises(ToolManifestRegistryError):
            ToolManifestRegistry().register(_manifest(permissions=("invalid",)))

    def test_invalid_risk_rejected(self) -> None:
        with self.assertRaises(ToolManifestRegistryError):
            ToolManifestRegistry().register(_manifest(risk="invalid"))

    def test_missing_required_fields_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "bad.json").write_text(json.dumps({"name": "bad"}), encoding="utf-8")

            with self.assertRaises(ToolManifestRegistryError):
                ToolManifestRegistry().load_directory(path)

    def test_validate_against_tool_succeeds_for_echo(self) -> None:
        registry = ToolManifestRegistry()
        registry.register(EchoTool().spec.manifest())

        registry.validate_against_tool(EchoTool())

    def test_validate_against_tool_rejects_mismatched_permissions(self) -> None:
        registry = ToolManifestRegistry()
        registry.register(_manifest(permissions=(Permission.READ_FILES,)))

        with self.assertRaises(ToolManifestRegistryError):
            registry.validate_against_tool(EchoTool())

    def test_validate_against_tool_rejects_lower_manifest_risk(self) -> None:
        tool = EchoTool()
        tool.spec = tool.spec.__class__(
            name="echo",
            description="higher risk echo",
            permissions=(),
            default_risk=RiskLevel.MEDIUM,
        )
        registry = ToolManifestRegistry()
        registry.register(_manifest(risk=RiskLevel.LOW))

        with self.assertRaises(ToolManifestRegistryError):
            registry.validate_against_tool(tool)

    def test_validate_against_tool_rejects_manifest_secret_widening(self) -> None:
        registry = ToolManifestRegistry()
        registry.register(_manifest(secrets_allowed=True))

        with self.assertRaises(ToolManifestRegistryError):
            registry.validate_against_tool(EchoTool())

    def test_load_directory_loads_multiple_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "one.json").write_text(json.dumps(_manifest_json("one")), encoding="utf-8")
            (path / "two.json").write_text(json.dumps(_manifest_json("two")), encoding="utf-8")
            registry = ToolManifestRegistry()

            registry.load_directory(path)

            self.assertEqual(registry.names(), ["one", "two"])

    def test_to_tool_specs_returns_tool_specs(self) -> None:
        registry = ToolManifestRegistry()
        registry.register(_manifest(egress_methods=("GET", "PUT"), rollback_egress_methods=("PUT",)))

        specs = registry.to_tool_specs()

        self.assertEqual(specs["echo"].name, "echo")
        self.assertEqual(tuple(specs["echo"].egress_methods), ("GET", "PUT"))
        self.assertEqual(tuple(specs["echo"].rollback_egress_methods), ("PUT",))

    def test_secrets_allowed_defaults_to_false_if_absent(self) -> None:
        manifest = _manifest_json("echo")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "echo.json").write_text(json.dumps(manifest), encoding="utf-8")
            registry = ToolManifestRegistry()

            registry.load_directory(path)

            self.assertFalse(registry.get("echo").secrets_allowed)


if __name__ == "__main__":
    unittest.main()
