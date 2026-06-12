# Leos

[![CI](https://github.com/Leos-byte/Leos/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Leos-byte/Leos/actions/workflows/ci.yml)

**Leos is a safety-first runtime kernel for bounded, auditable AI agent actions.**

Leos is for AI systems that take real actions. It makes action boundaries
explicit so tool use can be authorized, inspected, verified, and recovered.
It is not a chatbot wrapper, a general open-world autonomous agent, or a
production autonomous employee.

## What Leos is

- A runtime kernel for policy-gated tool execution.
- A transaction manager for dry-run, execution, verification, and rollback.
- A human-control layer with anti-replay human approval packets.
- An audit layer with secret sanitization and append-only audit logs.
- A bounded GitHub software-engineering runtime prototype.

## What Leos is not

- Not a chatbot wrapper or AutoGPT clone.
- Not a general open-world autonomous agent.
- Not a production autonomous employee.
- Not a formal verification system.
- Not a replacement for deployment-level isolation, identity, or network controls.

## Why this exists

Most agent frameworks focus on generating the next tool call. Leos focuses on
the action boundary around that call:

1. Check permissions and risk before execution.
2. Bind consequential actions to human approval packets.
3. Declare expected effects through causal contracts.
4. Validate outputs and perform post-action verification.
5. Apply runtime egress checks to bounded network clients.
6. Record sanitized, replayable audit logs.
7. Evaluate explicit goal criteria separately from action verification.

## Current evidence

- release-grade proof artifacts bind checks to source and test hashes.
- The exact unit-test count and coverage result are recorded in the current
  [release proof manifest](docs/proofs/MANIFEST.json), avoiding stale README
  counters.
- The safety regression suite reports 15/15 passing cases.
- `production_github_only` restricts execution to bounded GitHub workflows and
  `api.github.com` egress.
- private disposable GitHub real-write smoke evidence covers signed approval,
  branch creation, guarded file update, PR creation, read-back verification,
  PR closure, and branch cleanup.

See [Release Checks](docs/RELEASE.md) for the exact-HEAD artifact flow. The
downloaded sanitized evidence is inspected locally at
`docs/proofs/real_github_smoke_latest.json`; it is intentionally not maintained
as a moving tracked snapshot.

## Production-shaped beta

`v0.1.0-beta.1` supports one narrow operator-controlled path: observe a GitHub
issue, prepare a typed single-file plan, review and HMAC-sign step-bound
approval packets, create a branch, perform an optimistic guarded update, open a
pull request, read the result back, and inspect the audit chain.

It does not generate arbitrary patches from issue text, merge pull requests,
enable open-world tools, or permit arbitrary production shell execution. Real
writes remain disabled until the operator explicitly enables the apply command.

See [Production GitHub getting started](docs/GETTING_STARTED_PRODUCTION_GITHUB.md)
and the [v0.1 release checklist](docs/RELEASE_CHECKLIST_V0_1.md).

## Core capabilities

- Policy profiles, tool manifests, typed goals, and bounded resource budgets.
- Transactional execution with optimistic guards, idempotency, and compensation.
- Runtime egress enforcement and attestation for scoped GitHub API access.
- Secret-safe approval, audit, trace, runtime-store, and proof boundaries.
- In-memory, JSONL, and SQLite development persistence.
- Opt-in Docker/Podman sandbox command execution.
- Failure analysis, bounded replanning, and manual recovery packets.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make test
make safety
make bench
```

Run the local software-engineering demo:

```bash
python examples/software_engineering_agent/run_demo.py
```

It creates a temporary project and exercises the local observe-plan-act-verify
loop without network access or external credentials.

## Production GitHub-only profile

`production_github_only` is the narrowest production-shaped boundary in Leos.
It is intended for bounded GitHub software-engineering workflows, not general
agent deployment.

The profile:

- Allows only an explicit GitHub tool allowlist.
- Defaults network access to deny and fixes egress to `api.github.com`.
- Requires typed goal criteria and causal contracts for consequential actions.
- Requires runtime egress attestation to match declared hosts and methods.
- Requires signed approval for GitHub writes and messages.
- Requires optimistic file guards and idempotent PR creation.
- Blocks direct protected-branch cleanup.

Real GitHub writes remain disabled by default and are exposed only through a
manual `workflow_dispatch` smoke path against a private disposable repository.
See [Release Checks](docs/RELEASE.md) for token scope, cleanup, evidence, and
revocation requirements.

## Architecture

```text
Goal
  -> Planner
  -> Policy Engine
  -> Causal Contract
  -> Approval Gate
  -> Transaction Manager
  -> Tool Runtime
  -> Post-action Verifier
  -> Goal Evaluator
  -> Audit / Runtime Store
```

Action verification and goal success are separate. A verified tool call means
the declared action effect was observed; it does not mean the user's goal
criteria were satisfied.

The detailed runtime model is documented in
[Architecture](docs/ARCHITECTURE.md).

## Demos

```bash
# Local bounded software-engineering loop
python examples/software_engineering_agent/run_demo.py

# GitHub REST dry-run with no real write
python examples/github_rest_agent/run_dry_run.py

# Issue-to-PR orchestration through a fake REST transport
python examples/github_rest_agent/run_orchestration.py
```

The real-write example is manual, secret-gated, repository-scoped, and intended
only for the private disposable smoke workflow described in the release guide.

## Proof and readiness checks

```bash
make check
make production-readiness
python scripts/check_release_proof.py
python scripts/scan_artifacts_for_secrets.py --root .
```

For an exact-HEAD private smoke artifact:

```bash
make production-smoke-evidence-check
```

Proof documents are audit evidence, not formal verification. Safety evals are
regression tests, not a complete external red-team assessment.

## Security boundaries

- The runtime egress guard is application-level enforcement, not an OS or
  firewall boundary.
- Docker/Podman sandboxing is opt-in and depends on a local container runtime.
- SQLite provides stronger local persistence, not distributed production
  storage.
- Causal contracts provide partial runtime enforcement, not a complete formal
  structural causal model.
- Proof documents support audit and release review; they are not mathematical
  or formal verification.
- Network content is untrusted external observation and cannot grant policy,
  credentials, permissions, or system-instruction authority.
- Tokens and secrets must not enter audit, trace, memory, runtime stores,
  evidence summaries, or stdout.

See the [Threat Model](docs/THREAT_MODEL.md) for implemented mitigations and
remaining risks.

## Project status

Leos is production-shaped, not broadly production-complete. Its strongest
current use case is a scoped, human-gated GitHub software-engineering runtime
prototype with auditable real-write evidence.

High-risk tools are not enabled by default. General browser automation,
open-ended code execution, distributed state, enterprise identity, and
open-world autonomy are outside the current production boundary.

## Further reading

- [Project positioning](docs/PROJECT_POSITIONING.md)
- [Design philosophy](docs/DESIGN_PHILOSOPHY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Release checks and private smoke evidence](docs/RELEASE.md)
- [Production GitHub getting started](docs/GETTING_STARTED_PRODUCTION_GITHUB.md)
- [v0.1 beta release checklist](docs/RELEASE_CHECKLIST_V0_1.md)
- [Roadmap](docs/ROADMAP.md)
