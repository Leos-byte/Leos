# Getting Started: Production GitHub Beta

Leos `v0.1.0-beta.1` is scoped to bounded, human-gated GitHub software-engineering
actions. It is not a general open-world autonomous agent and it does not merge
pull requests automatically.

The supported operator workflow is:

```text
issue/task -> typed plan draft -> dry-run -> signed approval -> branch/file/PR
           -> tool-mediated read-back -> goal evaluation -> audit
```

Real writes are disabled by default.

## Install From The Beta Tag

```bash
python -m venv .venv
source .venv/bin/activate
pip install "leos-agent @ git+https://github.com/Leos-byte/Leos.git@v0.1.0-beta.1"
leos doctor --profile production_github_only
```

## Credentials

For private beta and disposable-repository smoke runs, use a fine-grained PAT
limited to the single target repository:

- Metadata: read-only
- Contents: read/write
- Pull requests: read/write
- Issues: read-only, or read/write only when PR comments are used

Do not grant administration, Actions write, workflows, secrets, deployments,
organization access, or all-repositories access. PAT support is acceptable for
this private beta and smoke boundary; a repository-installed GitHub App with
short-lived installation tokens is the recommended next authentication model.

Set credentials without putting values in plan, approval, audit, or shell
arguments:

```bash
read -rsp "GitHub token: " LEOS_GITHUB_TOKEN
export LEOS_GITHUB_TOKEN
printf '\n'
read -rsp "Approval HMAC secret: " LEOS_APPROVAL_HMAC_SECRET
export LEOS_APPROVAL_HMAC_SECRET
printf '\n'
```

## Observe And Draft

Read an issue without writes:

```bash
leos github dry-run --repo OWNER/REPO --issue 123 --audit issue-read.audit.jsonl
```

Create an operator-editable plan:

```bash
leos github plan --repo OWNER/REPO --issue 123 --out plan.json
```

Edit `plan.json` and:

- set `status` to `ready`;
- set a non-empty `leos/` work branch;
- set one file path and its complete intended content;
- set exactly one optimistic guard: `expected_sha` or `expected_previous`.

Leos does not infer a patch from untrusted issue text in this beta.

## Approve

Create step-bound approval packets:

```bash
leos approval create --plan plan.json --out approval.json
```

Review the packet fields, including repository, branch, file paths, optimistic
guard, egress methods, expiry, step hash, and rollback description. Then create
a signed decision sidecar:

```bash
leos approval decide \
  --packet approval.json \
  --out approval.decision.json \
  --approver YOUR_NAME \
  --decision approve \
  --reason "Reviewed bounded change and rollback scope"
```

Approval is invalid if it is expired, replayed, for another profile, or if the
plan changes its step hash.

## Apply

Explicitly enable the real-write boundary for this process:

```bash
LEOS_ENABLE_REAL_GITHUB_WRITES=1 \
leos github apply \
  --plan plan.json \
  --approval approval.json \
  --decision approval.decision.json \
  --audit plan.audit.jsonl \
  --receipts .leos-approval-receipts
```

The apply path creates a branch, performs one guarded file update, opens a pull
request, and reads the file back through a GitHub tool. It does not merge the
pull request.

Inspect the audit chain:

```bash
leos audit inspect --path plan.audit.jsonl
```

If automatic rollback fails, the CLI writes `plan.recovery.json` from the
sanitized `ManualRecoveryPacket`. Inspect both files before manual action:

```bash
leos audit inspect --path plan.audit.jsonl
python -m json.tool plan.recovery.json
```

## Troubleshooting

- `real GitHub writes are disabled`: set `LEOS_ENABLE_REAL_GITHUB_WRITES=1`
  only for the reviewed apply command.
- `plan status must be ready`: finish the operator fields and explicitly mark
  the plan ready.
- optimistic guard failure: re-read the target file and create a new plan and
  approval; do not reuse the old decision.
- approval expired, replayed, or hash mismatch: create fresh approval packets
  and a new signed decision.
- egress blocked: `production_github_only` permits only `api.github.com` and
  declared methods. Do not widen it to work around a failure.
- rollback failed: inspect `plan.recovery.json`; do not retry destructive
  cleanup without verifying current repository state.

Runtime egress checks are application-level controls, not an OS or firewall
boundary. Use deployment egress controls in addition to Leos.
