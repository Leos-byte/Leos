from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leos_agent import (
    ActionStep,
    AgentKernel,
    ApprovalGate,
    CausalGraph,
    CausalHypothesis,
    CausalWorldModel,
    Goal,
    Permission,
    PlanProposal,
    PlannerConfig,
    PolicyEngine,
    RiskLevel,
    StepStatus,
    default_registry,
)
from leos_agent.cli import build_demo_agent


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

    def test_cli_demo_requires_auto_approval_for_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            agent = build_demo_agent(workspace, auto_approve=False)
            goal = Goal(
                description="Demo write",
                success_criteria=["file exists"],
                stop_conditions=["blocked or verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {
                            "path": "hello.txt",
                            "content": "hello",
                            "file_written": str((workspace / "hello.txt").resolve()),
                        },
                        "demo",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.BLOCKED)
            self.assertFalse((workspace / "hello.txt").exists())

    def test_cli_demo_auto_approval_allows_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            agent = build_demo_agent(workspace, auto_approve=True)
            goal = Goal(
                description="Demo write",
                success_criteria=["file exists"],
                stop_conditions=["blocked or verified"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {
                            "path": "hello.txt",
                            "content": "hello",
                            "file_written": str((workspace / "hello.txt").resolve()),
                        },
                        "demo",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.VERIFIED)
            self.assertEqual((workspace / "hello.txt").read_text(encoding="utf-8"), "hello")

    def test_planner_selects_first_satisfactory_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            agent = AgentKernel(
                registry=registry,
                policy=PolicyEngine(granted_permissions={Permission.WRITE_FILES}),
                planner_config=PlannerConfig(max_risk=RiskLevel.MEDIUM, max_cost=5.0, min_benefit=0.5),
            )
            goal = Goal(
                description="Choose a plan",
                success_criteria=["a candidate is selected"],
                stop_conditions=["selected"],
            )
            proposals = [
                PlanProposal(
                    steps=[ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "too costly")],
                    rationale="Too costly",
                    estimated_cost=10.0,
                    expected_benefit=1.0,
                ),
                PlanProposal(
                    steps=[ActionStep("echo", {"message": "hello"}, "low cost")],
                    rationale="Satisfactory",
                    estimated_cost=1.0,
                    expected_benefit=0.75,
                ),
                PlanProposal(
                    steps=[ActionStep("echo", {"message": "later"}, "also acceptable")],
                    rationale="Also satisfactory",
                    estimated_cost=1.0,
                    expected_benefit=1.0,
                ),
            ]

            result = agent.plan(goal, proposals)

            self.assertIs(result.selected, result.candidates[1])
            self.assertFalse(result.candidates[0].score.satisfies)
            self.assertTrue(result.candidates[1].score.satisfies)

    def test_planner_returns_no_selection_without_satisfactory_candidate(self) -> None:
        registry = default_registry()
        agent = AgentKernel(
            registry=registry,
            policy=PolicyEngine(),
            planner_config=PlannerConfig(max_risk=RiskLevel.LOW, max_cost=1.0, min_benefit=2.0),
        )
        goal = Goal(
            description="Choose a plan",
            success_criteria=["a candidate is selected"],
            stop_conditions=["selected"],
        )
        proposals = [
            PlanProposal(
                steps=[ActionStep("echo", {"message": "hello"}, "not enough benefit")],
                rationale="Low benefit",
                estimated_cost=0.5,
                expected_benefit=1.0,
            )
        ]

        result = agent.plan(goal, proposals)

        self.assertIsNone(result.selected)
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.candidates[0].score.satisfies)

    def test_causal_graph_reports_action_consequences_and_counterfactuals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_path = str((Path(tmp) / "x.txt").resolve())
            registry = default_registry(Path(tmp))
            causal = CausalGraph(
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
            step = result.steps[0]

            self.assertEqual(step.status, StepStatus.VERIFIED)
            self.assertEqual(step.predictions[0].variable, "file_written")
            self.assertIsNotNone(step.counterfactual_report)
            self.assertEqual(step.counterfactual_report.action_consequences[0].expected_after, expected_path)
            self.assertIsNone(step.counterfactual_report.no_action_consequences[0].expected_after)

    def test_causal_graph_verification_reports_consequence_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = default_registry(Path(tmp))
            causal = CausalGraph(
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
            )
            goal = Goal(
                description="Write a file",
                success_criteria=["file exists"],
                stop_conditions=["failed"],
            )
            plan = agent.build_plan(
                goal,
                [
                    ActionStep(
                        "safe_file_write",
                        {"path": "x.txt", "content": "x", "file_written": "wrong-path"},
                        "test",
                    )
                ],
            )

            result = agent.run(plan)

            self.assertEqual(result.steps[0].status, StepStatus.ROLLED_BACK)
            failures = [event for event in agent.audit_log.events if event.event_type == "step.verification_failed"]
            self.assertEqual(failures[0].payload["data"]["mismatches"][0]["reason"], "consequence_mismatch")


if __name__ == "__main__":
    unittest.main()
