# Release Checks

Leos release evidence is an audit aid, not a formal proof. For release review,
generate proof documents from a clean worktree and then check that the manifest
claims release-grade evidence for the current commit:

```bash
python scripts/generate_proofs.py --output docs/proofs --require-clean
python scripts/check_release_proof.py
python scripts/check_production_readiness.py --profile production_github_only
git add docs/proofs
git commit -m "chore(proofs): refresh release-grade evidence"
```

`check_release_proof.py` verifies that `docs/proofs/MANIFEST.json` has
`proof_status=release_grade`, `release_grade=true`, `dirty_worktree=false`, and
a commit SHA matching either the current `HEAD` or the parent of a commit that
only refreshes `docs/proofs/`. The latter case supports the normal two-step
release evidence flow: commit code, generate clean proof for that code commit,
then commit the generated proof documents.

Dirty proofs generated with `--allow-dirty` are useful for local review only and
must not be treated as release-grade evidence.

CI runs `check_release_proof.py` only on `main`, before generating local proof
documents for the workflow run. Pull request CI still generates local proof
artifacts, but it does not require contributors to refresh release proof metadata
on every ordinary code change.

`check_production_readiness.py --profile production_github_only` is a scoped
repository gate for the GitHub-only runtime boundary. It checks that the
release proof is current, the profile is fail-closed to `api.github.com`, signed
approval is required, allowed GitHub tools declare egress/rollback metadata, CI
contains the main-only proof check, and real-write smoke remains manual.

On `main`, production readiness is fail-closed until a successful manual smoke
run exists for the exact current commit. The smoke workflow uploads
`production-smoke-evidence-<commit-sha>` as a short-lived GitHub Actions
artifact. Main CI downloads that artifact and verifies both its workflow run ID
and `leos_commit_sha` before running the readiness checker. The evidence is not
kept as a moving tracked file because a tracked evidence file cannot truthfully
bind itself to the commit that adds it.

## Backend Smoke Evidence (Sandbox Isolation, Queue Concurrency)

Two further smoke suites produce commit-bound evidence with the same model
(gitignored JSON, uploaded as an exact-SHA CI artifact, never a tracked file):

- `scripts/sandbox_smoke.py` executes real hardened rootless-podman containers
  and asserts every isolation property end to end: network egress denial,
  non-root uid, read-only rootfs with writable tmpfs `/tmp`, pids and memory
  limits (cgroup-configured **and** trigger-enforced), timeout kill, and the
  fail-closed microVM path. Output: `docs/proofs/sandbox_smoke_latest.json`
  (override with `LEOS_SANDBOX_SMOKE_EVIDENCE_OUT`).
- `scripts/queue_smoke.py` spawns real worker processes against a live
  PostgreSQL server (`LEOS_TEST_POSTGRES_DSN`) and asserts exactly-once
  consumption: no double claims, every task completed once, a killed worker's
  expired lease reaped and its task rescued by another worker, and idempotency
  dedupe. Output: `docs/proofs/queue_smoke_latest.json` (override with
  `LEOS_QUEUE_SMOKE_EVIDENCE_OUT`). The DSN never enters the evidence.

Both run in the CI `integration` job on every push and upload
`production-sandbox-evidence-<commit-sha>` / `production-queue-evidence-<commit-sha>`
artifacts after in-job validation. `check_production_readiness.py` accepts
opt-in gates `--require-sandbox-evidence` / `--require-queue-evidence`
(with `--sandbox-evidence-path` / `--queue-evidence-path`) that verify commit
binding, run id, freshness format, all checks true, and the absence of secret
or credentialed-URL markers. `scripts/download_smoke_evidence.py` now takes
`--artifact-prefix`, `--filename`, and `--event` so the same exact-HEAD
download flow works for all three evidence kinds.

## Private Disposable GitHub Smoke Evidence

The optional real-write evidence check is intentionally scoped to one bounded
GitHub software-engineering path. It must use a private disposable repository,
not the Leos source repository, not a public repository, and not any business
repository. The recommended target is:

```text
Leos-byte/leos-smoke-private-test
```

Use a fine-grained PAT with access only to that repository. Required repository
permissions are `Contents: read/write`, `Pull requests: read/write`,
`Issues: read/write`, and `Metadata: read-only`. Do not grant all-repositories
access, administration, workflow, actions, secrets, deployment, environment, or
organization permissions. Revoke the PAT immediately after evidence capture.

Run the workflow manually:

```bash
gh workflow run "GitHub Real Write Smoke" \
  --repo Leos-byte/Leos \
  -f test_repo="Leos-byte/leos-smoke-private-test" \
  -f base_branch="main"
```

The workflow file is `.github/workflows/github-real-write-smoke.yml`. Configure
the protected `smoke-private` environment with
`LEOS_SMOKE_GITHUB_TOKEN` and `LEOS_SMOKE_APPROVAL_HMAC_SECRET`. The GitHub
token must be a fine-grained PAT scoped only to the disposable private target
repository.

The smoke must run with cleanup enabled. A successful run closes the smoke pull
request, deletes its `leos/` branch, verifies the disposable repository base
branch and checked-out Leos commit were unchanged, and never merges the pull
request. Cleanup failure makes the workflow fail and produces only a sanitized
failure summary.

Only sanitized summary evidence may be written to
`docs/proofs/real_github_smoke_latest.json` for local inspection. Do not commit
that moving file, raw logs, raw audit, tokens, Authorization headers, HMAC
secrets, or raw approval signatures. Main CI downloads the exact-commit artifact
to that path transiently. Check a downloaded evidence file with:

```bash
make production-smoke-evidence-check
python scripts/scan_artifacts_for_secrets.py --root docs/proofs
```

The expected main-branch sequence is:

1. Merge the code change.
2. Dispatch `GitHub Real Write Smoke` against the private disposable repository
   for the current `main`.
3. Confirm the workflow closed the PR and deleted the smoke branch.
4. Rerun the main CI workflow for that same commit. CI downloads the exact-SHA
   smoke artifact and applies the production readiness gate.
5. Revoke the fine-grained PAT after evidence capture.

This smoke evidence proves one private disposable GitHub-only real-write path
through `production_github_only`. It does not prove general open-world
autonomy, and it does not replace OS/firewall-level egress enforcement.
