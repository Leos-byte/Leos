# Extensibility Demo

This local demo exercises the extension infrastructure without network access
or external dependencies:

```bash
python examples/extensibility_demo/run_demo.py
```

It loads an EchoTool manifest, validates it against the runtime tool spec,
evaluates a simple goal through `EvaluatorRegistry`, stores a runtime
checkpoint in `InMemoryRuntimeStore`, and creates a `SecretHandle` in
`InMemoryCredentialVault` without printing the secret value.
