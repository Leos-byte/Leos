"""GitHub App installation-token authentication (short-lived, auto-refreshed).

Implements the credential model recommended by
``docs/GETTING_STARTED_PRODUCTION_GITHUB.md``: a repository-installed GitHub
App whose short-lived installation tokens replace long-lived PATs. The
provider signs an RS256 App JWT, exchanges it for an installation token, and
caches the token until shortly before expiry. Tokens are returned wrapped in
:class:`~leos_agent.tools.Secret`, so the existing secret boundary applies
unchanged; the App private key never appears in logs, audit records, or
exception messages.

``PyJWT`` and ``cryptography`` are optional (extra ``github-app``); they are
imported lazily and their absence raises the typed
:class:`GitHubAppUnavailable` (mirroring ``SandboxUnavailable``). The token
exchange reuses the ``GitHubTransport`` protocol, so no request/error handling
in ``GitHubRESTClient`` changes.

Credential precedence (``resolve_github_credential``): an explicit
``LEOS_GITHUB_TOKEN`` (PAT) always wins; otherwise a fully configured App
(``LEOS_GITHUB_APP_ID``, ``LEOS_GITHUB_APP_INSTALLATION_ID``,
``LEOS_GITHUB_APP_PRIVATE_KEY_PATH``) is used; a partially configured App
fails loudly rather than silently falling back.
"""

from __future__ import annotations

import importlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .errors import LeosError
from .github_client import DEFAULT_BASE_URL, GitHubTransport, UrllibGitHubTransport
from .tools import Secret

APP_ID_ENV = "LEOS_GITHUB_APP_ID"
INSTALLATION_ID_ENV = "LEOS_GITHUB_APP_INSTALLATION_ID"
PRIVATE_KEY_PATH_ENV = "LEOS_GITHUB_APP_PRIVATE_KEY_PATH"
PAT_ENV = "LEOS_GITHUB_TOKEN"

_JWT_LIFETIME_SECONDS = 540.0  # GitHub caps App JWTs at 10 minutes; stay under it.
_JWT_BACKDATE_SECONDS = 60.0  # Tolerate clock skew against GitHub's servers.


class GitHubAppUnavailable(LeosError):
    """Raised when the optional PyJWT/cryptography dependencies are missing."""


class GitHubAppAuthError(LeosError):
    """Raised when App credentials are misconfigured or the exchange fails."""


