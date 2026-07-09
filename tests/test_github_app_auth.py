"""Tests for GitHub App installation-token authentication."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent import github_app_auth
from leos_agent.github_app_auth import (
    GitHubAppAuthError,
    GitHubAppTokenProvider,
    GitHubAppUnavailable,
    resolve_github_credential,
)
from leos_agent.github_client import GitHubHTTPResponse
from leos_agent.tools import Secret

_FAKE_PEM = "-----BEGIN PRIVATE KEY-----\nfake-key-material-not-a-real-key\n-----END PRIVATE KEY-----\n"


class _FakeTransport:
    """Records exchange requests and returns configurable responses."""

    def __init__(self, responses: list[GitHubHTTPResponse] | None = None) -> None:
        self.requests: list[dict[str, object]] = []
        self._responses = responses or []

    def request(self, method, url, *, headers, body, timeout_seconds):  # noqa: ANN001, ANN201
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if self._responses:
            return self._responses.pop(0)
        return _token_response("installation-token-1", "2026-01-01T01:00:00Z")


def _token_response(token: str, expires_at: str) -> GitHubHTTPResponse:
    return GitHubHTTPResponse(
        status_code=201,
        body=json.dumps({"token": token, "expires_at": expires_at}).encode("utf-8"),
    )


def _fake_encoder_calls() -> tuple[list[dict[str, object]], object]:
    calls: list[dict[str, object]] = []

    def encoder(claims, private_key):  # noqa: ANN001, ANN202
        calls.append({"claims": dict(claims), "key": private_key})
        return f"fake-jwt-for-{claims['iss']}"

    return calls, encoder


def _provider(**kwargs: object) -> GitHubAppTokenProvider:
    defaults: dict[str, object] = {
        "app_id": "12345",
        "installation_id": "678",
        "private_key": Secret(_FAKE_PEM),
        "transport": _FakeTransport(),
        "clock": lambda: 1_000_000.0,
    }
    defaults.update(kwargs)
    if "jwt_encoder" not in defaults:
        defaults["jwt_encoder"] = _fake_encoder_calls()[1]
    return GitHubAppTokenProvider(**defaults)  # type: ignore[arg-type]


class JwtConstructionTests(unittest.TestCase):
    def test_claims_use_backdated_iat_and_capped_exp(self) -> None:
        calls, encoder = _fake_encoder_calls()
        provider = _provider(jwt_encoder=encoder)
        provider.token()
        self.assertEqual(len(calls), 1)
        claims = calls[0]["claims"]
        self.assertEqual(claims["iss"], "12345")
        self.assertEqual(claims["iat"], int(1_000_000.0 - 60))
        self.assertEqual(claims["exp"], int(1_000_000.0 + 540))
        self.assertEqual(calls[0]["key"], _FAKE_PEM)

    def test_missing_pyjwt_raises_typed_error_naming_the_extra(self) -> None:
        provider = _provider(jwt_encoder=None)
        with mock.patch.dict(sys.modules, {"jwt": None}), self.assertRaises(GitHubAppUnavailable) as ctx:
            provider.token()
        self.assertIn("github-app", str(ctx.exception))


class TokenExchangeTests(unittest.TestCase):
    def test_exchange_posts_jwt_to_installation_endpoint(self) -> None:
        transport = _FakeTransport()
        provider = _provider(transport=transport)
        token = provider.token()
        self.assertEqual(token.unwrap(), "installation-token-1")
        request = transport.requests[0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], "https://api.github.com/app/installations/678/access_tokens")
        self.assertEqual(request["headers"]["Authorization"], "Bearer fake-jwt-for-12345")
        self.assertIsNone(request["body"])

    def test_non_201_raises_without_echoing_the_body(self) -> None:
        transport = _FakeTransport([GitHubHTTPResponse(status_code=401, body=b"Bearer nope secret-material")])
        provider = _provider(transport=transport)
        with self.assertRaises(GitHubAppAuthError) as ctx:
            provider.token()
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("secret-material", str(ctx.exception))

    def test_malformed_response_raises(self) -> None:
        transport = _FakeTransport([GitHubHTTPResponse(status_code=201, body=b"not-json")])
        provider = _provider(transport=transport)
        with self.assertRaises(GitHubAppAuthError):
            provider.token()


class TokenCacheTests(unittest.TestCase):
    def test_token_is_cached_until_close_to_expiry(self) -> None:
        clock = [1_000_000.0]
        transport = _FakeTransport(
            [
                _token_response("token-a", "2026-01-01T01:00:00Z"),
                _token_response("token-b", "2026-01-01T02:00:00Z"),
            ]
        )
        provider = _provider(transport=transport, clock=lambda: clock[0])
        expires = github_app_auth._parse_expires_at("2026-01-01T01:00:00Z", fallback=0.0)
        clock[0] = expires - 3600.0
        first = provider.token()
        second = provider.token()
        self.assertIs(first, second)
        self.assertEqual(len(transport.requests), 1)
        # Within the refresh skew of expiry: a new token is minted.
        clock[0] = expires - 30.0
        third = provider.token()
        self.assertEqual(third.unwrap(), "token-b")
        self.assertEqual(len(transport.requests), 2)


class PrivateKeyLeakTests(unittest.TestCase):
    def test_key_material_never_appears_in_repr_or_errors(self) -> None:
        transport = _FakeTransport([GitHubHTTPResponse(status_code=500, body=b"boom")])
        provider = _provider(transport=transport)
        self.assertNotIn("fake-key-material", repr(provider))
        self.assertNotIn("fake-key-material", str(vars(provider)))
        try:
            provider.token()
        except GitHubAppAuthError as exc:
            self.assertNotIn("fake-key-material", str(exc))
            self.assertNotIn("fake-key-material", repr(exc))


class FromEnvTests(unittest.TestCase):
    def test_missing_variables_are_named(self) -> None:
        with (
            mock.patch.dict(os.environ, {"LEOS_GITHUB_APP_ID": "1"}, clear=True),
            self.assertRaises(GitHubAppAuthError) as ctx,
        ):
            GitHubAppTokenProvider.from_env()
        message = str(ctx.exception)
        self.assertIn("LEOS_GITHUB_APP_INSTALLATION_ID", message)
        self.assertIn("LEOS_GITHUB_APP_PRIVATE_KEY_PATH", message)

    def test_reads_private_key_from_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "app.pem"
            key_path.write_text(_FAKE_PEM, encoding="utf-8")
            env = {
                "LEOS_GITHUB_APP_ID": "12345",
                "LEOS_GITHUB_APP_INSTALLATION_ID": "678",
                "LEOS_GITHUB_APP_PRIVATE_KEY_PATH": str(key_path),
            }
            with mock.patch.dict(os.environ, env, clear=True):
                provider = GitHubAppTokenProvider.from_env(transport=_FakeTransport())
        self.assertEqual(provider.app_id, "12345")

    def test_unreadable_key_file_raises_without_path_contents(self) -> None:
        env = {
            "LEOS_GITHUB_APP_ID": "12345",
            "LEOS_GITHUB_APP_INSTALLATION_ID": "678",
            "LEOS_GITHUB_APP_PRIVATE_KEY_PATH": "/nonexistent/app.pem",
        }
        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(GitHubAppAuthError):
            GitHubAppTokenProvider.from_env()


class ResolveCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        github_app_auth._provider_cache.clear()

    def test_explicit_pat_wins_over_app(self) -> None:
        def factory(env):  # noqa: ANN001, ANN202
            raise AssertionError("App provider must not be constructed when a PAT is set")

        env = {"LEOS_GITHUB_TOKEN": "pat-value", "LEOS_GITHUB_APP_ID": "1"}
        credential = resolve_github_credential(env=env, provider_factory=factory)
        assert credential is not None
        self.assertEqual(credential.unwrap(), "pat-value")

    def test_full_app_configuration_mints_installation_token(self) -> None:
        class _StubProvider:
            def token(self) -> Secret:
                return Secret("installation-token-2")

        env = {
            "LEOS_GITHUB_APP_ID": "12345",
            "LEOS_GITHUB_APP_INSTALLATION_ID": "678",
            "LEOS_GITHUB_APP_PRIVATE_KEY_PATH": "/tmp/app.pem",
        }
        credential = resolve_github_credential(env=env, provider_factory=lambda env_arg: _StubProvider())
        assert credential is not None
        self.assertEqual(credential.unwrap(), "installation-token-2")

    def test_partial_app_configuration_raises_instead_of_falling_back(self) -> None:
        env = {"LEOS_GITHUB_APP_ID": "12345"}
        with self.assertRaises(GitHubAppAuthError):
            resolve_github_credential(env=env)

    def test_no_credential_required_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            resolve_github_credential(env={})

    def test_no_credential_optional_returns_none(self) -> None:
        self.assertIsNone(resolve_github_credential(env={}, required=False))

    def test_provider_is_cached_across_calls(self) -> None:
        constructed: list[object] = []

        class _StubProvider:
            def token(self) -> Secret:
                return Secret("installation-token-3")

        def factory(env):  # noqa: ANN001, ANN202
            provider = _StubProvider()
            constructed.append(provider)
            return provider

        env = {
            "LEOS_GITHUB_APP_ID": "12345",
            "LEOS_GITHUB_APP_INSTALLATION_ID": "678",
            "LEOS_GITHUB_APP_PRIVATE_KEY_PATH": "/tmp/app.pem",
        }
        resolve_github_credential(env=env, provider_factory=factory)
        resolve_github_credential(env=env, provider_factory=factory)
        self.assertEqual(len(constructed), 1)


def _have_real_jwt() -> bool:
    try:
        import jwt  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(_have_real_jwt(), "requires the optional 'github-app' extra (PyJWT + cryptography)")
class RealJwtEncodingTests(unittest.TestCase):
    def test_default_encoder_produces_verifiable_rs256_jwt(self) -> None:
        import jwt as pyjwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("utf-8")
        transport = _FakeTransport()
        provider = GitHubAppTokenProvider(
            app_id="12345",
            installation_id="678",
            private_key=Secret(pem),
            transport=transport,
            clock=lambda: 1_000_000.0,
        )
        provider.token()
        authorization = str(transport.requests[0]["headers"]["Authorization"])
        encoded = authorization.removeprefix("Bearer ")
        decoded = pyjwt.decode(
            encoded,
            key.public_key(),
            algorithms=["RS256"],
            options={"verify_exp": False, "verify_iat": False},
        )
        self.assertEqual(decoded["iss"], "12345")
        self.assertEqual(pyjwt.get_unverified_header(encoded)["alg"], "RS256")


if __name__ == "__main__":
    unittest.main()
