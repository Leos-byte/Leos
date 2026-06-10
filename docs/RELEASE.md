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
