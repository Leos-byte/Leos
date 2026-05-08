"""Backward-compatible public surface for the Leos agent runtime.

The runtime is split across focused modules. Importing from `leos_agent.core`
continues to work for existing callers.
"""

from __future__ import annotations

from .audit import AuditEvent, AuditLog
from .causal import (
    ActionConsequence,
    CausalEffect,
    CausalGraph,
    CausalHypothesis,
    CausalWorldModel,
    CounterfactualReport,
    CounterfactualReview,
    EffectPrediction,
)
from .enums import (
    CompensationStrategy,
    Decision,
    Permission,
    Reversibility,
    RiskLevel,
    StepStatus,
    _max_risk,
    _risk_value,
)
from .errors import (
    DryRunFailed,
    LeosError,
    PolicyDenied,
    RollbackFailed,
    SchemaValidationFailed,
    ToolTimeout,
    VerificationFailed,
    WorkspaceEscapeBlocked,
)
from .goals import Goal
from .kernel import AgentKernel
from .memory import MemoryStore
from .manifest import ToolManifest, validate_json_schema
from .planner import Planner
from .plans import ActionStep, PlanCandidate, PlanProposal, PlanScore, PlannerConfig, PlannerResult, TransactionPlan
from .policy import ApprovalGate, BUILT_IN_POLICY_PROFILES, PolicyEngine, PolicyProfile
from .replay import AuditReplayer, ReplayResult, replay_audit_log
from .state import TrustLevel, WorldState
from .tools import EchoTool, SafeFileWriteTool, Tool, ToolRegistry, ToolResult, ToolSpec, default_registry
from .transactions import TransactionManager, _error_type

__all__ = [
    "ActionConsequence",
    "ActionStep",
    "AgentKernel",
    "ApprovalGate",
    "AuditEvent",
    "AuditLog",
    "AuditReplayer",
    "BUILT_IN_POLICY_PROFILES",
    "CausalEffect",
    "CausalGraph",
    "CausalHypothesis",
    "CausalWorldModel",
    "CompensationStrategy",
    "CounterfactualReport",
    "CounterfactualReview",
    "Decision",
    "DryRunFailed",
    "EchoTool",
    "EffectPrediction",
    "Goal",
    "LeosError",
    "MemoryStore",
    "Permission",
    "PlanCandidate",
    "PlanProposal",
    "PlanScore",
    "Planner",
    "PlannerConfig",
    "PlannerResult",
    "PolicyDenied",
    "PolicyEngine",
    "PolicyProfile",
    "Reversibility",
    "RiskLevel",
    "ReplayResult",
    "RollbackFailed",
    "SafeFileWriteTool",
    "SchemaValidationFailed",
    "StepStatus",
    "Tool",
    "ToolManifest",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolTimeout",
    "TransactionManager",
    "TransactionPlan",
    "TrustLevel",
    "VerificationFailed",
    "WorkspaceEscapeBlocked",
    "WorldState",
    "default_registry",
    "replay_audit_log",
    "validate_json_schema",
]
