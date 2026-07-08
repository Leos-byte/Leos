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

Bare placeholder retained for backward compatibility. `run()` raises
`SandboxUnavailable("container sandbox requires external runtime")`. Prefer the
concrete container backends in `sandbox_backends.py` (`GvisorSandboxRunner`,
`RootlessPodmanSandboxRunner`) or `DockerSandboxRunner`.

## MicroVMSandboxRunner

Bare placeholder retained for backward compatibility. `run()` raises
`SandboxUnavailable("microVM sandbox requires external runtime")`. See
`FirecrackerSandboxRunner` in `sandbox_backends.py` for the microVM target with
prerequisite detection and VM-config construction.

## Production isolation backends (`sandbox_backends.py`)

- `GvisorSandboxRunner`: container under the gVisor `runsc` OCI runtime
  (`--runtime runsc`); `SandboxPolicy.CONTAINER`.
- `RootlessPodmanSandboxRunner`: rootless Podman with `--userns=keep-id` and an
  optional seccomp profile; `SandboxPolicy.CONTAINER`.
- `FirecrackerSandboxRunner`: microVM target; fails closed until firecracker
  binary + guest kernel + rootfs are present; `SandboxPolicy.MICROVM`.
- `resolve_sandbox_runner(policy, workspace_root, ...)`: returns the strongest
  available runner and never downgrades a container/microVM policy to the
  workspace runner (raises `SandboxUnavailable` instead).

## SandboxCommandTool

A tool wrapping `WorkspaceSubprocessSandboxRunner`. Requires `Permission.EXECUTE_CODE`,
risk HIGH, irreversible. **NOT registered in `default_registry`.** Must be
added explicitly with a conservative policy profile.

## No Network Isolation

The workspace subprocess sandbox does NOT restrict network access. Tools that
should not access the network must not use this runner or must be wrapped with
additional controls.
