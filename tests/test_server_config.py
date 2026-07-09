"""Tests for the unified server configuration loader and `leos serve`."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent.server import ServerConfigurationError
from leos_agent.server.config import ServerConfig, load_server_config, summarize_config

_KEY = "k" * 40


def _load(toml_text: str | None = None, env: dict[str, str] | None = None, **overrides: object) -> ServerConfig:
    env = dict(env or {})
    if toml_text is None:
        with mock.patch.dict(os.environ, env, clear=True):
            return load_server_config(overrides=overrides)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "leos-server.toml"
        path.write_text(toml_text, encoding="utf-8")
        with mock.patch.dict(os.environ, env, clear=True):
            return load_server_config(path=path, overrides=overrides)


class LoadServerConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = _load()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8080)
        self.assertEqual(config.workers, 1)
        self.assertEqual(config.data_dir, Path("leos-data"))
        self.assertIsNone(config.inbox_dir)

    def test_toml_values_apply(self) -> None:
        config = _load('host = "0.0.0.0"\nport = 9000\nworkers = 2\ndata_dir = "/data"\ninbox_dir = "/inbox"\n')
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.workers, 2)
        self.assertEqual(config.data_dir, Path("/data"))
        self.assertEqual(config.inbox_dir, Path("/inbox"))

    def test_env_overrides_toml(self) -> None:
        config = _load("port = 9000\n", env={"LEOS_SERVER_PORT": "9100"})
        self.assertEqual(config.port, 9100)

    def test_explicit_overrides_beat_env_and_toml(self) -> None:
        config = _load("port = 9000\n", env={"LEOS_SERVER_PORT": "9100"}, port=9200)
        self.assertEqual(config.port, 9200)

    def test_config_path_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.toml"
            path.write_text("port = 9001\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"LEOS_SERVER_CONFIG": str(path)}, clear=True):
                config = load_server_config()
        self.assertEqual(config.port, 9001)

    def test_missing_explicit_config_file_fails(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ServerConfigurationError):
            load_server_config(path=Path("/nonexistent/leos-server.toml"))

    def test_unknown_toml_key_fails(self) -> None:
        with self.assertRaises(ServerConfigurationError) as ctx:
            _load("bogus_key = 1\n")
        self.assertIn("bogus_key", str(ctx.exception))

    def test_secret_in_toml_fails_closed(self) -> None:
        for key in ("api_key", "leos_server_api_key", "approval_hmac_secret", "github_token"):
            with self.subTest(key=key), self.assertRaises(ServerConfigurationError) as ctx:
                _load(f'{key} = "supersecret"\n')
            self.assertIn("environment", str(ctx.exception))
            self.assertNotIn("supersecret", str(ctx.exception))

    def test_invalid_port_fails(self) -> None:
        with self.assertRaises(ServerConfigurationError):
            _load("port = 0\n")
        with self.assertRaises(ServerConfigurationError):
            _load(env={"LEOS_SERVER_PORT": "not-a-number"})

    def test_invalid_workers_fails(self) -> None:
        with self.assertRaises(ServerConfigurationError):
            _load("workers = 0\n")


class SummarizeConfigTests(unittest.TestCase):
    def test_summary_reports_presence_not_values(self) -> None:
        config = _load()
        with mock.patch.dict(
            os.environ,
            {"LEOS_SERVER_API_KEY": _KEY, "LEOS_APPROVAL_HMAC_SECRET": "hmacsecretvalue"},
            clear=True,
        ):
            summary = summarize_config(config)
        self.assertIn("api_key: set", summary)
        self.assertIn("approval_hmac_secret: set", summary)
        self.assertNotIn(_KEY, summary)
        self.assertNotIn("hmacsecretvalue", summary)

    def test_summary_reports_missing_secrets(self) -> None:
        config = _load()
        with mock.patch.dict(os.environ, {}, clear=True):
            summary = summarize_config(config)
        self.assertIn("api_key: MISSING", summary)


def _run_cli(*argv: str) -> int:
    from leos_agent.cli import main

    with mock.patch("sys.argv", ["leos", *argv]):
        return main()


class ServeCliTests(unittest.TestCase):
    def test_serve_check_validates_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"LEOS_SERVER_API_KEY": _KEY}
            with mock.patch.dict(os.environ, env, clear=True):
                code = _run_cli("serve", "--data-dir", tmp, "--check")
        self.assertEqual(code, 0)

    def test_serve_check_fails_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            code = _run_cli("serve", "--data-dir", tmp, "--check")
        self.assertNotEqual(code, 0)

    def test_serve_without_uvicorn_names_the_extra(self) -> None:
        from leos_agent.server.run import run_server

        config = _load(env={"LEOS_SERVER_API_KEY": _KEY})
        with (
            mock.patch.dict(os.environ, {"LEOS_SERVER_API_KEY": _KEY}, clear=True),
            mock.patch.dict(sys.modules, {"uvicorn": None}),
            self.assertRaises(Exception) as ctx,
        ):
            run_server(config)
        self.assertIn("server", str(ctx.exception))

    def test_run_server_invokes_uvicorn_with_config(self) -> None:
        from leos_agent.server.run import run_server

        fake_uvicorn = mock.MagicMock()
        config = _load(env={"LEOS_SERVER_API_KEY": _KEY}, port=9300, host="0.0.0.0")
        with (
            mock.patch.dict(os.environ, {"LEOS_SERVER_API_KEY": _KEY}, clear=True),
            mock.patch.dict(sys.modules, {"uvicorn": fake_uvicorn}),
        ):
            code = run_server(config)
        self.assertEqual(code, 0)
        _args, kwargs = fake_uvicorn.run.call_args
        self.assertEqual(kwargs["host"], "0.0.0.0")
        self.assertEqual(kwargs["port"], 9300)
        self.assertEqual(kwargs["workers"], 1)
        self.assertTrue(kwargs["factory"])


if __name__ == "__main__":
    unittest.main()
