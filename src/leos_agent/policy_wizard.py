"""Deny-by-default policy profile generator behind ``leos policy init``.

The wizard produces a JSON policy configuration that passes
``validate_policy_config`` and starts from the most restrictive posture:
nothing is granted, every ungrated permission is explicitly denied, network is
default-deny, signed approval is required, and all fail-closed profile checks
are on. Users opt *in* to tools and permissions; they never opt out of gates.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .enums import Permission, RiskLevel
from .policy import validate_policy_config

# Permissions that cause external effects; granting one automatically puts it
# behind human approval.
_CONSEQUENTIAL = (
    Permission.WRITE_MEMORY,
    Permission.WRITE_FILES,
    Permission.NETWORK,
    Permission.SEND_MESSAGE,
    Permission.EXECUTE_CODE,
    Permission.DELETE,
    Permission.FINANCIAL,
    Permission.SYSTEM_CONFIG,
)


def generate_policy_config(
    *,
    name: str,
    allowed_tools: Sequence[str] = (),
    granted_permissions: Sequence[str] = (),
    max_auto_risk: str = "low",
    egress_hosts: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a deny-first policy configuration dictionary.

    Raises ``ValueError`` for an empty name, unknown permission, or unknown
    risk level. The result always passes ``validate_policy_config``.
    """
    if not name.strip():
        raise ValueError("profile name must be non-empty")
    try:
        granted = tuple(Permission(value) for value in granted_permissions)
    except ValueError as exc:
        raise ValueError(f"unknown permission: {exc}") from exc
    try:
        risk = RiskLevel(max_auto_risk)
    except ValueError as exc:
        raise ValueError(f"unknown risk level: {max_auto_risk}") from exc
    denied = tuple(permission for permission in Permission if permission not in granted)
    require_human = tuple(permission for permission in granted if permission in _CONSEQUENTIAL)
    config: dict[str, Any] = {
        "name": name.strip(),
        "granted_permissions": [permission.value for permission in granted],
        "deny_permissions": [permission.value for permission in denied],
        "require_human_for": [permission.value for permission in require_human],
        "max_auto_risk": risk.value,
        "allowed_tools": [str(tool) for tool in allowed_tools],
        "network_default_deny": True,
        "require_signed_approval": True,
        "require_typed_goal_criteria": True,
        "require_strong_sandbox_for_execute": True,
        "require_causal_contract_for_medium_risk": True,
        "require_timeout_for_medium_risk": True,
        "require_output_schema_for_medium_risk": True,
    }
    if egress_hosts:
        config["egress_policy"] = {"allowed_hosts": [str(host) for host in egress_hosts]}
    issues = validate_policy_config(config)
    if issues:  # pragma: no cover - defensive; generation must stay self-consistent
        raise ValueError(f"generated config failed validation: {issues}")
    return config


def run_policy_init(
    *,
    name: str | None,
    allow_tools: Sequence[str],
    grants: Sequence[str],
    max_auto_risk: str,
    egress_hosts: Sequence[str],
    output: Path,
    non_interactive: bool,
    input_fn: Callable[[str], str] | None = None,
    print_fn: Callable[[str], None] = print,
) -> int:
    """Drive `leos policy init`. Returns a process exit code."""
    if input_fn is None:
        input_fn = input  # resolved at call time so tests can patch builtins.input
    if output.exists():
        print_fn(f"Error: refusing to overwrite existing file: {output}")
        return 1
    tools = list(allow_tools)
    permissions = list(grants)
    hosts = list(egress_hosts)
    if not non_interactive:
        if name is None:
            name = input_fn("Profile name: ")
        if not tools:
            tools = _split(input_fn("Allowed tools (comma-separated, empty = none): "))
        if not permissions:
            permissions = _split(
                input_fn("Granted permissions (comma-separated, empty = none; consequential ones require approval): ")
            )
        if not hosts:
            hosts = _split(input_fn("Allowed egress hosts (comma-separated, empty = deny all): "))
    if name is None:
        print_fn("Error: --name is required with --non-interactive")
        return 1
    try:
        config = generate_policy_config(
            name=name,
            allowed_tools=tools,
            granted_permissions=permissions,
            max_auto_risk=max_auto_risk,
            egress_hosts=hosts,
        )
    except ValueError as exc:
        print_fn(f"Error: {exc}")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print_fn(f"Wrote deny-by-default policy profile '{config['name']}' to {output}")
    print_fn("Review the file, then validate with: leos validate-policy " + str(output))
    return 0


def _split(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]
