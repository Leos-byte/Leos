# Release Checks

Leos release evidence is an audit aid, not a formal proof. For release review,
generate proof documents from a clean worktree and then check that the manifest
claims release-grade evidence for the current commit:

```bash
python scripts/generate_proofs.py --output docs/proofs --require-clean
python scripts/check_release_proof.py
```

`check_release_proof.py` verifies that `docs/proofs/MANIFEST.json` has
`proof_status=release_grade`, `release_grade=true`, `dirty_worktree=false`, and
a commit SHA matching either the current `HEAD` or the parent of a commit that
only refreshes `docs/proofs/`. The latter case supports the normal two-step
release evidence flow: commit code, generate clean proof for that code commit,
then commit the generated proof documents.

Dirty proofs generated with `--allow-dirty` are useful for local review only and
must not be treated as release-grade evidence.

CI runs `check_release_proof.py` only on `main`. Pull requests still generate
local dirty proof documents for review, but ordinary code changes should not be
blocked by proof-document churn before maintainers perform the release proof
refresh.
