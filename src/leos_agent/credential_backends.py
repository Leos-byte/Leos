"""Managed :class:`~leos_agent.credentials.CredentialVault` backends.

These implement the same ``put``/``get``/``revoke``/``has`` protocol as the
in-memory dev vault but persist secrets in an OS keychain, an encrypted-at-rest
env file, or a managed secrets service (HashiCorp Vault). A shared base enforces
the scope, expiry, and revocation invariants uniformly; subclasses only provide
raw envelope storage. Optional dependencies (``keyring``, ``hvac``) are imported
lazily and surface a typed error when absent.

Secret material lives only inside the backing store as an envelope; it never
enters a :class:`SecretHandle` (which is a reference only) or any log/audit path.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from .credentials import (
    CredentialError,
    CredentialExpiredError,
    CredentialRevokedError,
    CredentialScopeError,
    SecretHandle,
)
from .tools import Secret


class CredentialBackendUnavailable(CredentialError):
    """Raised when an optional credential backend dependency is missing."""


def _make_envelope(secret: Secret, scope: str, expires_at: float | None) -> str:
    return json.dumps({"value": secret.unwrap(), "scope": scope, "created_at": time.time(), "expires_at": expires_at})


class _BaseCredentialVault:
    """Base vault enforcing scope/expiry/revocation over an envelope store.

    Subclasses implement ``_write``/``_read``/``_delete`` for a single JSON
    envelope keyed by ``handle_id``.
    """

    def _write(self, handle_id: str, envelope: str) -> None:
        raise NotImplementedError

    def _read(self, handle_id: str) -> str | None:
        raise NotImplementedError

    def _delete(self, handle_id: str) -> None:
        raise NotImplementedError

    def put(self, secret: Secret, *, scope: str, expires_at: float | None = None) -> SecretHandle:
        if not scope:
            raise CredentialScopeError("Credential scope must be non-empty")
        handle_id = str(uuid.uuid4())
        self._write(handle_id, _make_envelope(secret, scope, expires_at))
        return SecretHandle(handle_id=handle_id, scope=scope, expires_at=expires_at)

    def get(self, handle: SecretHandle, *, scope: str) -> Secret:
        raw = self._read(handle.handle_id)
        if raw is None:
            raise CredentialRevokedError("Credential handle is missing or revoked")
        envelope = json.loads(raw)
        if envelope.get("scope") != scope or handle.scope != scope:
            raise CredentialScopeError("Credential handle scope mismatch")
        expires_at = envelope.get("expires_at")
        if expires_at is not None and time.time() > float(expires_at):
            raise CredentialExpiredError("Credential handle has expired")
        return Secret(str(envelope["value"]))

    def revoke(self, handle: SecretHandle) -> None:
        self._delete(handle.handle_id)

    def has(self, handle: SecretHandle) -> bool:
        return self._read(handle.handle_id) is not None


class EnvFileCredentialVault(_BaseCredentialVault):
    """Vault persisting envelopes to a private JSON file (chmod 600).

    Suitable for single-host development/staging. Not a managed KMS.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save({})

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise CredentialError("env credential file is corrupt")
        return {str(k): str(v) for k, v in data.items()}

    def _save(self, data: dict[str, str]) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.path.chmod(0o600)

    def _write(self, handle_id: str, envelope: str) -> None:
        data = self._load()
        data[handle_id] = envelope
        self._save(data)

    def _read(self, handle_id: str) -> str | None:
        return self._load().get(handle_id)

    def _delete(self, handle_id: str) -> None:
        data = self._load()
        data.pop(handle_id, None)
        self._save(data)


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class KeyringCredentialVault(_BaseCredentialVault):
    """Vault backed by an OS keychain via the optional ``keyring`` package.

    A ``backend`` may be injected for testing; otherwise ``keyring`` is imported
    lazily and used as the backend.
    """

    def __init__(self, *, service: str = "leos-agent", backend: KeyringBackend | None = None) -> None:
        self.service = service
        self._backend = backend if backend is not None else _load_keyring()

    def _write(self, handle_id: str, envelope: str) -> None:
        self._backend.set_password(self.service, handle_id, envelope)

    def _read(self, handle_id: str) -> str | None:
        return self._backend.get_password(self.service, handle_id)

    def _delete(self, handle_id: str) -> None:
        # Deleting a missing key is not an error.
        with suppress(Exception):
            self._backend.delete_password(self.service, handle_id)


