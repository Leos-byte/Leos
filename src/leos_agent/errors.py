"""Typed runtime errors for Leos safety boundaries."""

from __future__ import annotations


class LeosError(Exception):
    """Base class for typed Leos runtime failures."""


class PolicyDenied(LeosError):
    """Raised or recorded when policy blocks an action."""


class DryRunFailed(LeosError):
    """Raised or recorded when a dry-run check fails."""


class VerificationFailed(LeosError):
    """Raised or recorded when post-action verification fails."""


class RollbackFailed(LeosError):
    """Raised or recorded when rollback cannot restore the prior state."""


class ToolTimeout(LeosError):
    """Raised or recorded when a tool exceeds its execution budget."""


class WorkspaceEscapeBlocked(LeosError):
    """Raised or recorded when a path escapes the configured workspace."""


class SchemaValidationFailed(LeosError):
    """Raised or recorded when structured input or output validation fails."""


class BudgetExceeded(LeosError):
    """Raised or recorded when a goal or plan exceeds its resource budget."""


class PreconditionFailed(LeosError):
    """Raised or recorded when a step precondition is not satisfied."""


class PostconditionFailed(LeosError):
    """Raised or recorded when a step postcondition is not satisfied."""


class IdempotencyConflict(LeosError):
    """Raised or recorded when an idempotency key was already consumed."""


class InvalidGoalTransition(LeosError):
    """Raised when a goal lifecycle transition is not allowed."""


class SecretBoundaryViolation(LeosError):
    """Raised when a secret value attempts to cross into memory or audit state."""


class PolicyConfigurationError(LeosError):
    """Raised when policy-as-code configuration is invalid or unsafe."""
