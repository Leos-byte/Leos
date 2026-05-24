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
