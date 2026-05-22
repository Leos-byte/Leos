from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from leos_agent import (
    ActionStep,
    AgentKernel,
    ApprovalDecision,
    ApprovalDecisionValue,
    ApprovalGate,
    Goal,
    InteractiveApprovalGate,
    PolicyEngine,
    SafeFileWriteTool,
    ToolRegistry,
)
from leos_agent.approval import (
    build_approval_packet,
    build_step_hash,
    render_approval_packet_html,
    render_approval_packet_markdown,
    validate_approval_decision,
)


class _PacketGate(ApprovalGate):
    def __init__(self, decision: ApprovalDecisionValue = ApprovalDecisionValue.APPROVE) -> None:
        super().__init__()
        self.decision_value = decision
        self.packets = []

    def request_packet(self, packet, step):
        self.packets.append(packet)
        return ApprovalDecision(packet.approval_id, packet.step_hash, self.decision_value)


class ApprovalPacketTests(unittest.TestCase):
    def test_approval_packet_created_and_used_for_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = _PacketGate()
            kernel = _kernel(Path(tmp), gate)
            plan = kernel.build_plan(
                Goal("write", ["file written"], stop_conditions=["done"]),
                [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "write")],
            )

            result = kernel.run(plan)

            self.assertEqual(result.steps[0].status.value, "verified")
            self.assertEqual(len(gate.packets), 1)
            self.assertTrue(any(event.event_type == "approval.packet_created" for event in kernel.audit_log.events))
            self.assertTrue(any(event.event_type == "approval.used" for event in kernel.audit_log.events))

    def test_dry_run_only_decision_does_not_execute_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = _PacketGate(ApprovalDecisionValue.DRY_RUN_ONLY)
            kernel = _kernel(Path(tmp), gate)
            plan = kernel.build_plan(
                Goal("write", ["file written"], stop_conditions=["done"]),
                [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "write")],
            )

            result = kernel.run(plan)

            self.assertEqual(result.steps[0].status.value, "blocked")
            self.assertFalse((Path(tmp) / "x.txt").exists())
            self.assertTrue(any(event.event_type == "approval.dry_run_only" for event in kernel.audit_log.events))
            self.assertTrue(any(event.event_type == "approval.rejected" for event in kernel.audit_log.events))

    def test_narrow_scope_decision_does_not_execute_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gate = _PacketGate(ApprovalDecisionValue.NARROW_SCOPE)
            kernel = _kernel(Path(tmp), gate)
            plan = kernel.build_plan(
                Goal("write", ["file written"], stop_conditions=["done"]),
                [ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "write")],
            )

            result = kernel.run(plan)

            self.assertEqual(result.steps[0].status.value, "blocked")
            self.assertFalse((Path(tmp) / "x.txt").exists())
            self.assertTrue(
                any(event.event_type == "approval.narrow_scope_requested" for event in kernel.audit_log.events)
            )

    def test_changed_args_invalidate_approval_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = SafeFileWriteTool(Path(tmp))
            step = ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "write")
            step.required_permissions = tuple(tool.spec.permissions)
            step.risk = tool.spec.default_risk
            goal = Goal("write", ["file written"], stop_conditions=["done"])
            plan = _kernel(Path(tmp), _PacketGate()).build_plan(goal, [step])
            packet = build_approval_packet(plan=plan, step=step, tool=tool, dry_run_summary="dry", profile="custom")
            step.arguments["content"] = "changed"
            current_hash = build_step_hash(goal_id=plan.goal.goal_id, plan_id=plan.plan_id, step=step, tool=tool)
            decision = ApprovalDecision(packet.approval_id, packet.step_hash, ApprovalDecisionValue.APPROVE)

            self.assertEqual(
                validate_approval_decision(packet, decision, current_step_hash=current_hash, profile="custom"),
                "step_hash mismatch",
            )

    def test_changed_tool_invalidates_approval_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = SafeFileWriteTool(Path(tmp))
            step = ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "write")
            step.required_permissions = tuple(tool.spec.permissions)
            step.risk = tool.spec.default_risk
            plan = _kernel(Path(tmp), _PacketGate()).build_plan(
                Goal("write", ["file written"], stop_conditions=["done"]),
                [step],
            )
            packet = build_approval_packet(plan=plan, step=step, tool=tool, dry_run_summary="dry", profile="custom")
            step.tool_name = "other_tool"
            current_hash = build_step_hash(goal_id=plan.goal.goal_id, plan_id=plan.plan_id, step=step, tool=tool)
            decision = ApprovalDecision(packet.approval_id, packet.step_hash, ApprovalDecisionValue.APPROVE)

            self.assertEqual(
                validate_approval_decision(packet, decision, current_step_hash=current_hash, profile="custom"),
                "step_hash mismatch",
            )

    def test_expired_approval_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = SafeFileWriteTool(Path(tmp))
            step = ActionStep("safe_file_write", {"path": "x.txt", "content": "x"}, "write")
            step.required_permissions = tuple(tool.spec.permissions)
            step.risk = tool.spec.default_risk
            plan = _kernel(Path(tmp), _PacketGate()).build_plan(
                Goal("write", ["file written"], stop_conditions=["done"]),
                [step],
            )
            packet = build_approval_packet(plan=plan, step=step, tool=tool, dry_run_summary="dry", profile="custom")
            expired = type(packet)(**{**packet.as_dict(), "expires_at": time.time() - 1})
            decision = ApprovalDecision(expired.approval_id, expired.step_hash, ApprovalDecisionValue.APPROVE)

            self.assertEqual(
                validate_approval_decision(expired, decision, current_step_hash=expired.step_hash, profile="custom"),
                "approval expired",
            )

    def test_renderers_include_risk_and_escape_html(self) -> None:
        data = {
            "approval_id": "a",
            "goal_id": "g",
            "plan_id": "p",
            "step_id": "s",
            "step_hash": "h",
            "tool_name": "tool<script>",
            "action_summary": "act",
            "risk_level": "high",
            "required_permissions": ["write_files"],
            "causal_contract_summary": "contract",
            "dry_run_summary": "dry",
            "rollback_summary": "rollback",
            "diff_summary": "diff",
            "alternatives": ["deny", "narrow"],
            "requester": "tester",
        }
        from leos_agent import ApprovalPacket

        packet = ApprovalPacket.from_mapping(data)
        markdown = render_approval_packet_markdown(packet)
        self.assertIn("risk_level", markdown)
        self.assertIn("step_hash", markdown)
        self.assertIn("action_summary", markdown)
        self.assertIn("alternatives", markdown)
        self.assertIn("diff_summary", markdown)
        html = render_approval_packet_html(packet)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_dry_run_only_validation_reason_is_specific(self) -> None:
        packet = _packet()
        decision = ApprovalDecision(packet.approval_id, packet.step_hash, ApprovalDecisionValue.DRY_RUN_ONLY)

        self.assertEqual(
            validate_approval_decision(packet, decision, current_step_hash=packet.step_hash, profile="custom"),
            "approval decision is dry_run_only",
        )

    def test_narrow_scope_validation_reason_is_specific(self) -> None:
        packet = _packet()
        decision = ApprovalDecision(packet.approval_id, packet.step_hash, ApprovalDecisionValue.NARROW_SCOPE)

        self.assertEqual(
            validate_approval_decision(packet, decision, current_step_hash=packet.step_hash, profile="custom"),
            "approval decision requires narrowed scope",
        )

    def test_non_tty_interactive_packet_defaults_deny(self) -> None:
        packet = _packet()

        decision = InteractiveApprovalGate().request_packet(packet, ActionStep("echo", {}, "echo"))

        self.assertIs(decision.decision, ApprovalDecisionValue.DENY)


def _kernel(workspace: Path, gate: ApprovalGate) -> AgentKernel:
    registry = ToolRegistry()
    registry.register(SafeFileWriteTool(workspace))
    return AgentKernel(registry, PolicyEngine(), approval_gate=gate)


def _packet():
    from leos_agent import ApprovalPacket

    return ApprovalPacket.from_mapping(
        {
            "approval_id": "a",
            "goal_id": "g",
            "plan_id": "p",
            "step_id": "s",
            "step_hash": "h",
            "tool_name": "tool",
            "risk_level": "medium",
            "required_permissions": [],
            "causal_contract_summary": "none",
            "dry_run_summary": "dry",
            "rollback_summary": "rollback",
        }
    )


if __name__ == "__main__":
    unittest.main()
