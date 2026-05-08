"""Goal model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class Goal:
    """A user or system goal with explicit success and stop conditions."""

    description: str
    success_criteria: Sequence[str]
    constraints: Sequence[str] = ()
    stop_conditions: Sequence[str] = ()
    priority: int = 5
    goal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
