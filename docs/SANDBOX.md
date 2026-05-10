# Sandbox — Leos Agent Runtime

## SandboxRunner Protocol

```python
class SandboxRunner(Protocol):
    def run(self, command: SandboxCommand) -> SandboxResult: ...
```

Three implementations: workspace subprocess (active), container (placeholder),
microVM (placeholder).

## WorkspaceSubprocessSandboxRunner

Active implementation. Constraints:
- Filesystem scoping to workspace root (commonpath check).
- Empty environment by default (no parent env inheritance).
- Optional allowlisted env keys.
- Timeout via `subprocess.run(timeout=...)`.
- Output truncation via `max_output_bytes`.

**WARNING**: This provides filesystem scoping ONLY. No network isolation, no
memory limits, no OS-level security. It is suitable for development/testing
but is NOT a production isolation boundary.

## Env Isolation

- Default: empty env dict passed to subprocess (no parent env inherited).
- `allowed_env_keys=("PATH",)`: only PATH is forwarded from command.env.
- This prevents CI secrets, shell tokens, and parent process environment
  from leaking into sandboxed commands.

## ContainerSandboxRunner

Placeholder. `run()` raises `SandboxUnavailable("container sandbox requires external runtime")`.
Intended for future Docker/Podman integration.

## MicroVMSandboxRunner

Placeholder. `run()` raises `SandboxUnavailable("microVM sandbox requires external runtime")`.
Intended for future Firecracker/gVisor integration.

## SandboxCommandTool

A tool wrapping `WorkspaceSubprocessSandboxRunner`. Requires `Permission.EXECUTE_CODE`,
risk HIGH, irreversible. **NOT registered in `default_registry`.** Must be
added explicitly with a conservative policy profile.

## No Network Isolation

The workspace subprocess sandbox does NOT restrict network access. Tools that
should not access the network must not use this runner or must be wrapped with
additional controls.
