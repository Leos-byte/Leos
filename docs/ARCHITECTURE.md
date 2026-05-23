# Leos Agent Architecture

## Design target

Leos Agent is designed for reliable autonomy rather than maximal apparent capability. The intended direction is a narrow-domain, auditable agent runtime that can be trusted with bounded tasks before it is expanded toward broader autonomy.

## Seven required subsystems

### 1. Goal system

A goal must declare:

- description
- success criteria
- constraints
- stop conditions
- priority

This forces the agent to reason under bounded rationality: it can decide when a result is good enough and when to stop.

Goal success is evaluated separately from action verification. Transaction verification
answers "did this tool action produce the predicted observed state delta?"
`GoalEvaluator` answers "do the goal success criteria have evidence in world
state?" For example, a verified patch action is not enough to satisfy a "tests
pass" goal unless the world state contains `tests_ok=True`.

### 2. World model

The world state separates:

- verified facts
- assumptions
- uncertainty estimates

This prevents the agent from treating guesses as ground truth.

### 3. Causal model

Before action, each step receives action-consequence predictions such as:

```text
safe_file_write -> file_written should become /workspace/file.txt
no safe_file_write -> file_written should remain unchanged
```

Counterfactual review compares the action path with the no-action path before execution. After execution, observed state deltas are compared against predicted consequences. Missing or mismatched observations fail verification.

Current implementation supports both legacy `CausalHypothesis` predictions and
tool-level causal contracts. Contract enforcement is partial runtime support:
required observations are checked and missing observations trigger rollback, but
this is not a complete structural causal model.

### 4. Planning and search

The current implementation includes a deterministic planner that accepts explicit `PlanProposal` candidates, scores each candidate by risk, cost, and benefit, and selects the first satisfactory plan. The intended next layer is an LLM planner adapter that must output the same typed proposal schema rather than free-form text. The runtime should remain independent from any one model vendor.

### 5. Tool/action system

Each tool declares:

- name
- description
- permissions
- default risk
- reversibility
- sandbox policy
- filesystem/network scope
- optional causal contract

Each tool must support:

- `dry_run`
- `execute`
- `rollback`

Developer tools are available through `default_dev_registry()`. High-risk tools such as test execution and network fetch are opt-in and remain subject to policy and approval.

`ToolManifestRegistry` validates manifest-declared tool metadata against
runtime `ToolSpec` values. A manifest is an auditable capability declaration and
schema entry point; it does not approve a tool, instantiate a tool, or bypass
`PolicyEngine`.

GitHub software-engineering tools accept any implementation of the
`GitHubClient` protocol. `InMemoryGitHubClient` is used for local demos and
tests. `GitHubRESTClient` can call the real GitHub REST API through an injected
transport, but real writes remain consequential tool actions: file updates need
`expected_sha` or `expected_previous`, PR creation uses an idempotency marker,
and protected branches are not deleted by rollback or cleanup.

GitHub tools explicitly declare `network_access`, `egress_host`,
`egress_methods`, and `rollback_egress_methods`. In `production_locked_down`,
the policy checks every declared forward method and every rollback or
compensation method; allowing `GET` is not enough to authorize a tool that can
also `PUT`, `POST`, `PATCH`, or `DELETE`. Reversible and compensatable network
tools are blocked if rollback egress is missing or not allowed. These checks are
policy-level semantics for auditing and fail-closed planning.

`GitHubRESTClient` can additionally enforce egress at runtime with
`enforce_egress=True`. Each request URL and method is checked by
`RuntimeEgressGuard` before the transport is called; no policy means deny-all,
wildcards are rejected, and private or localhost hosts are blocked. This guard
protects direct client use inside Leos, but production deployments still need
OS, container, or network firewall egress enforcement.

In `production_locked_down`, network tools must also provide runtime
attestation. GitHub tools report whether their underlying client has runtime
egress enforcement enabled and has a configured egress policy. Real
`GitHubRESTClient` instances must attest `enforce_egress=True`; the in-memory
client attests an explicit `in_memory` mode for tests and demos.

`GitHubIssuePlanProvider` is a deterministic bridge between GitHub observations
and the closed loop runtime. It does not call GitHub directly. Its first
proposal reads the issue and target file through normal tools. Once those facts
exist in `WorldState`, it proposes the branch/update/PR transaction, keeping the
same `AgentLoop -> Planner -> TransactionManager -> PolicyEngine` path as other
runtime actions.

Goal evaluation is also registry-backed. `EvaluatorRegistry` groups
domain-specific deterministic criteria rules, so new domains can add success
evaluators without widening `GoalEvaluator`. Unmatched criteria remain
unsatisfied and cannot be silently counted as success.

