from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent.enums import SandboxPolicy
from leos_agent.errors import WorkspaceEscapeBlocked
from leos_agent.sandbox import (
    SandboxCommand,
    SandboxUnavailable,
    WorkspaceSubprocessSandboxRunner,
)
from leos_agent.sandbox_backends import (
    FirecrackerSandboxRunner,
    GvisorSandboxRunner,
    RootlessPodmanSandboxRunner,
    resolve_sandbox_runner,
)


class GvisorSandboxRunnerTests(unittest.TestCase):
    def test_build_argv_injects_runsc_runtime_and_keeps_hardening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = GvisorSandboxRunner(Path(tmp), runtime="/usr/bin/docker", runsc_runtime="runsc")
            argv = runner.build_argv(SandboxCommand(["python", "-V"]))

        # gVisor runtime is selected as the OCI runtime.
        self.assertIn("--runtime", argv)
        self.assertIn("runsc", argv)
        # --runtime must come right after the `run` subcommand, before the image.
        self.assertLess(argv.index("--runtime"), argv.index(runner.image))
        # Inherited hardening flags remain.
        self.assertIn("--network", argv)
        self.assertIn("none", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--security-opt", argv)
        self.assertIn("no-new-privileges", argv)
        self.assertIn("--user", argv)

    def test_run_raises_when_runsc_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = GvisorSandboxRunner(Path(tmp), runtime="/usr/bin/docker", runsc_runtime="runsc")
            # docker present, runsc absent
            with (
                mock.patch("shutil.which", side_effect=lambda name: "/usr/bin/docker" if name != "runsc" else None),
                self.assertRaises(SandboxUnavailable),
            ):
                runner.run(SandboxCommand(["python", "-V"]))

    def test_is_available_reflects_runtime_presence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = GvisorSandboxRunner(Path(tmp))
            with mock.patch("shutil.which", return_value="/usr/bin/anything"):
                self.assertTrue(runner.is_available())
            with mock.patch("shutil.which", return_value=None):
                self.assertFalse(runner.is_available())


class RootlessPodmanSandboxRunnerTests(unittest.TestCase):
    def test_build_argv_has_userns_and_seccomp_and_hardening(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = RootlessPodmanSandboxRunner(
                Path(tmp), runtime="/usr/bin/podman", seccomp_profile="/etc/leos/seccomp.json"
            )
            argv = runner.build_argv(SandboxCommand(["python", "-V"]))

        self.assertIn("--userns", argv)
        self.assertIn("keep-id", argv)
        self.assertTrue(any("seccomp=/etc/leos/seccomp.json" in part for part in argv))
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--network", argv)
        self.assertIn("none", argv)

    def test_build_argv_without_seccomp_profile_omits_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = RootlessPodmanSandboxRunner(Path(tmp), runtime="/usr/bin/podman", seccomp_profile=None)
            argv = runner.build_argv(SandboxCommand(["python", "-V"]))
        self.assertFalse(any("seccomp=" in part for part in argv))
        # userns hardening still present without an explicit profile.
        self.assertIn("keep-id", argv)

    def test_workspace_escape_still_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = RootlessPodmanSandboxRunner(Path(tmp), runtime="/usr/bin/podman")
            with self.assertRaises(WorkspaceEscapeBlocked):
                runner.build_argv(SandboxCommand(["python"], cwd="../outside"))


class FirecrackerSandboxRunnerTests(unittest.TestCase):
    def test_run_raises_when_binary_missing(self) -> None:
        runner = FirecrackerSandboxRunner(
            firecracker_bin="/missing/firecracker",
            kernel_image=Path("/nonexistent/vmlinux"),
            rootfs_image=Path("/nonexistent/rootfs.ext4"),
        )
        with self.assertRaises(SandboxUnavailable):
            runner.run(SandboxCommand(["python", "-V"]))

    def test_is_available_false_without_prerequisites(self) -> None:
        runner = FirecrackerSandboxRunner(
            firecracker_bin="firecracker",
            kernel_image=Path("/nonexistent/vmlinux"),
            rootfs_image=Path("/nonexistent/rootfs.ext4"),
        )
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(runner.is_available())

    def test_build_config_describes_readonly_root_and_command(self) -> None:
        runner = FirecrackerSandboxRunner(
            kernel_image=Path("/img/vmlinux"),
            rootfs_image=Path("/img/rootfs.ext4"),
            vcpu_count=2,
            mem_size_mib=256,
        )
        config = runner.build_config(SandboxCommand(["python", "-V"]))
        self.assertEqual(config["boot-source"]["kernel_image_path"], "/img/vmlinux")
        self.assertTrue(config["drives"][0]["is_read_only"])
        self.assertEqual(config["machine-config"]["vcpu_count"], 2)
        self.assertFalse(config["machine-config"]["smt"])
        self.assertEqual(config["command"], ["python", "-V"])

    def test_run_fails_closed_when_prerequisites_present_but_unwired(self) -> None:
        runner = FirecrackerSandboxRunner(
            kernel_image=Path("/img/vmlinux"),
            rootfs_image=Path("/img/rootfs.ext4"),
        )
        with (
            mock.patch.object(runner, "is_available", return_value=True),
            self.assertRaises(SandboxUnavailable) as ctx,
        ):
            runner.run(SandboxCommand(["python", "-V"]))
        self.assertIn("not yet wired", str(ctx.exception))


class ResolveSandboxRunnerTests(unittest.TestCase):
    def test_workspace_policy_returns_workspace_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = resolve_sandbox_runner(SandboxPolicy.WORKSPACE, Path(tmp))
            self.assertIsInstance(runner, WorkspaceSubprocessSandboxRunner)

    def test_container_policy_never_returns_workspace_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # gVisor available -> returns a container-grade runner, not workspace.
            with mock.patch("shutil.which", return_value="/usr/bin/anything"):
                runner = resolve_sandbox_runner(SandboxPolicy.CONTAINER, Path(tmp))
            self.assertNotIsInstance(runner, WorkspaceSubprocessSandboxRunner)
            self.assertIsInstance(runner, GvisorSandboxRunner | RootlessPodmanSandboxRunner)

    def test_container_policy_raises_when_nothing_available(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("shutil.which", return_value=None),
            self.assertRaises(SandboxUnavailable),
        ):
            resolve_sandbox_runner(SandboxPolicy.CONTAINER, Path(tmp))

    def test_microvm_policy_raises_when_firecracker_absent(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("shutil.which", return_value=None),
            self.assertRaises(SandboxUnavailable),
        ):
            resolve_sandbox_runner(SandboxPolicy.MICROVM, Path(tmp))

    def test_prefer_orders_container_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # All runtimes available; prefer podman first -> rootless podman chosen.
            with mock.patch("shutil.which", return_value="/usr/bin/anything"):
                runner = resolve_sandbox_runner(SandboxPolicy.CONTAINER, Path(tmp), prefer=("podman", "gvisor"))
            self.assertIsInstance(runner, RootlessPodmanSandboxRunner)

    def test_microvm_policy_returns_available_firecracker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fc = FirecrackerSandboxRunner(
                kernel_image=Path("/img/vmlinux"),
                rootfs_image=Path("/img/rootfs.ext4"),
            )
            with mock.patch.object(fc, "is_available", return_value=True):
                runner = resolve_sandbox_runner(SandboxPolicy.MICROVM, Path(tmp), firecracker=fc)
            self.assertIs(runner, fc)

    def test_none_policy_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SandboxUnavailable):
            resolve_sandbox_runner(SandboxPolicy.NONE, Path(tmp))


if __name__ == "__main__":
    unittest.main()
