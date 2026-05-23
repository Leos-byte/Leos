from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leos_agent import ActionStep, ApprovalDecisionValue, ApprovalPacket, FileApprovalGate
from leos_agent.approval_exchange import (
    build_decision_for_packet,
    read_approval_decision,
    read_approval_packet,
    write_approval_decision,
    write_approval_packet,
)


class ApprovalExchangeTests(unittest.TestCase):
    def test_packet_and_decision_file_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = _packet()
            decision = build_decision_for_packet(packet, ApprovalDecisionValue.APPROVE, "human", "ok")

            write_approval_packet(packet, root / "packet.json")
            write_approval_decision(decision, root / "decision.json")

            self.assertEqual(read_approval_packet(root / "packet.json").approval_id, packet.approval_id)
            self.assertEqual(read_approval_decision(root / "decision.json").decision, ApprovalDecisionValue.APPROVE)

    def test_file_approval_gate_writes_packet_and_denies_without_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_dir = Path(tmp) / "packets"
            decision_dir = Path(tmp) / "decisions"
            gate = FileApprovalGate(packet_dir, decision_dir)
            packet = _packet()

            decision = gate.request_packet(packet, ActionStep("tool", {}, "run"))

            self.assertEqual(decision.decision, ApprovalDecisionValue.DENY)
            self.assertTrue((packet_dir / f"{packet.approval_id}.json").exists())

    def test_file_approval_gate_reads_existing_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_dir = Path(tmp) / "packets"
            decision_dir = Path(tmp) / "decisions"
            packet = _packet()
            decision = build_decision_for_packet(packet, ApprovalDecisionValue.APPROVE, "human")
            write_approval_decision(decision, decision_dir / f"{packet.approval_id}.json")
            gate = FileApprovalGate(packet_dir, decision_dir)

            returned = gate.request_packet(packet, ActionStep("tool", {}, "run"))

            self.assertEqual(returned.decision, ApprovalDecisionValue.APPROVE)

    def test_file_approval_gate_approver_allowlist_accepts_allowed_approver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_dir = Path(tmp) / "packets"
            decision_dir = Path(tmp) / "decisions"
            packet = _packet()
            decision = build_decision_for_packet(packet, ApprovalDecisionValue.APPROVE, "alice")
            write_approval_decision(decision, decision_dir / f"{packet.approval_id}.json")
            gate = FileApprovalGate(packet_dir, decision_dir, allowed_approvers={"alice"})

            returned = gate.request_packet(packet, ActionStep("tool", {}, "run"))

            self.assertEqual(returned.decision, ApprovalDecisionValue.APPROVE)

    def test_file_approval_gate_approver_allowlist_rejects_unknown_approver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_dir = Path(tmp) / "packets"
            decision_dir = Path(tmp) / "decisions"
            packet = _packet()
            decision = build_decision_for_packet(packet, ApprovalDecisionValue.APPROVE, "mallory")
            write_approval_decision(decision, decision_dir / f"{packet.approval_id}.json")
            gate = FileApprovalGate(packet_dir, decision_dir, allowed_approvers={"alice"})

            returned = gate.request_packet(packet, ActionStep("tool", {}, "run"))

            self.assertEqual(returned.decision, ApprovalDecisionValue.DENY)
            self.assertEqual(returned.reason, "approver not allowed")

    def test_file_approval_gate_approver_allowlist_rejects_missing_approver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_dir = Path(tmp) / "packets"
            decision_dir = Path(tmp) / "decisions"
            packet = _packet()
            decision = build_decision_for_packet(packet, ApprovalDecisionValue.APPROVE, None)
            write_approval_decision(decision, decision_dir / f"{packet.approval_id}.json")
            gate = FileApprovalGate(packet_dir, decision_dir, allowed_approvers={"alice"})

            returned = gate.request_packet(packet, ActionStep("tool", {}, "run"))

            self.assertEqual(returned.decision, ApprovalDecisionValue.DENY)
            self.assertEqual(returned.reason, "approver not allowed")

    def test_file_approval_gate_rejects_broad_decision_file_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_dir = Path(tmp) / "packets"
            decision_dir = Path(tmp) / "decisions"
            packet = _packet()
            decision_path = decision_dir / f"{packet.approval_id}.json"
            decision = build_decision_for_packet(packet, ApprovalDecisionValue.APPROVE, "human")
            write_approval_decision(decision, decision_path)
            decision_path.chmod(0o644)
            gate = FileApprovalGate(packet_dir, decision_dir)

            returned = gate.request_packet(packet, ActionStep("tool", {}, "run"))

            self.assertEqual(returned.decision, ApprovalDecisionValue.DENY)
            self.assertEqual(returned.reason, "decision file permissions too broad")

    def test_file_approval_gate_can_allow_broad_decision_file_permissions_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet_dir = Path(tmp) / "packets"
            decision_dir = Path(tmp) / "decisions"
            packet = _packet()
            decision_path = decision_dir / f"{packet.approval_id}.json"
            decision = build_decision_for_packet(packet, ApprovalDecisionValue.APPROVE, "human")
            write_approval_decision(decision, decision_path)
            decision_path.chmod(0o644)
            gate = FileApprovalGate(packet_dir, decision_dir, require_private_decision_files=False)

            returned = gate.request_packet(packet, ActionStep("tool", {}, "run"))

            self.assertEqual(returned.decision, ApprovalDecisionValue.APPROVE)

    def test_approval_exchange_writes_private_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = _packet()
            decision = build_decision_for_packet(packet, ApprovalDecisionValue.APPROVE, "human")
            packet_path = root / "packet.json"
            decision_path = root / "decision.json"

            write_approval_packet(packet, packet_path)
            write_approval_decision(decision, decision_path)

            self.assertEqual(packet_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(decision_path.stat().st_mode & 0o777, 0o600)

    def test_approval_exchange_files_do_not_contain_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packet.json"
            packet = ApprovalPacket.from_mapping(
                {
                    **_packet().as_dict(),
                    "dry_run_summary": "would use ghp_should_not_leak",
                }
            )

            write_approval_packet(packet, path)

            self.assertNotIn("ghp_should_not_leak", path.read_text(encoding="utf-8"))

    def test_unsafe_approval_id_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            packet = ApprovalPacket.from_mapping({**_packet().as_dict(), "approval_id": "../bad"})
            gate = FileApprovalGate(Path(tmp) / "packets", Path(tmp) / "decisions")

            with self.assertRaises(ValueError):
                gate.request_packet(packet, ActionStep("tool", {}, "run"))


def _packet() -> ApprovalPacket:
    return ApprovalPacket.from_mapping(
        {
            "approval_id": "approval-1",
            "goal_id": "goal",
            "plan_id": "plan",
            "step_id": "step",
            "step_hash": "hash",
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
