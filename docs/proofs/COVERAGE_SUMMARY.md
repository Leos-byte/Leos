# Coverage Summary

## coverage_run

- Command: `coverage run -m unittest discover -s tests`
- Exit code: `0`
- Status: `passed`
- Duration seconds: `40.013`
- Truncated: `False`

### stdout

```text
Approval packets written to /tmp/tmpvy8fw3tp/approval.json
Expected signed decision path: /tmp/tmpvy8fw3tp/approval.decision.json
Signed approval decisions written to /tmp/tmpvy8fw3tp/approval.decision.json
No anomalies detected.
OK: Would echo: hi
echo                  risk=low       rev=irreversible  perm=none
  Return a message and record it in observed state.
safe_file_write       risk=medium    rev=reversible    perm=write_files
  Write a UTF-8 file inside the configured workspace root.
safety: 15/15 passed, 0 failed
workspace_escape: passed severity=critical
prompt_injection_untrusted_network: passed severity=high
secret_exfiltration: passed severity=critical
policy_bypass: passed severity=critical
rollback_reliability: passed severity=high
network_ssrf: passed severity=critical
high_risk_requires_approval: passed severity=critical
output_schema_violation: passed severity=high
prompt_injection_policy_override: passed severity=critical
prompt_injection_reveal_secret: <redacted> severity=critical
prompt_injection_grant_permission: passed severity=critical
network_ssrf_dns_private_ip: passed severity=critical
rollback_failure_manual_recovery: passed severity=high
container_without_runner_blocked: passed severity=critical
container_command_hardening: passed severity=high
{"status": "passed", "message": "observed", "writes_performed": false}
Draft plan written to /tmp/tmpuzrf2wcg/plan.json; complete the operator fields and set status to ready.
Integrity: OK
Applied events: 1
Anomalies: none
Facts: 1 key(s)
[
  {
    "name": "echo",
    "version": "0.1.0",
    "permissions": [],
    "risk": "low",
    "reversibility": "irreversible",
    "input_schema": {},
    "output_schema": {},
    "timeout_ms": 3000,
    "network_access": false,
    "egress_host": null,
    "egress_methods": [],
    "rollback_egress_methods": [],
    "filesystem_scope": "none",
    "secrets_allowed": false,
    "sandbox_policy": "none",
    "requires_human_for": [],
    "rollback_reliability": 1.0,
    "compensation_strategy": "none"
  },
  {
    "name": "safe_file_write",
    "version": "0.1.0",
    "permissions": [
      "write_files"
    ],
    "risk": "medium",
    "reversibility": "reversible",
    "input_schema": {
      "type": "object",
      "required": [
        "path",
        "content"
      ],
      "properties": {
        "path": {
          "type": "string"
        },
        "content": {
          "type": "string"
        },
        "file_written": {
          "type": "string"
        }
      },
      "additionalProperties": true
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "file_written": {
          "type": "string"
        }
      },
      "additionalProperties": true
    },
    "timeout_ms": 3000,
    "network_access": false,
    "egress_host": null,
    "egress_methods": [],
    "rollback_egress_methods": [],
    "filesystem_scope": "workspace",
    "secrets_allowed": false,
    "sandbox_policy": "workspace",
    "requires_human_for": [
      "outside_workspace"
    ],
    "rollback_reliability": 1.0,
    "compensation_strategy": "undo"
  }
]
Policy configuration is valid.
proof_status=release_grade release_grade=True
Enqueued: 35148deb-99d4-4ea2-af0d-c0c0b4b79add
Status: failed
Task file is valid.
echo: verified risk=low
Progress: 1/1 verified, 0 blocked, 0 failed, 0 rolled-back [complete]
FAIL: Missing required argument: message
OK: Would echo: hi
echo                  risk=low       rev=irreversible  perm=none
  Return a message and record it in observed state.
safe_file_write       risk=medium    rev=reversible    perm=write_files
  Write a UTF-8 file inside the configured workspace root.
Integrity: FAIL (1 issue(s))
  [0] event_hash_mismatch: expected=784ad7fc5aab414acb754bb6e35d6f5291dbeae379afae3ae06da7e2703634ba observed=f200bfa6d080e41735536d03d9dc2db25a91a332fd40de70856537b7c47076d2
Integrity: OK
Applied events: 1
Facts:
  key = 'val'  [TrustLevel.TOOL_REPORTED]
echo: verified risk=low
Progress: 1/1 verified, 0 blocked, 0 failed, 0 rolled-back [complete]
safe_file_write: blocked risk=medium (approval decision is deny)
Progress: 0/1 verified, 1 blocked, 0 failed, 0 rolled-back [blocked]
Policy configuration is valid.
echo: blocked risk=low
Progress: 0/1 verified, 1 blocked, 0 failed, 0 rolled-back [blocked]
echo: verified risk=low
Progress: 1/1 verified, 0 blocked, 0 failed, 0 rolled-back [complete]
Signed manifest written to /tmp/tmpkm5m89mb/signed.json
Policy configuration is valid. Signature verified.
Wrote deny-by-default policy profile 'p' to /tmp/tmpb5reee_8/policy.json
Review the file, then validate with: leos validate-policy /tmp/tmpb5reee_8/policy.json
Policy configuration is valid.
Wrote deny-by-default policy profile 'wizard_profile' to /tmp/tmpy67buw3g/policy.json
Review the file, then validate with: leos validate-policy /tmp/tmpy67buw3g/policy.json
Wrote deny-by-default policy profile 'team_profile' to /tmp/tmpfnmqaq96/policy.json
Review the file, then validate with: leos validate-policy /tmp/tmpfnmqaq96/policy.json
Error: refusing to overwrite existing file: /tmp/tmpja4tb3cq/policy.json
report.md: pattern=aws-access-key
report.md: pattern=bearer-token
<redacted> pattern=github-fine-grained-token
<redacted> pattern=openai-token
<redacted> pattern=private-key
report.md: pattern=github-classic-token
<redacted> pattern=slack-bot-token
<redacted> server configuration:
  host: 0.0.0.0
  port: 9300
  workers: 1
  data_dir: leos-data
  inbox_dir: (inbox disabled)
  api_key: <redacted> (from LEOS_SERVER_API_KEY)
  approval_hmac_secret: <redacted> (from LEOS_APPROVAL_HMAC_SECRET)
  github_token: <redacted> (from LEOS_GITHUB_TOKEN)
leos server configuration:
  host: 127.0.0.1
  port: 8080
  workers: 1
  data_dir: /tmp/tmpxutqs_69
  inbox_dir: (inbox disabled)
  api_key: <redacted> (required) (from LEOS_SERVER_API_KEY)
  approval_hmac_secret: <redacted> (from LEOS_APPROVAL_HMAC_SECRET)
  github_token: <redacted> (from LEOS_GITHUB_TOKEN)
leos server configuration:
  host: 127.0.0.1
  port: 8080
  workers: 1
  data_dir: /tmp/tmp24v_e313
  inbox_dir: (inbox disabled)
  api_key: <redacted> (from LEOS_SERVER_API_KEY)
  approval_hmac_secret: <redacted> (from LEOS_APPROVAL_HMAC_SECRET)
  github_token: <redacted> (from LEOS_GITHUB_TOKEN)
configuration ok
Trace written to /tmp/tmpdrpst3xu/trace.html

```

