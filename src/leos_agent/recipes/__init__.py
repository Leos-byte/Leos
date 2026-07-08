"""One-call recipes for validated agent paths.

Recipes assemble the existing gate path — they never add or bypass gating.
"""

from .github_pr import (
    GitHubFileChange,
    PreparedChange,
    apply_single_file_pr,
    approve_single_file_pr,
    prepare_single_file_pr,
)

__all__ = [
    "GitHubFileChange",
    "PreparedChange",
    "apply_single_file_pr",
    "approve_single_file_pr",
    "prepare_single_file_pr",
]
