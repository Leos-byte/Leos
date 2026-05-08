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
