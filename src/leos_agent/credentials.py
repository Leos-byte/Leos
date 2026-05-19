"""Credential handle and development vault abstractions."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from .errors import LeosError
from .tools import Secret


class CredentialError(LeosError):
    """Base class for credential vault failures."""


class CredentialScopeError(CredentialError):
    """Credential handle was used outside its allowed scope."""


class CredentialExpiredError(CredentialError):
    """Credential handle has expired."""


class CredentialRevokedError(CredentialError):
    """Credential handle was revoked or is missing."""


@dataclass(frozen=True)
class SecretHandle:
    """Serializable reference to a secret value stored in a vault."""

    handle_id: str
    scope: str
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def __repr__(self) -> str:
        return f"SecretHandle(handle_id={self.handle_id!r}, scope={self.scope!r})"

    def __str__(self) -> str:
        return self.handle_id

    def to_dict(self) -> dict[str, object]:
        return {
            "handle_id": self.handle_id,
            "scope": self.scope,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SecretHandle:
        created_at = data.get("created_at", time.time())
        expires_at = data.get("expires_at")
        return cls(
            handle_id=str(data["handle_id"]),
            scope=str(data["scope"]),
            created_at=float(created_at) if isinstance(created_at, (int, float, str)) else time.time(),
            expires_at=float(expires_at) if isinstance(expires_at, (int, float, str)) else None,
        )


class CredentialVault(Protocol):
    def put(self, secret: Secret, *, scope: str, expires_at: float | None = None) -> SecretHandle: ...

    def get(self, handle: SecretHandle, *, scope: str) -> Secret: ...

    def revoke(self, handle: SecretHandle) -> None: ...

    def has(self, handle: SecretHandle) -> bool: ...


class InMemoryCredentialVault:
    """Development-only vault that stores secrets in process memory."""

    def __init__(self) -> None:
        self._secrets: dict[str, Secret] = {}
        self._handles: dict[str, SecretHandle] = {}
        self._revoked: set[str] = set()

    def __repr__(self) -> str:
        return f"InMemoryCredentialVault(handles={len(self._handles)}, revoked={len(self._revoked)})"

    def put(self, secret: Secret, *, scope: str, expires_at: float | None = None) -> SecretHandle:
        if not scope:
            raise CredentialScopeError("Credential scope must be non-empty")
        handle = SecretHandle(handle_id=str(uuid.uuid4()), scope=scope, expires_at=expires_at)
        self._secrets[handle.handle_id] = secret
        self._handles[handle.handle_id] = handle
        return handle

    def get(self, handle: SecretHandle, *, scope: str) -> Secret:
        stored = self._handles.get(handle.handle_id)
        if stored is None or handle.handle_id in self._revoked:
            raise CredentialRevokedError("Credential handle is missing or revoked")
        if stored.scope != scope or handle.scope != scope:
            raise CredentialScopeError("Credential handle scope mismatch")
        if stored.expires_at is not None and time.time() > stored.expires_at:
            raise CredentialExpiredError("Credential handle has expired")
        return self._secrets[handle.handle_id]

    def revoke(self, handle: SecretHandle) -> None:
        self._revoked.add(handle.handle_id)
        self._secrets.pop(handle.handle_id, None)

    def has(self, handle: SecretHandle) -> bool:
        return handle.handle_id in self._handles and handle.handle_id not in self._revoked