class GitHubAppTokenProvider:
    """Mint and cache short-lived installation tokens for a GitHub App."""

    def __init__(
        self,
        *,
        app_id: str,
        installation_id: str,
        private_key: Secret,
        transport: GitHubTransport | None = None,
        base_url: str = DEFAULT_BASE_URL,
        clock: Callable[[], float] = time.time,
        jwt_encoder: Callable[[dict[str, float | str], str], str] | None = None,
        refresh_skew_seconds: float = 60.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not app_id or not installation_id:
            raise GitHubAppAuthError("app_id and installation_id are required")
        self.app_id = app_id
        self.installation_id = installation_id
        self._private_key = private_key
        self._transport = transport or UrllibGitHubTransport()
        self.base_url = base_url.rstrip("/")
        self._clock = clock
        self._jwt_encoder = jwt_encoder
        self.refresh_skew_seconds = refresh_skew_seconds
        self.timeout_seconds = timeout_seconds
        self._cached_token: Secret | None = None
        self._cached_expires_at: float = 0.0
        self._lock = threading.Lock()

    def __repr__(self) -> str:  # never expose key material
        return f"GitHubAppTokenProvider(app_id={self.app_id!r}, installation_id={self.installation_id!r})"

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> GitHubAppTokenProvider:
        env = env if env is not None else os.environ
        app_id = env.get(APP_ID_ENV, "")
        installation_id = env.get(INSTALLATION_ID_ENV, "")
        key_path = env.get(PRIVATE_KEY_PATH_ENV, "")
        missing = [
            name
            for name, value in (
                (APP_ID_ENV, app_id),
                (INSTALLATION_ID_ENV, installation_id),
                (PRIVATE_KEY_PATH_ENV, key_path),
            )
            if not value
        ]
        if missing:
            raise GitHubAppAuthError(f"incomplete GitHub App configuration; missing: {', '.join(missing)}")
        try:
            key_material = Path(key_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise GitHubAppAuthError(f"could not read the App private key file: {type(exc).__name__}") from exc
        return cls(
            app_id=app_id,
            installation_id=installation_id,
            private_key=Secret(key_material),
            **kwargs,  # type: ignore[arg-type]
        )

    def token(self) -> Secret:
        """Return a valid installation token, refreshing when close to expiry."""
        with self._lock:
            now = self._clock()
            if self._cached_token is not None and now < self._cached_expires_at - self.refresh_skew_seconds:
                return self._cached_token
            token_value, expires_at = self._exchange(now)
            self._cached_token = Secret(token_value)
            self._cached_expires_at = expires_at
            return self._cached_token

    def _app_jwt(self, now: float) -> str:
        claims: dict[str, float | str] = {
            "iat": int(now - _JWT_BACKDATE_SECONDS),
            "exp": int(now + _JWT_LIFETIME_SECONDS),
            "iss": self.app_id,
        }
        encoder = self._jwt_encoder or _pyjwt_encoder
        return encoder(claims, self._private_key.unwrap())

    def _exchange(self, now: float) -> tuple[str, float]:
        url = f"{self.base_url}/app/installations/{self.installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {self._app_jwt(now)}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "leos-agent/0.1",
        }
        response = self._transport.request(
            "POST", url, headers=headers, body=None, timeout_seconds=self.timeout_seconds
        )
        if response.status_code != 201:
            # Response bodies are not echoed: they can reflect header material.
            raise GitHubAppAuthError(f"installation token exchange failed with HTTP {response.status_code}")
        try:
            payload = json.loads(response.body.decode("utf-8"))
            token_value = str(payload["token"])
            expires_at = _parse_expires_at(str(payload["expires_at"]), fallback=now + 3300.0)
        except (ValueError, KeyError, UnicodeDecodeError) as exc:
            raise GitHubAppAuthError("installation token response was malformed") from exc
        if not token_value:
            raise GitHubAppAuthError("installation token response was malformed")
        return token_value, expires_at


def _pyjwt_encoder(claims: dict[str, float | str], private_key: str) -> str:
    try:
        pyjwt = importlib.import_module("jwt")
    except ImportError as exc:
        raise GitHubAppUnavailable(
            "GitHub App auth requires the optional 'github-app' extra: pip install 'leos-agent[github-app]'"
        ) from exc
    encoded = pyjwt.encode(claims, private_key, algorithm="RS256")
    return encoded if isinstance(encoded, str) else str(encoded, "utf-8")


def _parse_expires_at(value: str, *, fallback: float) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return fallback


_provider_lock = threading.Lock()
_provider_cache: dict[tuple[str, str, str], GitHubAppTokenProvider] = {}


def resolve_github_credential(
    *,
    required: bool = True,
    env: Mapping[str, str] | None = None,
    provider_factory: Callable[..., GitHubAppTokenProvider] = GitHubAppTokenProvider.from_env,
) -> Secret | None:
    """Resolve GitHub credentials: explicit PAT first, then GitHub App.

    A set ``LEOS_GITHUB_TOKEN`` always wins (documented precedence). With no
    PAT, a fully configured App mints a short-lived installation token; a
    partially configured App raises instead of silently degrading. Providers
    are cached per configuration so token caching survives across calls.
    """
    env = env if env is not None else os.environ
    pat = env.get(PAT_ENV)
    if pat:
        return Secret(pat)
    app_values = (env.get(APP_ID_ENV, ""), env.get(INSTALLATION_ID_ENV, ""), env.get(PRIVATE_KEY_PATH_ENV, ""))
    if any(app_values):
        cache_key = app_values
        with _provider_lock:
            provider = _provider_cache.get(cache_key)
            if provider is None:
                provider = provider_factory(env)
                _provider_cache[cache_key] = provider
        return provider.token()
    if required:
        raise ValueError("LEOS_GITHUB_TOKEN or a GitHub App configuration is required")
    return None
