"""Command-line demo for the Leos agent kernel."""

from __future__ import annotations

import argparse
from pathlib import Path

from .core import (
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


def build_demo_agent(workspace: Path, auto_approve: bool) -> AgentKernel:
    registry = default_registry(workspace)
    policy = PolicyEngine(granted_permissions={Permission.WRITE_FILES})
    causal = CausalWorldModel(
        [
            CausalHypothesis(
                action_name="safe_file_write",
                affected_variables=["file_written"],
                rationale="Writing a file should update the last written file path.",
                confidence=0.9,
            )
        ]
    )
    approval = ApprovalGate(lambda step: auto_approve)
    return AgentKernel(registry=registry, policy=policy, causal_model=causal, approval_gate=approval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Leos autonomous-agent kernel demo.")
    parser.add_argument("--workspace", default=".leos-workspace", help="Sandbox workspace for reversible file actions.")
    parser.add_argument("--auto-approve", action="store_true", help="Approve medium/high-risk demo actions.")
    args = parser.parse_args()

    agent = build_demo_agent(Path(args.workspace), auto_approve=args.auto_approve)
    goal = Goal(
        description="Create a hello file through a transactionally verified action.",
        success_criteria=["hello.txt exists in the sandbox workspace"],
        constraints=["Do not write outside the sandbox workspace"],
        stop_conditions=["Stop after file verification or policy denial"],
    )
    plan = agent.build_plan(
        goal,
        [
            ActionStep(
                tool_name="safe_file_write",
                arguments={"path": "hello.txt", "content": "Hello from Leos agent.\n", "file_written": str(Path(args.workspace).resolve() / "hello.txt")},
                reason="Demonstrate permissioned, reversible, verified action.",
            )
        ],
    )
    result = agent.run(plan)
    for step in result.steps:
        print(f"{step.tool_name}: {step.status.value} risk={step.risk.value}")
    return 0 if all(step.status is StepStatus.VERIFIED for step in result.steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
