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
    GoalStatus,
    Permission,
    Reversibility,
    RiskLevel,
    StepStatus,
    TaskStatus,
    _max_risk,
    _risk_value,
)
from .errors import (
    BudgetExceeded,
    DryRunFailed,
    IdempotencyConflict,
    InvalidGoalTransition,
    LeosError,
    PolicyConfigurationError,
    PolicyDenied,
    PostconditionFailed,
    PreconditionFailed,
    RollbackFailed,
    SchemaValidationFailed,
    SecretBoundaryViolation,
    ToolTimeout,
    VerificationFailed,
    WorkspaceEscapeBlocked,
)
from .goals import Goal, ResourceBudget
from .kernel import AgentKernel
from .memory import MemoryRecord, MemorySensitivity, MemoryStore, MemoryType
from .manifest import ToolManifest, validate_json_schema
from .planner import Planner
from .plans import ActionStep, PlanCandidate, PlanProposal, PlanScore, PlannerConfig, PlannerResult, StateCondition, TransactionPlan
from .policy import ApprovalGate, BUILT_IN_POLICY_PROFILES, PolicyEngine, PolicyProfile, PolicyRule, validate_policy_config
from .replay import AuditReplayer, ReplayResult, replay_audit_log
from .state import TrustLevel, WorldState
from .task_queue import RetryPolicy, RuntimeTask, TaskQueue, TaskRunner, TimeoutPolicy, Watchdog
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
    "BudgetExceeded",
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
    "GoalStatus",
    "IdempotencyConflict",
    "InvalidGoalTransition",
    "LeosError",
    "MemoryRecord",
    "MemorySensitivity",
    "MemoryStore",
    "MemoryType",
    "Permission",
    "PlanCandidate",
    "PlanProposal",
    "PlanScore",
    "Planner",
    "PlannerConfig",
    "PlannerResult",
    "PolicyConfigurationError",
    "PolicyDenied",
    "PolicyEngine",
    "PolicyProfile",
    "PolicyRule",
    "PostconditionFailed",
    "PreconditionFailed",
    "Reversibility",
    "RiskLevel",
    "ReplayResult",
    "ResourceBudget",
    "RetryPolicy",
    "RollbackFailed",
    "RuntimeTask",
    "SafeFileWriteTool",
    "SchemaValidationFailed",
    "SecretBoundaryViolation",
    "StateCondition",
    "StepStatus",
    "TaskQueue",
    "TaskRunner",
    "TaskStatus",
    "Tool",
    "ToolManifest",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolTimeout",
    "TransactionManager",
    "TransactionPlan",
    "TrustLevel",
    "TimeoutPolicy",
    "VerificationFailed",
    "WorkspaceEscapeBlocked",
    "WorldState",
    "Watchdog",
    "default_registry",
    "replay_audit_log",
    "validate_policy_config",
    "validate_json_schema",
]
