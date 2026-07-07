# Sandboxing

Leos supports multiple sandbox runner shapes, all implementing the single
`SandboxRunner.run(command: SandboxCommand) -> SandboxResult` protocol:

- `WorkspaceSubprocessSandboxRunner`: development and test runner scoped to a
  workspace path. It is not a production isolation boundary.
- `DockerSandboxRunner`: Docker/Podman command builder with hardening flags such
  as `--network none`, `--cap-drop ALL`, `--security-opt no-new-privileges`,
  memory/CPU/PID limits, read-only rootfs, `/tmp` tmpfs, and a non-root user.
- `GvisorSandboxRunner` (`sandbox_backends.py`): runs the container under the
  gVisor `runsc` OCI runtime for syscall-level isolation. Composes over the
  Docker/Podman argv builder, injecting `--runtime runsc` while keeping every
  hardening flag. Registered under `SandboxPolicy.CONTAINER`.
- `RootlessPodmanSandboxRunner` (`sandbox_backends.py`): hardened rootless
  Podman with a user-namespace remap (`--userns=keep-id`) and an optional
  explicit seccomp profile (`--security-opt seccomp=<path>`); when no profile is
  supplied, the runtime's built-in default seccomp allowlist applies. Registered
  under `SandboxPolicy.CONTAINER`.
- `FirecrackerSandboxRunner` (`sandbox_backends.py`): microVM isolation target
  under `SandboxPolicy.MICROVM`. It fails closed with `SandboxUnavailable` until
  the firecracker binary, guest kernel, and rootfs image are provided, and the
  command-execution lifecycle is wired; it provides prerequisite detection and
  testable VM-config construction (`build_config`).

## Selecting a runner

`resolve_sandbox_runner(policy, workspace_root, *, prefer=(), firecracker=None)`
returns the strongest *available* runner for a policy. For `CONTAINER` and
`MICROVM` it **never** returns the workspace-subprocess runner — if no isolation
runtime is available it raises `SandboxUnavailable`. This complements the
`TransactionManager` guard, which already blocks a `CONTAINER`/`MICROVM` tool
when no matching runner is registered in `sandbox_runners` (it never downgrades).

The container backends are unit-tested for command construction. CI does not
prove full container/microVM isolation because it may not have `runsc`, `podman`,
`docker`, or `firecracker` available; real-runtime integration tests are gated
with `skipUnless`.

High-risk code execution remains opt-in and policy-gated. Under
`production_locked_down`, any `EXECUTE_CODE` tool using `SandboxPolicy.WORKSPACE`
is blocked; such tools must use `CONTAINER`/`MICROVM` with a matching runner.