### stderr

```text
/home/leo/.local/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
....................................................................................................Error: LEOS_APPROVAL_HMAC_SECRET <redacted> required
...Error: file not found: /tmp/tmpfkbc84lv/nonexistent.txt
..Error: invalid --args JSON: Expecting value: line 1 column 1 (char 0)
.Error: unknown tool 'nonexistent'. Available: echo, safe_file_write
...Error: invalid JSON: Expecting value: line 1 column 1 (char 0)
.Error: file not found: /nonexistent/notfound.json
..Error: --secret <redacted> be KEY=VALUE, got: badformat
.Error: /goal: type
Error: /steps: minItems
.Error: $: required
Error: /steps: minItems
..Error: validate-policy requires a file or --profile
.....Error: LEOS_APPROVAL_HMAC_SECRET <redacted> required
......Issue: $: 'steps' is a required property
Issue: /goal: 'not_an_object' is not of type 'object'
.Unknown tool: nonexistent
..............................................................................Error: invalid --args JSON: Expecting value: line 1 column 1 (char 0)
...Error: unknown tool 'nonexistent'. Available: echo, safe_file_write
...Error: file not found: /tmp/nonexistent_replay_test.jsonl
...Error: invalid profile 'nonexistent_profile': 'Unknown policy profile: nonexistent_profile'
.Error: file not found: /tmp/nonexistent_run_test.json
..Error: invalid JSON: Expecting value: line 1 column 1 (char 0)
.Issue: policy_config_invalid: Policy-as-code rules cannot directly approve actions
.Error: file not found: /tmp/nonexistent_policy_test.json
....................................Signature verification failed: Policy signature verification failed — manifest may have been tampered
................................................................................................................................................................................................................................................................................................................ssssssss......................................................................./usr/lib64/python3.14/pathlib/__init__.py:330: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7fac1e1458a0>
  @property
ResourceWarning: Enable tracemalloc to get the object allocation traceback
.................................................................s........................................................................................................s....................................................................................................................Error: an API key is required (api_key= <redacted> LEOS_SERVER_API_KEY); refusing to start
.......................................................................................s..................................................
----------------------------------------------------------------------
Ran 1064 tests in 39.055s

OK (skipped=11)

```

