from __future__ import annotations

import tempfile
from pathlib import Path

from leos_agent import (
    ActionStep,
    AgentKernel,
    AgentLoop,
    AgentLoopConfig,
    ApprovalGate,
    AuditLog,
    CausalGraph,
    DeterministicProposalProvider,
    Goal,
    PlannerConfig,
    PlanProposal,
    PolicyEngine,
    RiskLevel,
    default_dev_registry,
    render_trace_markdown,
    replay_audit_log,
)

BROKEN = """def add(a, b):
    return a - b
"""

FIXED = """def add(a, b):
    return a + b
"""

TEST = """import unittest

from app import add


class AddTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)


if __name__ == "__main__":
    unittest.main()
"""


def _create_project(root: Path) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "app.py").write_text(BROKEN, encoding="utf-8")
    (root / "tests" / "test_app.py").write_text(TEST, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="leos-se-demo-") as tmp:
        workspace = Path(tmp)
        _create_project(workspace)
        audit_path = workspace / "audit.jsonl"
        audit_log = AuditLog(audit_path)
        registry = default_dev_registry(workspace, include_execute=True)
        kernel = AgentKernel(
            registry=registry,
            policy=PolicyEngine(),
            causal_model=CausalGraph(),
            audit_log=audit_log,
            approval_gate=ApprovalGate(lambda step: True),
            planner_config=PlannerConfig(max_risk=RiskLevel.HIGH),
        )
        goal = Goal(
            description="Fix the failing arithmetic test",
            success_criteria=["tests pass"],
            stop_conditions=["tests pass or execution is blocked"],
        )
        proposal = PlanProposal(
            rationale="Patch the arithmetic implementation and run tests.",
            steps=[
                ActionStep("read_file", {"path": "app.py"}, "Inspect failing implementation"),
                ActionStep(
                    "patch_file",
                    {"path": "app.py", "expected_previous": BROKEN, "new_content": FIXED},
                    "Replace subtraction with addition",
                ),
                ActionStep(
                    "run_tests",
                    {"argv": ["python", "-m", "unittest", "discover", "-s", "tests"], "timeout_seconds": 10},
                    "Verify the test suite",
                ),
            ],
            expected_benefit=1.0,
        )
        loop = AgentLoop(
            kernel,
            DeterministicProposalProvider([proposal]),
            config=AgentLoopConfig(max_iterations=1),
        )
        result = loop.run(goal)
        replay = replay_audit_log(audit_log)
        trace = render_trace_markdown(audit_log.records())
        trace_path = workspace / "trace.md"
        trace_path.write_text(trace, encoding="utf-8")

        print(f"selected plan: {result.selected_plans[0].plan_id if result.selected_plans else 'none'}")
        print(f"executed steps: {len(result.selected_plans[0].steps) if result.selected_plans else 0}")
        print(f"test result: {kernel.state.facts.get('tests_ok')}")
        print(f"audit log path: {audit_path}")
        print(f"replay result: {'ok' if replay.ok else 'failed'}")
        print(f"trace markdown path: {trace_path}")
        print(f"final goal status: {result.goal.status.value}")
        return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
