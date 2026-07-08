# Credential Vaults

Credentials are referenced by `SecretHandle` values and stored behind the
`CredentialVault` protocol (`put`/`get`/`revoke`/`has`). A handle is a
serializable reference only — it never carries secret material, and the raw
value is redacted from audit, trace, memory, and runtime stores.

## Backends

- `InMemoryCredentialVault` (`credentials.py`) — development/testing vault in
  process memory.
- `EnvFileCredentialVault` (`credential_backends.py`) — persists JSON envelopes
  to a private file (`chmod 600`). Suitable for single-host development/staging.
- `KeyringCredentialVault` (`credential_backends.py`) — OS keychain via the
  optional `keyring` package. A backend may be injected for testing.
- `VaultCredentialVault` (`credential_backends.py`) — HashiCorp Vault (KV v2)
  via the optional `hvac` package. This is the first KMS-grade backend; AWS KMS
  and GCP KMS envelope vaults can extend the same `_BaseCredentialVault`
  (which enforces scope, expiry, and revocation uniformly) by implementing the
  three-method envelope store.

## Invariants

Enforced by the shared base and covered by the credential-vault contract in
`tests/test_credential_backends.py`:

- Empty scope is rejected at `put`.
- `get` requires the scope to match both the stored envelope and the handle.
- Expired handles raise `CredentialExpiredError`.
- A revoked handle is gone: `has` is `False` and `get` raises
  `CredentialRevokedError`.
- `SecretHandle` (including `to_dict()`, `repr`, `str`) carries no plaintext.

Optional dependencies are imported lazily; a missing `keyring`/`hvac` surfaces
`CredentialBackendUnavailable`.
