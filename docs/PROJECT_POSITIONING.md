# Project positioning

## One-line description

Leos is a safety-first runtime kernel for bounded, auditable AI agent actions.

## What Leos is

- A runtime kernel.
- A policy engine.
- An action transaction manager.
- An approval and audit layer.
- A bounded GitHub software-engineering runtime prototype.

## What Leos is not

- Not a chatbot wrapper.
- Not an AutoGPT clone.
- Not a general open-world autonomous agent.
- Not a production autonomous employee.
- Not a formal verification system.

## Target users

- Researchers building reliable AI agents.
- Developers building tool-using agents.
- Teams exploring human-gated AI automation.
- Safety engineers evaluating agent action boundaries.
- GitHub automation and software-engineering agent builders.

## Main differentiator

Most agent frameworks optimize for capability.

Leos optimizes for controlled action.

The runtime makes permissions, risk, approval, expected effects, verification,
rollback, and audit evidence explicit around consequential tool calls.

## Current strongest proof point

The `production_github_only` profile has:

- Release-grade proof artifacts.
- Safety regression evals.
- Private disposable GitHub real-write smoke evidence.
- Signed approval.
- Runtime egress checks.
- Read-back verification.

This evidence covers one deliberately narrow GitHub software-engineering path.
It does not establish general production autonomy.

## Honest maturity statement

Leos is production-shaped, not broadly production-complete.

It is strongest today as a scoped GitHub-only bounded software-engineering
runtime prototype.

As of `v0.1.0-beta.2` the claim boundary is explicit:

- **Implemented and CI-integration-verified** (real backends on every push,
  with commit-bound smoke evidence): `PostgresRuntimeStore` and
  `PostgresTaskQueue` against live PostgreSQL — including a multi-process
  smoke proving exactly-once claims, killed-worker lease reaping, and
  idempotency dedupe; rootless-podman sandbox isolation observed in real
  containers (network egress denial, non-root uid, read-only rootfs,
  pids/memory limits both configured and trigger-enforced, timeout kill);
  the HTTP service (auth, rate limits, inbox, red-teamed) and its container
  image; and the private disposable GitHub real-write smoke.
- **Implemented with unit-level verification only**: gVisor (`runsc`) and
  Firecracker runners (hardened command construction is tested; no such
  runtime exists on CI runners — Firecracker deliberately fails closed),
  keyring/HashiCorp Vault credential backends (contract-tested against
  fakes), structlog/OpenTelemetry sinks (injected fakes), and the GitHub App
  token flow (RS256 signing round-trips for real; the installation-token
  exchange is tested against an injected transport, not a live App).

Neither list establishes general production autonomy; the evidence covers the
deliberately narrow boundaries described above.
