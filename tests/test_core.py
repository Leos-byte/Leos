from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leos_agent import (
    ActionStep,
    AgentKernel,
    ApprovalGate,
    CausalHypothesis,
    CausalWorldModel,
    Goal,
    Permission,
    PolicyEngine,
    StepStatus,
    default_registry,
)


class AgentKernelTests(unittest.TestCase):
    def test_low_risk_echo_runs_and_verifies(self) -> None:
        registry = default_registry()
        agent = AgentKernel(registry=registry, policy=PolicyEngine())
        goal = Goal(
            description="Echo a message",
            success_criteria=["last_echo equals the message"],
            stop_conditions=["one step complete"],
        )
        plan = agent.build_plan(goal, [ActionStep("echo", {"message": "hello"}, "test echo")])

        result = agent.run(plan)

        self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
        self.assertEqual(agent.state.facts["last_echo"], "hello")

    def test_file_write_requires_permission_or_human_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(registry=registry, policy=PolicyEngine())
            goal = Goal(
                description="Write a file",
                success_criteria=["file exists"],
                stop_conditions=["blocked or verified"],
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "test")])

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
            self.assertFalse((Path(tmp) / "x.txt").exists())

    def test_approved_file_write_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_path = str((Path(tmp) / "x.txt").resolve())
            registry = default_registry(Path(tmp))
            causal = CausalWorldModel(
                [
                    CausalHypothesis(
                        action_name="safe_file_write",
                        affected_variables=["file_written"],
                        rationale="Writing a file updates file_written",
                        confidence=0.9,
                    )
                ]
            )
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                causal_model=causal,
                approval_gate=ApprovalGate(lambda step: True),
            )
            goal = Goal(
                description="Write a file",
                success_criteria=["file exists"],
                stop_conditions=["verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {"path": "x.txt", "content": "x", "file_written": expected_path},
                        "test",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
            self.assertEqual((Path(tmp) / "x.txt").read_text(encoding="utf-8"), "x")

    def test_workspace_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                approval_gate=ApprovalGate(lambda step: True),
            )
            goal = Goal(
                description="Attempt escaping workspace",
                success_criteria=["escape is rejected"],
                stop_conditions=["failed"],
            )
            plan = agent.build_plan(goal, [ActionStep("safe_file_write", {"path": "../x.txt", "content": "x"}, "test")])

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