Runtime progress can optionally be persisted with `RuntimeStore`.
`InMemoryRuntimeStore` is for tests and demos; `JsonlRuntimeStore` is a
development store for goals, plans, runtime events, and checkpoints. It is not a
production database or strong-concurrency storage layer.
`SQLiteRuntimeStore` provides stronger local persistence and restart recovery
for the same RuntimeStore interface, but it is still not a distributed
production database.
Runtime store writes pass through the shared sanitization boundary. Events and
checkpoints reject `Secret` values, redaction markers embedded in strings, and
common token-like literals before persistence.

Rollback credentials can be represented as `SecretHandle` values from a
`CredentialVault`. The in-memory vault is a local development abstraction; a
production deployment should use KMS, an OS keychain, or a cloud secret manager.
`SecretHandle` values are serializable references only and do not expose the
underlying secret. Audit logs and trace rendering use the same sanitizer:
secret-like audit payloads are replaced with `audit.secret_blocked`, and trace
HTML/Markdown redacts token-like values before rendering.

Rollback failures and blocked compensations produce `ManualRecoveryPacket`
records. A recovery packet includes the affected step, tool, risk level, reason,
safe resource summary, and suggested operator actions. It is designed for
auditable manual follow-up and never contains rollback credentials or raw
tokens. Network rollback is guarded again at runtime in `production_locked_down`:
if rollback egress is no longer allowed, the rollback call is skipped and manual
recovery is required.

### 6. Memory and learning

Memory records contain:

- key
- value
- confidence
- provenance
- timestamp

This creates a basis for learning while preserving traceability.

### 7. Audit and human collaboration

Every consequential step emits an audit event. Risky or under-authorized actions require approval. This supports Engelbart-style augmentation: the agent helps humans reason about work rather than silently replacing human judgment.

## Transaction protocol

```text
observe
recall memory
propose plans
select plan
transact:
  resolve tool
  assign permissions and risk
  predict causal effects
  review action consequences against no-action counterfactuals
  ask policy engine
  request human approval if needed
  dry run
  execute
  update observed world state
  verify causal predictions
  continue or rollback
evaluate goal success criteria
remember progress
replan or stop
```

## Safety invariants

1. Unknown tools cannot execute.
2. Missing permissions block execution unless approved by a human gate.
3. High and critical risk actions require approval by default.
4. File writes are constrained to a workspace root.
5. Dry-run failure prevents execution.
6. Verification failure triggers rollback for reversible prior actions.
7. Audit events are append-only.

## Production hardening checklist

- Replace in-memory policy with signed policy manifests.
- Add per-user and per-tool capability grants.
- Add secure secret handling and never expose secrets to untrusted tools.
- Run high-risk tools in containers or microVMs.
- Generate proof documents before release review.
- Run `leos eval --suite safety` for safety regressions.
- Use structured LLM outputs with JSON schema validation.
- Add anomaly detection over audit logs.
- Add replay tests for known failures.
- Add external red-team suites for prompt injection and tool injection.
- Use `production_locked_down` for fail-closed local production simulations:
  typed goal criteria are required, medium+ tools need causal contracts,
  timeout, and output schema, and workspace subprocess execution is blocked as
  a production isolation boundary.
- Use approval packets for consequential human-gated actions. Approval is bound
  to the exact step hash, profile, expiry, goal, plan, tool, permissions, risk,
  and causal contract summary.
- Use file-based approval exchange for non-interactive local/manual workflows
  when a human needs to review a packet out of band. Decision files still bind
  to the packet approval id and step hash and cannot bypass production hard
  blocks. `FileApprovalGate` can restrict approvers by allowlist and can reject
  decision files with group/world permissions; this is local hardening, not a
  cryptographic signature or multi-tenant approval system.
- For release evidence, regenerate clean proofs and verify the manifest:

  ```bash
  python scripts/generate_proofs.py --output docs/proofs --require-clean
  python scripts/check_release_proof.py
  ```

## Current readiness boundaries

- Implemented: local dev tools, network trust boundaries, safety evals, proof generation, task queue persistence.
- Implemented with fake-transport tests: GitHub REST client for issue/file/branch/PR/comment/CI workflows and issue-to-PR AgentLoop orchestration.
- Implemented: production locked-down policy checks, typed goal criteria,
  bounded failure-driven replanning, and anti-replay approval packets.
- Implemented: policy-level egress method checks for GitHub forward and
  rollback paths, plus network budget accounting for `network_access` tools.
- Implemented: opt-in GitHub runtime egress guard and rollback egress audit.
- Implemented: manual recovery packets for rollback failure or blocked
  compensation.
- Implemented: local file-based approval packet exchange for non-interactive
  runs.
- Implemented: release proof consistency script that checks release-grade proof
  metadata against the current commit.
- Partial: causal contract runtime enforcement. The causal model is
  tool-contract verification, not a full structural causal model.
- Opt-in: Docker/Podman sandboxing requires a local container runtime and is
  not enabled by default.
- Opt-in: DNS-aware SSRF checks depend on resolver results; production still
  needs egress firewalls.
- Not complete: production autonomy, full SQLite persistence for all state, formal safety proof, deployment egress controls.
