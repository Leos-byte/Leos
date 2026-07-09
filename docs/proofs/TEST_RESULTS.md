# Test Results

## unit_tests

- Command: `python -m unittest discover -s tests`
- Exit code: `0`
- Status: `passed`
- Duration seconds: `46.42`
- Truncated: `False`

### stdout

```text
Approval packets written to /tmp/tmp4di2r32a/approval.json
Expected signed decision path: /tmp/tmp4di2r32a/approval.decision.json
Signed approval decisions written to /tmp/tmp4di2r32a/approval.decision.json
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
Draft plan written to /tmp/tmp2585dld2/plan.json; complete the operator fields and set status to ready.
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
Enqueued: 210c52e1-9d0e-473f-9ad9-fc11d4015b1e
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
  [0] event_hash_mismatch: expected=fb413be5dc63283e6cca5da73ebab0ed85f03d15d5f024f138fafcdbae1a8662 observed=c85dc8f26234b714c8fc33ce4def6507d4f0fcc070829524f65a4f9615c2f2c7
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
Signed manifest written to /tmp/tmp9go5chpu/signed.json
Policy configuration is valid. Signature verified.
Wrote deny-by-default policy profile 'p' to /tmp/tmp6flmgjzb/policy.json
Review the file, then validate with: leos validate-policy /tmp/tmp6flmgjzb/policy.json
Policy configuration is valid.
Wrote deny-by-default policy profile 'wizard_profile' to /tmp/tmpdtositff/policy.json
Review the file, then validate with: leos validate-policy /tmp/tmpdtositff/policy.json
Wrote deny-by-default policy profile 'team_profile' to /tmp/tmp_n_zgcn9/policy.json
Review the file, then validate with: leos validate-policy /tmp/tmp_n_zgcn9/policy.json
Error: refusing to overwrite existing file: /tmp/tmpoqbknn3f/policy.json
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
  data_dir: /tmp/tmp7xaettmr
  inbox_dir: (inbox disabled)
  api_key: <redacted> (required) (from LEOS_SERVER_API_KEY)
  approval_hmac_secret: <redacted> (from LEOS_APPROVAL_HMAC_SECRET)
  github_token: <redacted> (from LEOS_GITHUB_TOKEN)
leos server configuration:
  host: 127.0.0.1
  port: 8080
  workers: 1
  data_dir: /tmp/tmpsspzdtd8
  inbox_dir: (inbox disabled)
  api_key: <redacted> (from LEOS_SERVER_API_KEY)
  approval_hmac_secret: <redacted> (from LEOS_APPROVAL_HMAC_SECRET)
  github_token: <redacted> (from LEOS_GITHUB_TOKEN)
configuration ok
Trace written to /tmp/tmpwavswriw/trace.html

```

### stderr

```text
/home/leo/.local/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
  from starlette.testclient import TestClient as TestClient  # noqa
.............................................................................................................Error: LEOS_APPROVAL_HMAC_SECRET <redacted> required
...Error: file not found: /tmp/tmp0cmj39us/nonexistent.txt
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
.................................................................................................................................................................................................................................................................................................................................ssssssss.............................................................................../usr/lib64/python3.14/dataclasses.py:1468: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7f7ea5a753f0>
  return tuple(f for f in fields.values() if f._field_type is _FIELD)
ResourceWarning: Enable tracemalloc to get the object allocation traceback
.........................................................s........................................................................................................s....................................................................................................................Error: an API key is required (api_key= <redacted> LEOS_SERVER_API_KEY); refusing to start
................................................................................................s..................................................
----------------------------------------------------------------------
Ran 1099 tests in 45.796s

OK (skipped=11)

```
