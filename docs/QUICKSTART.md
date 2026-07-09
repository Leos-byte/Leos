# Quickstart — 15 Minutes to a Gated Dry Run

Goal: start the service with Docker Compose, generate a deny-by-default
policy profile, and walk a bounded GitHub single-file change through the full
prepare → approve → apply pipeline — without performing any real write
(the write gate stays closed throughout).

Prerequisites: Docker (or Podman) with compose, Python 3.10+, a clone of this
repository.

## 1. Start the service (≈5 min)

```bash
cp leos-server.env.example leos-server.env
python - <<'EOF'
import secrets
print("LEOS_SERVER_API_KEY=" + secrets.token_hex(32))
print("LEOS_APPROVAL_HMAC_SECRET=" + secrets.token_hex(32))
EOF
# paste both values into leos-server.env (replace the change-me lines)

docker compose up --build -d
curl -fsS http://127.0.0.1:8080/healthz    # {"status":"ok"}
curl -fsS http://127.0.0.1:8080/readyz     # profile + writes_enabled:false
```

Sanity check the boundary: a request without the key must be refused.

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/plans  # 401
```

## 2. Generate a deny-by-default policy (≈2 min)

```bash
pip install -e .
leos policy init --name my_profile --allow-tool echo --grant read_files \
  --output my-policy.json --non-interactive
leos validate-policy my-policy.json    # "Policy configuration is valid."
```

Open `my-policy.json`: everything not granted is explicitly denied, risky
permissions require a human, and every fail-closed flag is on. This is the
shape to start from for any custom deployment.

## 3. Walk the gated pipeline end to end, dry (≈8 min)

First, the full observe → plan → act → verify loop against a deterministic
fake GitHub transport (nothing leaves your machine, every gate runs for
real — policy, signed approval, egress attestation, dry-run, read-back):

```bash
python examples/github_rest_agent/run_full_dry_run.py
# ... stop reason: goal_succeeded, pull request: 7, audit/trace paths printed
leos audit inspect --path /tmp/leos-github-full-dry-run-*/audit.jsonl   # Integrity: OK
```

Now the one-call recipes — the same plan/approval/decision documents the
service uses — with the real-write gate (`LEOS_ENABLE_REAL_GITHUB_WRITES`)
left closed:

```python
# quickstart_dry_run.py  (run from the repository root)
import sys
from pathlib import Path

sys.path.insert(0, "examples/github_rest_agent")
from run_full_dry_run import FullDryRunGitHubTransport  # deterministic fake GitHub

from leos_agent import GitHubRESTClient, PolicyEngine
from leos_agent.recipes import GitHubFileChange, prepare_single_file_pr, approve_single_file_pr, apply_single_file_pr

policy = PolicyEngine.from_profile("production_github_only")
client = GitHubRESTClient(
    transport=FullDryRunGitHubTransport(),
    egress_policy=policy.egress_policy,
    enforce_egress=True,
)

change = GitHubFileChange(
    repo="Leos-byte/Leos",
    issue_number=42,
    path="README.md",
    content="# Demo\n\nFixes issue #42.\n",
    work_branch="leos/quickstart-demo",
    expected_previous="# Demo\n",
)

prepared = prepare_single_file_pr(change, client=client)   # drafts + validates, no writes
print("plan ready:", prepared.plan["plan_id"])

decision = approve_single_file_pr(
    prepared, approver="you", signature_secret="quickstart-hmac-secret"
)
print("decision signed:", decision["decisions"][0]["signature"].split(":")[0])

result = apply_single_file_pr(
    prepared, decision,
    token_value="placeholder-token",
    signature_secret="quickstart-hmac-secret",
    work_dir=Path("quickstart-work"),
    client=client,
)
# The write gate is closed, so the apply is REFUSED - that's the point.
print("apply ok:", result.ok, "-", result.message)
```

```bash
python quickstart_dry_run.py
```

You should see the plan validate, three approval packets signed with
`hmac-sha256`, and the apply **refused** with "real GitHub writes are
disabled" — the recipe inherits the gate, it cannot bypass it. Setting
`LEOS_ENABLE_REAL_GITHUB_WRITES=1` (with real credentials and a disposable
repository) is the deliberate, separate step documented in
`docs/GETTING_STARTED_PRODUCTION_GITHUB.md`.

## Where to go next

- Real writes against a disposable repository:
  `docs/GETTING_STARTED_PRODUCTION_GITHUB.md` (credentials — GitHub App
  recommended — plus the explicit write gate and smoke evidence).
- Operating the service: `docs/DEPLOYMENT.md` (TLS, backups) and
  `docs/RUNBOOK.md` (alerts, rotation, audit patrol).
- The approval inbox UI flow: `docs/APPROVAL_UX.md`.
