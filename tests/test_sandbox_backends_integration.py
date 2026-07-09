"""Real-runtime integration tests for the sandbox isolation backends.

Unlike ``tests/test_sandbox_backends.py`` (which mocks ``shutil.which`` and
``subprocess.run``), these tests execute real containers to observe the
hardening behavior end to end. Each class is gated on the presence of its
runtime binary and skips with an explicit reason when the runtime is missing,
so CI skip reports stay auditable.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - test-only image pre-pull with a fixed argv
import tempfile
import unittest
from pathlib import Path
from typing import Any

from leos_agent.enums import SandboxPolicy
from leos_agent.sandbox import SandboxCommand
from leos_agent.sandbox_backends import (
    GvisorSandboxRunner,
    RootlessPodmanSandboxRunner,
    resolve_sandbox_runner,
)

_IMAGE = "docker.io/library/alpine:3.21"
_PODMAN = shutil.which("podman")
_RUNSC = shutil.which("runsc")


def _pull_image(runtime: str) -> None:
    proc = subprocess.run(  # nosec B603 - fixed argv, pinned public test image
        [runtime, "pull", "--quiet", _IMAGE],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise unittest.SkipTest(f"cannot pull {_IMAGE}: {proc.stderr.strip()[:200]}")


@unittest.skipUnless(_PODMAN, "requires the podman runtime binary")
class RootlessPodmanRealRuntimeTests(unittest.TestCase):
    """Run real rootless-podman containers through RootlessPodmanSandboxRunner."""

    workspace: tempfile.TemporaryDirectory  # type: ignore[type-arg]

    @classmethod
    def setUpClass(cls) -> None:
        assert _PODMAN is not None
        _pull_image(_PODMAN)
        cls.workspace = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.workspace.cleanup()

    def _runner(self, **kwargs: Any) -> RootlessPodmanSandboxRunner:
        return RootlessPodmanSandboxRunner(
            Path(self.workspace.name),
            runtime=_PODMAN,
            image=_IMAGE,
            **kwargs,
        )

    def test_echo_round_trip(self) -> None:
        result = self._runner().run(SandboxCommand(argv=["echo", "hello-sandbox"], timeout_seconds=60.0))
        self.assertTrue(result.ok, msg=result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello-sandbox", result.stdout)

    def test_runs_as_non_root_user(self) -> None:
        result = self._runner().run(SandboxCommand(argv=["id", "-u"], timeout_seconds=60.0))
        self.assertTrue(result.ok, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "65532")

    def test_timeout_kills_command(self) -> None:
        result = self._runner().run(SandboxCommand(argv=["sleep", "30"], timeout_seconds=3.0))
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.returncode)

    def test_output_is_truncated(self) -> None:
        command = SandboxCommand(
            argv=["sh", "-c", "head -c 4096 /dev/zero | tr '\\0' 'a'"],
            timeout_seconds=60.0,
            max_output_bytes=64,
        )
        result = self._runner().run(command)
        self.assertTrue(result.ok, msg=result.stderr)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.stdout), 64)

    def test_network_egress_is_blocked(self) -> None:
        command = SandboxCommand(argv=["wget", "-q", "-T", "2", "-O-", "http://example.com"], timeout_seconds=60.0)
        result = self._runner().run(command)
        self.assertFalse(result.ok)
        self.assertNotEqual(result.returncode, 0)

    def test_rootfs_is_read_only_with_tmpfs_tmp(self) -> None:
        denied = self._runner().run(SandboxCommand(argv=["sh", "-c", "echo probe > /etc/probe"], timeout_seconds=60.0))
        self.assertFalse(denied.ok)
        allowed = self._runner().run(
            SandboxCommand(argv=["sh", "-c", "echo probe > /tmp/probe && cat /tmp/probe"], timeout_seconds=60.0)
        )
        self.assertTrue(allowed.ok, msg=allowed.stderr)
        self.assertIn("probe", allowed.stdout)

    def test_resolve_sandbox_runner_prefers_podman_and_executes(self) -> None:
        runner = resolve_sandbox_runner(
            SandboxPolicy.CONTAINER,
            Path(self.workspace.name),
            prefer=("podman",),
            image=_IMAGE,
        )
        self.assertIsInstance(runner, RootlessPodmanSandboxRunner)
        result = runner.run(SandboxCommand(argv=["echo", "resolved"], timeout_seconds=60.0))
        self.assertTrue(result.ok, msg=result.stderr)
        self.assertIn("resolved", result.stdout)


@unittest.skipUnless(_RUNSC, "requires the gVisor runsc runtime binary")
class GvisorRealRuntimeTests(unittest.TestCase):
    """Run a real container under the gVisor runsc OCI runtime."""

    workspace: tempfile.TemporaryDirectory  # type: ignore[type-arg]

    @classmethod
    def setUpClass(cls) -> None:
        runtime = shutil.which("podman") or shutil.which("docker")
        if runtime is None:
            raise unittest.SkipTest("requires podman or docker to host the runsc runtime")
        cls.runtime = runtime
        _pull_image(runtime)
        cls.workspace = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.workspace.cleanup()

    def test_echo_under_runsc(self) -> None:
        runner = GvisorSandboxRunner(
            Path(self.workspace.name),
            runtime=self.runtime,
            image=_IMAGE,
        )
        result = runner.run(SandboxCommand(argv=["echo", "hello-gvisor"], timeout_seconds=60.0))
        self.assertTrue(result.ok, msg=result.stderr)
        self.assertIn("hello-gvisor", result.stdout)


if __name__ == "__main__":
    unittest.main()
