from __future__ import annotations

import time
import unittest

from leos_agent.credentials import (
    CredentialExpiredError,
    CredentialRevokedError,
    CredentialScopeError,
    InMemoryCredentialVault,
    SecretHandle,
)
from leos_agent.tools import Secret


class CredentialTests(unittest.TestCase):
    def test_put_get_secret(self) -> None:
        vault = InMemoryCredentialVault()
        handle = vault.put(Secret("token-value"), scope="github:o/r")

        secret = vault.get(handle, scope="github:o/r")

        self.assertEqual(secret.unwrap(), "token-value")

    def test_wrong_scope_rejected(self) -> None:
        vault = InMemoryCredentialVault()
        handle = vault.put(Secret("token-value"), scope="github:o/r")

        with self.assertRaises(CredentialScopeError):
            vault.get(handle, scope="github:other/repo")

    def test_revoked_handle_rejected(self) -> None:
        vault = InMemoryCredentialVault()
        handle = vault.put(Secret("token-value"), scope="github:o/r")

        vault.revoke(handle)

        with self.assertRaises(CredentialRevokedError):
            vault.get(handle, scope="github:o/r")

    def test_expired_handle_rejected(self) -> None:
        vault = InMemoryCredentialVault()
        handle = vault.put(Secret("token-value"), scope="github:o/r", expires_at=time.time() - 1)

        with self.assertRaises(CredentialExpiredError):
            vault.get(handle, scope="github:o/r")

    def test_missing_handle_rejected(self) -> None:
        vault = InMemoryCredentialVault()
        handle = SecretHandle(handle_id="missing", scope="github:o/r")

        with self.assertRaises(CredentialRevokedError):
            vault.get(handle, scope="github:o/r")

    def test_repr_and_str_do_not_contain_secret(self) -> None:
        vault = InMemoryCredentialVault()
        handle = vault.put(Secret("token-value"), scope="github:o/r")

        self.assertNotIn("token-value", repr(handle))
        self.assertNotIn("token-value", str(handle))
        self.assertNotIn("token-value", repr(vault))

    def test_has_and_dict_roundtrip(self) -> None:
        vault = InMemoryCredentialVault()
        handle = vault.put(Secret("token-value"), scope="github:o/r")

        restored = SecretHandle.from_dict(handle.to_dict())

        self.assertTrue(vault.has(restored))
        self.assertEqual(restored.handle_id, handle.handle_id)
        self.assertEqual(restored.scope, handle.scope)


if __name__ == "__main__":
    unittest.main()
