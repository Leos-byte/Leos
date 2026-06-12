"""File-based approval packet exchange for non-interactive runs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from .approval import ApprovalDecision, ApprovalDecisionValue, ApprovalPacket
from .plans import ActionStep
from .policy import ApprovalGate
from .sanitization import redact_secrets, safe_json_dumps


def write_approval_packet(packet: ApprovalPacket, path: Path) -> None:
    _write_json(path, packet.as_dict())


def read_approval_packet(path: Path) -> ApprovalPacket:
    return ApprovalPacket.from_mapping(_read_json(path))


def write_approval_decision(decision: ApprovalDecision, path: Path, *, signature: str | None = None) -> None:
    data = decision.as_dict()
    if signature is not None:
        data["signature"] = signature
    _write_json(path, data)


def read_approval_decision(path: Path) -> ApprovalDecision:
    return _decision_from_mapping(_read_json(path))


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


def sign_approval_decision(decision: ApprovalDecision, secret: str) -> str:
    """Return an HMAC signature for an approval decision JSON payload."""

    digest = hmac.new(secret.encode("utf-8"), _decision_signature_payload(decision), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def verify_approval_decision_signature(decision: ApprovalDecision, secret: str, signature: str) -> bool:
    expected = sign_approval_decision(decision, secret)
    return hmac.compare_digest(expected, signature)


class FileApprovalGate(ApprovalGate):
    """Writes approval packets to disk and consumes matching decision files."""

    def __init__(
        self,
        packet_dir: Path,
        decision_dir: Path,
        timeout_seconds: float = 0.0,
        allowed_approvers: set[str] | None = None,
        require_private_decision_files: bool = True,
        signature_secret: str | None = None,
        require_signature: bool = False,
    ) -> None:
        super().__init__(approver=None)
        self.packet_dir = packet_dir
        self.decision_dir = decision_dir
        self.timeout_seconds = timeout_seconds
        self.allowed_approvers = allowed_approvers
        self.require_private_decision_files = require_private_decision_files
        self.signature_secret = signature_secret
        self.require_signature = require_signature
        self.signed_approval_enforced = bool(require_signature and signature_secret)
        self.last_decision_signature_valid = False
        self.last_decision_signature_algorithm: str | None = None
        self.packet_dir.mkdir(parents=True, exist_ok=True)
        self.decision_dir.mkdir(parents=True, exist_ok=True)
        self._consumed_approval_ids: set[str] = set()

    def request_packet(self, packet: ApprovalPacket, step: ActionStep) -> ApprovalDecision:
        del step
        self.last_decision_signature_valid = False
        self.last_decision_signature_algorithm = None
        packet_path = self.packet_dir / f"{_safe_approval_id(packet.approval_id)}.json"
        write_approval_packet(packet, packet_path)
        decision_path = self.decision_dir / f"{_safe_approval_id(packet.approval_id)}.json"
        if decision_path.exists():
            permission_issue = self._decision_file_permission_issue(decision_path)
            if permission_issue is not None:
                return ApprovalDecision(
                    packet.approval_id,
                    packet.step_hash,
                    ApprovalDecisionValue.DENY,
                    reason=permission_issue,
                )
            decision_data = _read_json(decision_path)
            decision = _decision_from_mapping(decision_data)
            allowlist_issue = self._approver_allowlist_issue(decision)
            if allowlist_issue is not None:
                return ApprovalDecision(
                    packet.approval_id,
                    packet.step_hash,
                    ApprovalDecisionValue.DENY,
                    approver=decision.approver,
                    reason=allowlist_issue,
                )
            signature_issue = self._signature_issue(decision, decision_data)
            if signature_issue is not None:
                return ApprovalDecision(
                    packet.approval_id,
                    packet.step_hash,
                    ApprovalDecisionValue.DENY,
                    approver=decision.approver,
                    reason=signature_issue,
                )
            if self.require_signature:
                self.last_decision_signature_valid = True
                self.last_decision_signature_algorithm = "hmac-sha256"
            return decision
        return ApprovalDecision(packet.approval_id, packet.step_hash, ApprovalDecisionValue.DENY)

    def _approver_allowlist_issue(self, decision: ApprovalDecision) -> str | None:
        if self.allowed_approvers is None:
            return None
        if decision.approver is None:
            return "approver required"
        if decision.approver not in self.allowed_approvers:
            return "approver not allowed"
        return None

    def _signature_issue(self, decision: ApprovalDecision, data: Mapping[str, Any]) -> str | None:
        if not self.require_signature:
            return None
        if not self.signature_secret:
            return "approval decision signature required"
        signature = data.get("signature")
        if not isinstance(signature, str) or not signature:
            return "approval decision signature required"
        if not verify_approval_decision_signature(decision, self.signature_secret, signature):
            return "approval decision signature invalid"
        return None

    def consume_approval(
        self,
        packet: ApprovalPacket,
        decision: ApprovalDecision,
        step: ActionStep,
    ) -> str | None:
        """Track consumed approval IDs to prevent replay of the same decision."""
        if packet.approval_id in self._consumed_approval_ids:
            return f"Approval {packet.approval_id} was already consumed"
        self._consumed_approval_ids.add(packet.approval_id)
        return None

    def _decision_file_permission_issue(self, path: Path) -> str | None:
        if not self.require_private_decision_files:
            return None
        if os.name == "nt":
            return None
        try:
            mode = path.stat().st_mode
        except OSError:
            return "decision file permission check failed"
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


def _decision_from_mapping(data: Mapping[str, Any]) -> ApprovalDecision:
    decided_at = data.get("decided_at", time.time())
    return ApprovalDecision(
        approval_id=str(data["approval_id"]),
        step_hash=str(data["step_hash"]),
        decision=ApprovalDecisionValue(str(data["decision"])),
        decided_at=float(decided_at) if isinstance(decided_at, (str, int, float)) else time.time(),
        approver=str(data["approver"]) if data.get("approver") is not None else None,
        reason=str(data["reason"]) if data.get("reason") is not None else None,
    )


def _decision_signature_payload(decision: ApprovalDecision) -> bytes:
    payload = {
        "approval_id": decision.approval_id,
        "step_hash": decision.step_hash,
        "decision": decision.decision.value,
        "decided_at": decision.decided_at,
        "approver": decision.approver,
        "reason": decision.reason,
    }
    safe_payload = redact_secrets(payload)
    return json.dumps(safe_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_approval_id(approval_id: str) -> str:
    safe = "".join(ch for ch in approval_id if ch.isalnum() or ch in {"-", "_"})
    if not safe or safe != approval_id:
        raise ValueError("approval_id contains unsafe path characters")
    return safe


def _chmod_private(path: Path) -> None:
    with suppress(OSError):
        path.chmod(0o600)
