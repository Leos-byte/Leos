"""File-based approval packet exchange for non-interactive runs."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .approval import ApprovalDecision, ApprovalDecisionValue, ApprovalPacket
from .plans import ActionStep
from .policy import ApprovalGate
from .sanitization import safe_json_dumps


def write_approval_packet(packet: ApprovalPacket, path: Path) -> None:
    _write_json(path, packet.as_dict())


def read_approval_packet(path: Path) -> ApprovalPacket:
    return ApprovalPacket.from_mapping(_read_json(path))


def write_approval_decision(decision: ApprovalDecision, path: Path) -> None:
    _write_json(path, decision.as_dict())


def read_approval_decision(path: Path) -> ApprovalDecision:
    data = _read_json(path)
    decided_at = data.get("decided_at", time.time())
    return ApprovalDecision(
        approval_id=str(data["approval_id"]),
        step_hash=str(data["step_hash"]),
        decision=ApprovalDecisionValue(str(data["decision"])),
        decided_at=float(decided_at) if isinstance(decided_at, (str, int, float)) else time.time(),
        approver=str(data["approver"]) if data.get("approver") is not None else None,
        reason=str(data["reason"]) if data.get("reason") is not None else None,
    )


def build_decision_for_packet(
    packet: ApprovalPacket,
    decision_value: ApprovalDecisionValue | str,
    approver: str | None,
    reason: str | None = None,
) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=packet.approval_id,
        step_hash=packet.step_hash,
        decision=ApprovalDecisionValue(decision_value),
        approver=approver,
        reason=reason,
    )


class FileApprovalGate(ApprovalGate):
    """Writes approval packets to disk and consumes matching decision files."""

    def __init__(
        self,
        packet_dir: Path,
        decision_dir: Path,
        timeout_seconds: float = 0.0,
        allowed_approvers: set[str] | None = None,
        require_private_decision_files: bool = True,
    ) -> None:
        super().__init__(approver=None)
        self.packet_dir = packet_dir
        self.decision_dir = decision_dir
        self.timeout_seconds = timeout_seconds
        self.allowed_approvers = set(allowed_approvers) if allowed_approvers is not None else None
        self.require_private_decision_files = require_private_decision_files
        self.packet_dir.mkdir(parents=True, exist_ok=True)
        self.decision_dir.mkdir(parents=True, exist_ok=True)

    def request_packet(self, packet: ApprovalPacket, step: ActionStep) -> ApprovalDecision:
        del step
        packet_path = self.packet_dir / f"{_safe_approval_id(packet.approval_id)}.json"
        write_approval_packet(packet, packet_path)
        decision_path = self.decision_dir / f"{_safe_approval_id(packet.approval_id)}.json"
        if decision_path.exists():
            permission_issue = self._decision_file_permission_issue(decision_path)
            if permission_issue is not None:
                return _deny(packet, permission_issue)
            decision = read_approval_decision(decision_path)
            if self.allowed_approvers is not None and (
                decision.approver is None or decision.approver not in self.allowed_approvers
            ):
                return _deny(packet, "approver not allowed")
            return decision
        return ApprovalDecision(packet.approval_id, packet.step_hash, ApprovalDecisionValue.DENY)

    def _decision_file_permission_issue(self, path: Path) -> str | None:
        if not self.require_private_decision_files:
            return None
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            return "decision file permissions could not be checked"
        if mode & 0o077:
            return "decision file permissions too broad"
        return None


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(safe_json_dumps(data), encoding="utf-8")
    _chmod_private(path)


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("approval exchange file must contain a JSON object")
    return data


def _safe_approval_id(approval_id: str) -> str:
    safe = "".join(ch for ch in approval_id if ch.isalnum() or ch in {"-", "_"})
    if not safe or safe != approval_id:
        raise ValueError("approval_id contains unsafe path characters")
    return safe


def _deny(packet: ApprovalPacket, reason: str) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=packet.approval_id,
        step_hash=packet.step_hash,
        decision=ApprovalDecisionValue.DENY,
        reason=reason,
    )


def _chmod_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        return
