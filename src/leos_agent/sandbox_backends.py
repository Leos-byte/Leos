"""Production-grade sandbox isolation backends.

These runners implement the same :class:`~leos_agent.sandbox.SandboxRunner`
protocol as the built-in runners but provide stronger isolation:

- :class:`GvisorSandboxRunner` runs the container under the gVisor ``runsc``
  OCI runtime (syscall interception), registered under ``SandboxPolicy.CONTAINER``.
- :class:`RootlessPodmanSandboxRunner` runs a hardened rootless Podman container
  with a user-namespace remap and an optional seccomp profile, also under
  ``SandboxPolicy.CONTAINER``.
- :class:`FirecrackerSandboxRunner` targets true microVM isolation under
  ``SandboxPolicy.MICROVM``. It fails closed with :class:`SandboxUnavailable`
  until the firecracker binary, guest kernel, and rootfs image are provided.

All backends reuse the conservative argv hardening of
:class:`~leos_agent.sandbox.DockerSandboxRunner` and never silently downgrade to
the weak workspace-subprocess runner. Use :func:`resolve_sandbox_runner` to pick
the strongest *available* runner for a policy; it raises ``SandboxUnavailable``
rather than returning a workspace runner for a container/microVM policy.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .enums import SandboxPolicy
from .sandbox import (
    DockerSandboxRunner,
    SandboxCommand,
    SandboxResult,
    SandboxRunner,
    SandboxUnavailable,
    WorkspaceSubprocessSandboxRunner,
)


def _insert_after_run(argv: list[str], extra: Sequence[str]) -> list[str]:
    """Insert ``extra`` immediately after the ``run`` subcommand token."""
    index = argv.index("run") + 1
    return argv[:index] + list(extra) + argv[index:]


class GvisorSandboxRunner(DockerSandboxRunner):
    """Container runner that executes under the gVisor ``runsc`` OCI runtime.

    gVisor is a container runtime, so this composes over the Docker/Podman argv
    builder and injects ``--runtime <runsc_runtime>`` while keeping every
    hardening flag. Registers under ``SandboxPolicy.CONTAINER``.
    """

    def __init__(self, workspace_root: Path, *, runsc_runtime: str = "runsc", **docker_kwargs: Any) -> None:
        super().__init__(workspace_root, **docker_kwargs)
        self.runsc_runtime = runsc_runtime

    def build_argv(self, command: SandboxCommand) -> list[str]:
        argv = super().build_argv(command)
        return _insert_after_run(argv, ["--runtime", self.runsc_runtime])

    def _runsc_available(self) -> bool:
        return shutil.which(self.runsc_runtime) is not None

    def is_available(self) -> bool:
        return super().is_available() and self._runsc_available()

    def run(self, command: SandboxCommand) -> SandboxResult:
        if not self._runsc_available():
            raise SandboxUnavailable(f"gVisor runtime '{self.runsc_runtime}' is not available")
        return super().run(command)


class RootlessPodmanSandboxRunner(DockerSandboxRunner):
    """Hardened rootless Podman runner.

    Adds a user-namespace remap (``--userns=keep-id``) on top of the shared
    container hardening. When ``seccomp_profile`` is set, an explicit
    ``--security-opt seccomp=<path>`` is applied; otherwise the runtime's
    built-in default seccomp profile (already a strong allowlist) applies.
    Registers under ``SandboxPolicy.CONTAINER``.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        userns: str = "keep-id",
        seccomp_profile: str | None = None,
        **docker_kwargs: Any,
    ) -> None:
        super().__init__(workspace_root, **docker_kwargs)
        self.userns = userns
        self.seccomp_profile = seccomp_profile

    def _runtime_binary(self) -> str:
        if self.runtime:
            return self.runtime
        found = shutil.which("podman")
        if found:
            return found
        raise SandboxUnavailable("podman runtime is not available")

    def build_argv(self, command: SandboxCommand) -> list[str]:
        argv = super().build_argv(command)
        extra = ["--userns", self.userns]
        if self.seccomp_profile:
            extra += ["--security-opt", f"seccomp={self.seccomp_profile}"]
        return _insert_after_run(argv, extra)


