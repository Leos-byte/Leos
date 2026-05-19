from __future__ import annotations

import subprocess
import sys
import unittest


class ExtensibilityDemoTests(unittest.TestCase):
    def test_extensibility_demo_runs_without_printing_secret(self) -> None:
        proc = subprocess.run(  # nosec B603
            [sys.executable, "examples/extensibility_demo/run_demo.py"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("manifest loaded", proc.stdout)
        self.assertIn("evaluator result", proc.stdout)
        self.assertIn("checkpoint saved", proc.stdout)
        self.assertIn("secret handle created", proc.stdout)
        self.assertIn("secret not printed", proc.stdout)
        self.assertNotIn("demo-secret-value", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