class VaultKVClient(Protocol):
    """Minimal subset of an ``hvac`` KV v2 client used by the vault."""

    def read_secret(self, handle_id: str) -> str | None: ...

    def write_secret(self, handle_id: str, envelope: str) -> None: ...

    def delete_secret(self, handle_id: str) -> None: ...


class VaultCredentialVault(_BaseCredentialVault):
    """Vault backed by HashiCorp Vault (KV v2) via the optional ``hvac`` package.

    Pass a ready ``client`` implementing :class:`VaultKVClient` (recommended, and
    used for testing), or ``url``/``token`` to construct an ``hvac`` client
    lazily. The chosen KMS-grade backend for the first production credential
    store; AWS/GCP KMS envelope vaults can extend :class:`_BaseCredentialVault`
    the same way.
    """

    def __init__(
        self,
        *,
        client: VaultKVClient | None = None,
        url: str | None = None,
        token: str | None = None,
        mount_point: str = "secret",
        base_path: str = "leos",
    ) -> None:
        if client is not None:
            self._client: VaultKVClient = client
        else:
            self._client = _HvacKVAdapter(url=url, token=token, mount_point=mount_point, base_path=base_path)

    def _write(self, handle_id: str, envelope: str) -> None:
        self._client.write_secret(handle_id, envelope)

    def _read(self, handle_id: str) -> str | None:
        return self._client.read_secret(handle_id)

    def _delete(self, handle_id: str) -> None:
        self._client.delete_secret(handle_id)


def _load_keyring() -> KeyringBackend:
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - exercised only without keyring installed
        raise CredentialBackendUnavailable("KeyringCredentialVault requires the optional 'keyring' package") from exc
    return keyring  # type: ignore[no-any-return]


class _HvacKVAdapter:
    """Adapts an ``hvac`` client to the small :class:`VaultKVClient` surface."""

    def __init__(self, *, url: str | None, token: str | None, mount_point: str, base_path: str) -> None:
        try:
            import hvac
        except ImportError as exc:  # pragma: no cover - exercised only without hvac installed
            raise CredentialBackendUnavailable("VaultCredentialVault requires the optional 'hvac' package") from exc
        self._hvac = hvac.Client(url=url, token=token)  # pragma: no cover - needs a live Vault server
        self._mount_point = mount_point  # pragma: no cover
        self._base_path = base_path  # pragma: no cover

    def _path(self, handle_id: str) -> str:  # pragma: no cover - needs a live Vault server
        return f"{self._base_path}/{handle_id}"

    def read_secret(self, handle_id: str) -> str | None:  # pragma: no cover - needs a live Vault server
        try:
            resp = self._hvac.secrets.kv.v2.read_secret_version(
                path=self._path(handle_id), mount_point=self._mount_point
            )
        except Exception:  # noqa: BLE001
            return None
        envelope: str | None = resp["data"]["data"].get("envelope")
        return envelope

    def write_secret(self, handle_id: str, envelope: str) -> None:  # pragma: no cover - needs a live Vault server
        self._hvac.secrets.kv.v2.create_or_update_secret(
            path=self._path(handle_id), secret={"envelope": envelope}, mount_point=self._mount_point
        )

    def delete_secret(self, handle_id: str) -> None:  # pragma: no cover - needs a live Vault server
        self._hvac.secrets.kv.v2.delete_metadata_and_all_versions(
            path=self._path(handle_id), mount_point=self._mount_point
        )