class FirecrackerSandboxRunner:
    """MicroVM isolation backend targeting Firecracker.

    This runner fails closed: :meth:`run` raises :class:`SandboxUnavailable`
    unless the firecracker binary, guest kernel, and rootfs image are all
    present, and the command-execution lifecycle is not yet wired. It provides
    prerequisite detection and testable VM-config construction so it can be
    registered under ``SandboxPolicy.MICROVM`` without downgrading isolation.
    """

    def __init__(
        self,
        *,
        firecracker_bin: str = "firecracker",
        jailer_bin: str = "jailer",
        kernel_image: Path,
        rootfs_image: Path,
        vcpu_count: int = 1,
        mem_size_mib: int = 512,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.firecracker_bin = firecracker_bin
        self.jailer_bin = jailer_bin
        self.kernel_image = kernel_image
        self.rootfs_image = rootfs_image
        self.vcpu_count = vcpu_count
        self.mem_size_mib = mem_size_mib
        self.timeout_seconds = timeout_seconds

    def is_available(self) -> bool:
        return (
            shutil.which(self.firecracker_bin) is not None and self.kernel_image.exists() and self.rootfs_image.exists()
        )

    def build_config(self, command: SandboxCommand) -> dict[str, Any]:
        """Return the Firecracker machine configuration for ``command``.

        Exposed for unit testing of the VM spec without booting a microVM.
        """
        return {
            "boot-source": {
                "kernel_image_path": str(self.kernel_image),
                "boot_args": "console=ttyS0 reboot=k panic=1 pci=off",
            },
            "drives": [
                {
                    "drive_id": "rootfs",
                    "path_on_host": str(self.rootfs_image),
                    "is_root_device": True,
                    "is_read_only": True,
                }
            ],
            "machine-config": {
                "vcpu_count": self.vcpu_count,
                "mem_size_mib": self.mem_size_mib,
                "smt": False,
            },
            "network-interfaces": [],
            "command": list(command.argv),
        }

    def run(self, command: SandboxCommand) -> SandboxResult:
        if not self.is_available():
            raise SandboxUnavailable("firecracker runtime, guest kernel, or rootfs image is not available")
        # Booting a microVM and executing inside it (API socket + vsock/ssh) is
        # deliberately not performed here; fail closed rather than degrade.
        raise SandboxUnavailable("firecracker command-execution lifecycle is not yet wired")


def resolve_sandbox_runner(
    policy: SandboxPolicy,
    workspace_root: Path,
    *,
    prefer: Sequence[str] = (),
    firecracker: FirecrackerSandboxRunner | None = None,
    **runner_kwargs: Any,
) -> SandboxRunner:
    """Return the strongest *available* runner for ``policy``.

    For ``CONTAINER``/``MICROVM`` this never returns a workspace-subprocess
    runner; if no isolation runtime is available it raises ``SandboxUnavailable``.
    ``prefer`` may reorder container candidates by name
    (``"gvisor"``, ``"podman"``, ``"docker"``).
    """
    if policy is SandboxPolicy.WORKSPACE:
        return WorkspaceSubprocessSandboxRunner(workspace_root)

    if policy is SandboxPolicy.CONTAINER:
        candidates: list[tuple[str, DockerSandboxRunner]] = [
            ("gvisor", GvisorSandboxRunner(workspace_root, **runner_kwargs)),
            ("podman", RootlessPodmanSandboxRunner(workspace_root, **runner_kwargs)),
            ("docker", DockerSandboxRunner(workspace_root, **runner_kwargs)),
        ]
        if prefer:
            order = {name: rank for rank, name in enumerate(prefer)}
            candidates.sort(key=lambda item: order.get(item[0], len(order)))
        for _name, runner in candidates:
            if runner.is_available():
                return runner
        raise SandboxUnavailable("no container isolation runtime (gVisor/rootless podman/docker) is available")

    if policy is SandboxPolicy.MICROVM:
        if firecracker is not None and firecracker.is_available():
            return firecracker
        raise SandboxUnavailable("no microVM runtime (firecracker) is available")

    raise SandboxUnavailable(f"policy '{policy.value}' has no isolation backend")
