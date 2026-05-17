from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from leos_agent import DockerSandboxRunner, SandboxCommand
from leos_agent.errors import SandboxViolation, WorkspaceEscapeBlocked
from leos_agent.sandbox import SandboxUnavailable


class DockerSandboxHardeningTests(unittest.TestCase):
    def test_command_contains_hardening_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = DockerSandboxRunner(Path(tmp), runtime="/usr/bin/docker", read_only_workspace=True)
            argv = runner.build_argv(SandboxCommand(["python", "-V"]))

        self.assertIn("--network", argv)
        self.assertIn("none", argv)
        self.assertIn("--cap-drop", argv)
        self.assertIn("ALL", argv)
        self.assertIn("--security-opt", argv)
        self.assertIn("no-new-privileges", argv)
        self.assertIn("--pids-limit", argv)
        self.assertIn("128", argv)
        self.assertTrue(any("readonly" in part for part in argv))

    def test_workspace_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = DockerSandboxRunner(Path(tmp), runtime="/usr/bin/docker")
            with self.assertRaises(WorkspaceEscapeBlocked):
                runner.build_argv(SandboxCommand(["python"], cwd="../outside"))

    def test_docker_unavailable_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = DockerSandboxRunner(Path(tmp), runtime="/missing/docker")
            with self.assertRaises(SandboxUnavailable):
                runner.run(SandboxCommand(["python", "-V"]))

    def test_stdout_stderr_are_truncated(self) -> None:
        completed = subprocess.CompletedProcess(["docker"], 0, stdout="x" * 20, stderr="y" * 20)
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("shutil.which", return_value="/usr/bin/docker"),
            mock.patch("subprocess.run", return_value=completed),
        ):
            runner = DockerSandboxRunner(Path(tmp), runtime="/usr/bin/docker")
            result = runner.run(SandboxCommand(["python"], max_output_bytes=5))

        self.assertTrue(result.truncated)
        self.assertEqual(result.stdout, "xxxxx")
        self.assertEqual(result.stderr, "yyyyy")

    def test_dangerous_root_mount_is_rejected(self) -> None:
        with self.assertRaises(SandboxViolation):
            DockerSandboxRunner(Path("/"))


if __name__ == "__main__":
    unittest.main()
