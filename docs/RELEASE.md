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

Only sanitized summary evidence may be committed to
`docs/proofs/real_github_smoke_latest.json`. Do not store raw logs, raw audit,
tokens, Authorization headers, HMAC secrets, or raw approval signatures. Check
the evidence with:

```bash
make production-smoke-evidence-check
python scripts/scan_artifacts_for_secrets.py --root docs/proofs
```

This smoke evidence proves one private disposable GitHub-only real-write path
through `production_github_only`. It does not prove general open-world
autonomy, and it does not replace OS/firewall-level egress enforcement.
