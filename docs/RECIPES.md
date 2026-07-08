# Recipes — One-Call Templates for Validated Paths

`leos_agent.recipes` packages the validated operator pipelines into a minimal
API. Recipes **assemble** the existing gate path — draft → validate → approval
packets → signed decision → gated apply — and add no gating logic of their
own, so every kernel gate (policy profile, egress enforcement, signed
consume-once approvals, dry-run, read-back verification, environment write
gate) still applies.

## GitHub single-file change + PR

```python
from pathlib import Path
from leos_agent.recipes import (
    GitHubFileChange, prepare_single_file_pr, approve_single_file_pr, apply_single_file_pr,
)

change = GitHubFileChange(
    repo="acme/widgets",
    issue_number=7,
    path="src/config.py",
    content="TIMEOUT = 30\n",
    work_branch="leos/issue-7",
    expected_previous="TIMEOUT = 10\n",   # exactly one optimistic guard
)

# 1. Read-only: draft from the issue, validate, build approval packets.
prepared = prepare_single_file_pr(change, token=token)

# 2. A human reviews prepared.approval and emits a signed decision.
decision = approve_single_file_pr(prepared, approver="alice", signature_secret=secret)

# 3. Full signed-apply path (requires LEOS_ENABLE_REAL_GITHUB_WRITES=1).
result = apply_single_file_pr(
    prepared, decision, token_value=..., signature_secret=secret, work_dir=Path("run1"),
)
```

Guarantees inherited from the operator path:

- `prepare_single_file_pr` performs **no writes** and raises `ValueError`
  listing every constraint violation (non-`leos/` branch, missing/double
  optimistic guard, secret-like content, ...).
- `apply_single_file_pr` refuses without `LEOS_ENABLE_REAL_GITHUB_WRITES=1`,
  without a valid signature, on any digest/profile/plan mismatch, and consumes
  each decision exactly once (receipts under `work_dir`).
- A denied decision blocks the apply with zero writes performed.

## Policy wizard: `leos policy init`

Generate a deny-by-default policy profile interactively or via flags:

```bash
leos policy init --name team_profile \
  --allow-tool echo --grant read_files \
  --egress-host api.github.com \
  --output policy.json
```

The generated profile always passes `validate_policy_config` and starts from
the most restrictive posture: nothing granted unless requested, every
ungrated permission **explicitly denied**, granted consequential permissions
(write/network/execute/delete/...) put behind human approval, `max_auto_risk`
low, network default-deny, signed approval required, and all fail-closed
profile checks (typed goals, causal contracts / timeouts / output schemas for
medium risk, strong sandbox for execute) enabled. Users opt *in* to
capabilities; they never opt out of gates.
