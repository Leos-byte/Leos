# Extensibility Demo

> **Leos is not a production autonomous employee and not a general open-world agent.**

This local demo exercises the extension infrastructure without network access
or external dependencies:

```bash
python examples/extensibility_demo/run_demo.py
```

## What the demo exercises

- **Tool manifest loading**: loads `manifests/echo.json`, validates it via
  `ToolManifestRegistry`, and checks it against the runtime `EchoTool` spec.
- **Goal evaluation**: runs a goal through `EvaluatorRegistry` to verify
  deterministic criteria matching.
- **Runtime checkpoint storage**: persists a checkpoint in
  `InMemoryRuntimeStore`.
- **Credential vault operations**: creates a `SecretHandle` in
  `InMemoryCredentialVault`. The secret value is never printed or persisted
  in plaintext. Only the handle reference is shown.
