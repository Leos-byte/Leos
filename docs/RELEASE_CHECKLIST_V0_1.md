# Release Checklist: v0.1.0-beta.1

The release is a production-shaped GitHub-only beta. It is not a general
autonomous agent release.

- [ ] Worktree is clean.
- [ ] `make check`
- [ ] `make safety`
- [ ] `make bench`
- [ ] `make package-check`
- [ ] `python scripts/generate_proofs.py --output docs/proofs --require-clean`
- [ ] `python scripts/check_release_proof.py`
- [ ] Run `GitHub Real Write Smoke` manually against a private disposable
      repository.
- [ ] Confirm the exact-SHA smoke artifact is
      `production-smoke-evidence-<commit-sha>`.
- [ ] Confirm the smoke PR was closed and the `leos/` branch was deleted.
- [ ] `python scripts/check_production_readiness.py --profile production_github_only --require-smoke-evidence --smoke-evidence-path docs/proofs/real_github_smoke_latest.json`
- [ ] `python scripts/scan_artifacts_for_secrets.py --root .`
- [ ] Revoke the disposable-repository fine-grained PAT.
- [ ] Tag `v0.1.0-beta.1`.
- [ ] Create a GitHub Release that states the GitHub-only scope and remaining
      limitations.

Do not publish to PyPI until every applicable item is green and the release
evidence is bound to the tagged commit.
