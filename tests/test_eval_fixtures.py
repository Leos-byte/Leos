from __future__ import annotations

import unittest
from pathlib import Path

from leos_agent.eval_runner import run_eval_suite


class EvalFixtureTests(unittest.TestCase):
    def test_safety_fixtures_load_and_run(self) -> None:
        report = run_eval_suite(Path("benchmarks/safety"))

        self.assertEqual(report.total, 8)
        self.assertEqual(report.failed, 0)
        self.assertEqual(
            {case.name for case in report.cases}, {path.stem for path in Path("benchmarks/safety").glob("*.json")}
        )


if __name__ == "__main__":
    unittest.main()
