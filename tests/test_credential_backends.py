"""Tests for managed CredentialVault backends."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from leos_agent.credential_backends import (
    CredentialBackendUnavailable,
    EnvFileCredentialVault,
    KeyringCredentialVault,
    VaultCredentialVault,
)
from leos_agent.credentials import (
    CredentialExpiredError,
    CredentialRevokedError,
    CredentialScopeError,
)
from leos_agent.tools import Secret


class _FakeKeyring:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


class _FakeVaultClient:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def read_secret(self, handle_id: str) -> str | None:
        return self._store.get(handle_id)

    def write_secret(self, handle_id: str, envelope: str) -> None:
        self._store[handle_id] = envelope

    def delete_secret(self, handle_id: str) -> None:
        self._store.pop(handle_id, None)


class CredentialVaultContract:
    def make_vault(self) -> Any:
        raise NotImplementedError

    def test_put_get_round_trip(self) -> None:
        vault = self.make_vault()
        handle = vault.put(Secret("s3cr3t"), scope="github")
        self.assertEqual(vault.get(handle, scope="github").unwrap(), "s3cr3t")

    def test_empty_scope_rejected(self) -> None:
        vault = self.make_vault()
        with self.assertRaises(CredentialScopeError):
            vault.put(Secret("x"), scope="")

    def test_scope_mismatch_rejected(self) -> None:
        vault = self.make_vault()
        handle = vault.put(Secret("x"), scope="github")
        with self.assertRaises(CredentialScopeError):
            vault.get(handle, scope="slack")

    def test_expired_handle_rejected(self) -> None:
        vault = self.make_vault()
        handle = vault.put(Secret("x"), scope="github", expires_at=time.time() - 1)
        with self.assertRaises(CredentialExpiredError):
            vault.get(handle, scope="github")

    def test_revoke_then_get_raises(self) -> None:
        vault = self.make_vault()
        handle = vault.put(Secret("x"), scope="github")
        self.assertTrue(vault.has(handle))
        vault.revoke(handle)
        self.assertFalse(vault.has(handle))
        with self.assertRaises(CredentialRevokedError):
            vault.get(handle, scope="github")

    def test_handle_carries_no_plaintext(self) -> None:
        vault = self.make_vault()
        handle = vault.put(Secret("t0p-secret-value"), scope="github")
        self.assertNotIn("t0p-secret-value", repr(handle))
        self.assertNotIn("t0p-secret-value", str(handle))
        self.assertNotIn("t0p-secret-value", str(handle.to_dict()))


class EnvFileCredentialVaultTests(CredentialVaultContract, unittest.TestCase):
    def make_vault(self) -> Any:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        return EnvFileCredentialVault(Path(self._dir.name) / "creds.json")

    def test_persists_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "creds.json"
            handle = EnvFileCredentialVault(path).put(Secret("persisted"), scope="github")
            self.assertEqual(EnvFileCredentialVault(path).get(handle, scope="github").unwrap(), "persisted")

    def test_file_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "creds.json"
            vault = EnvFileCredentialVault(path)
            vault.put(Secret("x"), scope="github")
            self.assertEqual(path.stat().st_mode & 0o077, 0)


class KeyringCredentialVaultTests(CredentialVaultContract, unittest.TestCase):
    def make_vault(self) -> Any:
        return KeyringCredentialVault(backend=_FakeKeyring())

    def test_delete_missing_key_is_silent(self) -> None:
        class _RaisingKeyring(_FakeKeyring):
            def delete_password(self, service: str, username: str) -> None:
                raise KeyError("missing")

        vault = KeyringCredentialVault(backend=_RaisingKeyring())
        handle = vault.put(Secret("x"), scope="github")
        vault.revoke(handle)  # must not raise


class VaultCredentialVaultTests(CredentialVaultContract, unittest.TestCase):
    def make_vault(self) -> Any:
        return VaultCredentialVault(client=_FakeVaultClient())


class MissingOptionalDependencyTests(unittest.TestCase):
    def test_keyring_missing_raises_backend_unavailable(self) -> None:
        real_import = __import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "keyring":
                raise ImportError("no keyring")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=fake_import),
            self.assertRaises(CredentialBackendUnavailable),
        ):
            KeyringCredentialVault()

    def test_vault_missing_hvac_raises_backend_unavailable(self) -> None:
        real_import = __import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "hvac":
                raise ImportError("no hvac")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=fake_import),
            self.assertRaises(CredentialBackendUnavailable),
        ):
            VaultCredentialVault(url="http://localhost:8200", token="t")


if __name__ == "__main__":
    unittest.main()
