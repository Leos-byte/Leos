"""Shared runtime enums and risk helpers."""

from __future__ import annotations

from enum import Enum
from typing import Iterable


class Permission(str, Enum):
    """Capability classes used by the policy engine."""

    READ_MEMORY = "read_memory"
    WRITE_MEMORY = "write_memory"
    READ_FILES = "read_files"
    WRITE_FILES = "write_files"
    NETWORK = "network"
    SEND_MESSAGE = "send_message"
    EXECUTE_CODE = "execute_code"
    DELETE = "delete"
    FINANCIAL = "financial"
    SYSTEM_CONFIG = "system_config"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    COMPENSATABLE = "compensatable"
    IRREVERSIBLE = "irreversible"


class CompensationStrategy(str, Enum):
    UNDO = "undo"
    COMPENSATE = "compensate"
    MANUAL = "manual"
    NONE = "none"


class Decision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    NEEDS_HUMAN = "needs_human"


class StepStatus(str, Enum):
    PENDING = "pending"
    DRY_RUN_OK = "dry_run_ok"
    EXECUTED = "executed"
    VERIFIED = "verified"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    BLOCKED = "blocked"


_RISK_VALUES = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


def _risk_value(risk: RiskLevel) -> int:
    return _RISK_VALUES[risk]


def _max_risk(risks: Iterable[RiskLevel]) -> RiskLevel:
    return max(risks, key=_risk_value, default=RiskLevel.LOW)
