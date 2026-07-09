"""Unified configuration for the Leos HTTP service.

Precedence: built-in defaults < ``leos-server.toml`` < ``LEOS_SERVER_*``
environment variables < explicit overrides (CLI flags).

Secrets are deliberately not part of this configuration. The API key, approval
HMAC secret, and GitHub token are read from the process environment (or a
``CredentialVault`` backend) by the server itself; any secret-shaped key in
the TOML file fails startup so plaintext credentials can never land on disk
via configuration.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .app import ServerConfigurationError

DEFAULT_CONFIG_FILENAME = "leos-server.toml"
CONFIG_PATH_ENV = "LEOS_SERVER_CONFIG"

_SECRET_TOML_KEYS = frozenset(
    {
        "api_key",
        "leos_server_api_key",
        "approval_hmac_secret",
        "leos_approval_hmac_secret",
        "github_token",
        "leos_github_token",
        "token",
        "secret",
        "hmac_secret",
        "password",
    }
)
_ALLOWED_TOML_KEYS = frozenset({"host", "port", "workers", "data_dir", "inbox_dir"})

_SECRET_ENV_VARS = (
    ("api_key", "LEOS_SERVER_API_KEY", True),
    ("approval_hmac_secret", "LEOS_APPROVAL_HMAC_SECRET", False),
    ("github_token", "LEOS_GITHUB_TOKEN", False),
)


@dataclass(frozen=True)
class ServerConfig:
    """Non-secret runtime settings for ``leos serve``."""

    host: str = "127.0.0.1"
    port: int = 8080
    workers: int = 1
    data_dir: Path = Path("leos-data")
    inbox_dir: Path | None = None


def load_server_config(
    path: Path | None = None,
    *,
    overrides: Mapping[str, object] | None = None,
) -> ServerConfig:
    """Load and validate the server configuration.

    ``path`` (or ``LEOS_SERVER_CONFIG``) names the TOML file; when neither is
    given, ``leos-server.toml`` in the working directory is used if present.
    """
    values: dict[str, Any] = {}
    explicit_path = path or (Path(os.environ[CONFIG_PATH_ENV]) if os.environ.get(CONFIG_PATH_ENV) else None)
    toml_path = explicit_path or Path(DEFAULT_CONFIG_FILENAME)
    if explicit_path is not None and not toml_path.exists():
        raise ServerConfigurationError(f"config file not found: {toml_path}")
    if toml_path.exists():
        values.update(_read_toml(toml_path))

    env = os.environ
    for key, env_name in (
        ("host", "LEOS_SERVER_HOST"),
        ("port", "LEOS_SERVER_PORT"),
        ("workers", "LEOS_SERVER_WORKERS"),
        ("data_dir", "LEOS_SERVER_DATA_DIR"),
        ("inbox_dir", "LEOS_SERVER_INBOX_DIR"),
    ):
        if env.get(env_name):
            values[key] = env[env_name]

    for key, value in (overrides or {}).items():
        if value is not None:
            values[key] = value

    return _validate(values)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        tomllib = importlib.import_module("tomllib")
    except ImportError:
        try:
            tomllib = importlib.import_module("tomli")
        except ImportError as exc:
            raise ServerConfigurationError(
                "reading leos-server.toml requires Python 3.11+ or the optional 'tomli' package "
                "(installed with the 'server' extra)"
            ) from exc
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise ServerConfigurationError(f"could not read {path.name}: {type(exc).__name__}") from exc
    if not isinstance(document, dict):
        raise ServerConfigurationError(f"{path.name} must contain a TOML table")

    lowered = {str(key).lower(): value for key, value in document.items()}
    secret_keys = sorted(set(lowered) & _SECRET_TOML_KEYS)
    if secret_keys:
        raise ServerConfigurationError(
            f"secret configuration ({', '.join(secret_keys)}) must come from the environment "
            "or a credential vault, never the TOML file; refusing to start"
        )
    unknown = sorted(set(lowered) - _ALLOWED_TOML_KEYS)
    if unknown:
        raise ServerConfigurationError(f"unknown configuration key(s) in {path.name}: {', '.join(unknown)}")
    return lowered


def _validate(values: Mapping[str, Any]) -> ServerConfig:
    host = str(values.get("host", "127.0.0.1"))
    if not host.strip():
        raise ServerConfigurationError("host must not be empty")
    port = _int_value("port", values.get("port", 8080))
    if not 1 <= port <= 65535:
        raise ServerConfigurationError("port must be between 1 and 65535")
    workers = _int_value("workers", values.get("workers", 1))
    if workers < 1:
        raise ServerConfigurationError("workers must be at least 1")
    data_dir = Path(str(values.get("data_dir", "leos-data")))
    inbox_value = values.get("inbox_dir")
    inbox_dir = Path(str(inbox_value)) if inbox_value else None
    return ServerConfig(host=host, port=port, workers=workers, data_dir=data_dir, inbox_dir=inbox_dir)


def _int_value(name: str, value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ServerConfigurationError(f"{name} must be an integer") from exc


def summarize_config(config: ServerConfig) -> str:
    """Human-readable startup summary; secret values never appear."""
    lines = [
        "leos server configuration:",
        f"  host: {config.host}",
        f"  port: {config.port}",
        f"  workers: {config.workers}",
        f"  data_dir: {config.data_dir}",
        f"  inbox_dir: {config.inbox_dir or '(inbox disabled)'}",
    ]
    for label, env_name, required in _SECRET_ENV_VARS:
        present = bool(os.environ.get(env_name))
        state = "set" if present else ("MISSING (required)" if required else "unset")
        lines.append(f"  {label}: {state} (from {env_name})")
    return "\n".join(lines)