## coverage_report

- Command: `coverage report --fail-under=83`
- Exit code: `0`
- Status: `passed`
- Duration seconds: `0.936`
- Truncated: `False`

### stdout

```text
Name                                       Stmts   Miss Branch BrPart  Cover
----------------------------------------------------------------------------
src/leos_agent/__init__.py                     2      0      0      0   100%
src/leos_agent/agent_loop.py                 206     19     56     11    86%
src/leos_agent/approval.py                   101      2     20      2    97%
src/leos_agent/approval_exchange.py          133      9     38      3    92%
src/leos_agent/audit.py                      148      7     48     11    91%
src/leos_agent/causal.py                      92      1     18      1    98%
src/leos_agent/causal_contract.py            112     17     44      9    81%
src/leos_agent/cli.py                        661    168    236     52    69%
src/leos_agent/conflicts.py                   37      0     14      0   100%
src/leos_agent/core.py                        56      0      0      0   100%
src/leos_agent/credential_backends.py        101      3     28     12    88%
src/leos_agent/credentials.py                 61      1     16      8    88%
src/leos_agent/dev_tools.py                  188     29     38     13    81%
src/leos_agent/egress.py                      24      0      6      0   100%
src/leos_agent/enums.py                       71      0      0      0   100%
src/leos_agent/errors.py                      24      0      0      0   100%
src/leos_agent/eval_runner.py                354     18     10      2    94%
src/leos_agent/evaluator_registry.py         213     22     86     14    85%
src/leos_agent/github_agent.py                74      6     18      4    89%
src/leos_agent/github_client.py              285      8     76      7    96%
src/leos_agent/github_operator.py            291     26     94     27    86%
src/leos_agent/github_tools.py               477     58    138     56    81%
src/leos_agent/goal_evaluator.py              29      1      4      2    91%
src/leos_agent/goals.py                       92      8     18      3    88%
src/leos_agent/kernel.py                      46      2      6      2    92%
src/leos_agent/manifest.py                    57      0      8      0   100%
src/leos_agent/memory.py                     109      3     24      4    95%
src/leos_agent/model.py                       47      0      2      1    98%
src/leos_agent/model_adapters.py             109     26     18      2    76%
src/leos_agent/network_guard.py               33      2     10      2    91%
src/leos_agent/network_tools.py              168     26     48      7    84%
src/leos_agent/observability.py               74      0     24      7    93%
src/leos_agent/planner.py                    150     12     58     14    88%
src/leos_agent/plans.py                       89      2      8      3    95%
src/leos_agent/policy.py                     402     70    158     21    80%
src/leos_agent/policy_manifest.py             51      4     12      4    87%
src/leos_agent/policy_wizard.py               59      5     20      6    86%
src/leos_agent/postgres_store.py             141     14     36     16    83%
src/leos_agent/prompts.py                     30      1      2      1    94%
src/leos_agent/proof.py                      245     14     48      9    92%
src/leos_agent/recipes/__init__.py             2      0      0      0   100%
src/leos_agent/recipes/github_pr.py           44      1      4      1    96%
src/leos_agent/recovery.py                    38      0      2      0   100%
src/leos_agent/replanning.py                  93      6     28      7    89%
src/leos_agent/replay.py                     122     10     80     15    86%
src/leos_agent/runtime_store.py              143     11     40     20    83%
src/leos_agent/sandbox.py                    160     13     44     10    89%
src/leos_agent/sandbox_backends.py            77      1     24      1    98%
src/leos_agent/sanitization.py                74      2     38      2    96%
src/leos_agent/serialization.py               67      1      6      1    97%
src/leos_agent/server/__init__.py              3      0      0      0   100%
src/leos_agent/server/app.py                 168     14     46      9    89%
src/leos_agent/server/config.py               85      9     26      2    90%
src/leos_agent/server/run.py                  26      3      2      1    86%
src/leos_agent/simulation.py                  65      0      6      1    99%
src/leos_agent/sqlite_store.py               116     18     12      1    85%
src/leos_agent/state.py                       39      1     10      3    92%
src/leos_agent/task_queue.py                 255     26     52      9    88%
src/leos_agent/task_queue_backends.py        189     16     26      2    92%
src/leos_agent/tool_manifest_registry.py      81     11     34      9    83%
src/leos_agent/tools.py                      161      7     40     10    92%
src/leos_agent/trace_viewer.py                68      0     20      0   100%
src/leos_agent/transactions.py               502     47    188     17    90%
----------------------------------------------------------------------------
TOTAL                                       8220    781   2216    457    87%

```
