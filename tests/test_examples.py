from __future__ import annotations

import subprocess
import sys
import unittest


class ExampleTests(unittest.TestCase):
    def test_software_engineering_agent_demo_runs(self) -> None:
        proc = subprocess.run(  # nosec B603
            [sys.executable, "examples/software_engineering_agent/run_demo.py"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("selected plan:", proc.stdout)
        self.assertIn("test result: True", proc.stdout)
        self.assertIn("goal evaluation: succeeded", proc.stdout)
        self.assertIn("final goal status: succeeded", proc.stdout)


if __name__ == "__main__":
    unittest.main()
