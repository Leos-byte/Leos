"""Append-only audit log with hash-chain integrity checks."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .errors import VerificationFailed
from .tools import ToolResult


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    message: str
    payload: Dict[str, Any]
    sequence: int = 0
    previous_hash: str = ""
    event_hash: str = ""
    created_at: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class AuditLog:
    """Append-only JSONL audit log."""

    GENESIS_HASH = "0" * 64

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path
        self.events: List[AuditEvent] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, message: str, **payload: Any) -> AuditEvent:
        previous_hash = self.events[-1].event_hash if self.events else self.GENESIS_HASH
        event = AuditEvent(
            event_type=event_type,
            message=message,
            payload=payload,
            sequence=len(self.events) + 1,
            previous_hash=previous_hash,
        )
        object.__setattr__(event, "event_hash", self._hash_event_record(asdict(event)))
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")
        return event

    def verify_integrity(self) -> ToolResult:
        return self.verify_event_records(self.records())

    def records(self) -> List[Dict[str, Any]]:
        return self._records_from_path() if self.path else [asdict(event) for event in self.events]

    @classmethod
    def verify_event_records(cls, records: Sequence[Mapping[str, Any]]) -> ToolResult:
        issues = []
        expected_sequence = 1
        expected_previous_hash = cls.GENESIS_HASH
        for index, record in enumerate(records):
            sequence = record.get("sequence")
            previous_hash = record.get("previous_hash")
            event_hash = record.get("event_hash")
            computed_hash = cls._hash_event_record(record)
            if sequence != expected_sequence:
                issues.append(
                    {
                        "index": index,
                        "reason": "sequence_mismatch",
                        "expected": expected_sequence,
                        "observed": sequence,
                    }
                )
            if previous_hash != expected_previous_hash:
                issues.append(
                    {
                        "index": index,
                        "reason": "previous_hash_mismatch",
                        "expected": expected_previous_hash,
                        "observed": previous_hash,
                    }
                )
            if event_hash != computed_hash:
                issues.append(
                    {
                        "index": index,
                        "reason": "event_hash_mismatch",
                        "expected": computed_hash,
                        "observed": event_hash,
                    }
                )
            expected_sequence += 1
            expected_previous_hash = str(event_hash or "")
        if issues:
            return ToolResult(
                False,
                "Audit integrity verification failed",
                {"issues": issues},
                error=VerificationFailed("Audit integrity verification failed"),
            )
        return ToolResult(True, "Audit integrity verification passed")

    def _records_from_path(self) -> List[Dict[str, Any]]:
        if not self.path or not self.path.exists():
            return []
        records = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        return records

    @staticmethod
    def _hash_event_record(record: Mapping[str, Any]) -> str:
        hashable = {key: value for key, value in record.items() if key != "event_hash"}
        encoded = json.dumps(hashable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
