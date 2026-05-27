# Coverage Summary

## coverage_run

- Command: `coverage run -m unittest discover -s tests`
- Exit code: `0`
- Status: `passed`
- Duration seconds: `3.843`
- Truncated: `False`

### stdout

```text
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
Enqueued: 7c346b8a-d630-4f8a-8062-adb1b2d4e743
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
  [0] event_hash_mismatch: expected=41f34d52ee3570aa2f9cb251021220b2f377a93230f611dbff612a9153751e83 observed=7a947d496fb6ac2a142732ce62c49a3b5cb06edc41ea1aa6ecdb7670625e8c8a
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
Signed manifest written to /tmp/tmpr5zdud64/signed.json
Policy configuration is valid. Signature verified.
report.md: pattern=aws-access-key
report.md: pattern=bearer-token
<redacted> pattern=github-fine-grained-token
<redacted> pattern=openai-token
<redacted> pattern=private-key
report.md: pattern=github-classic-token
<redacted> pattern=slack-bot-token
<redacted> written to /tmp/tmpt1v8vqdt/trace.html

```

### stderr

```text
....................................................................................................Error: file not found: /tmp/tmpw8snn2a3/nonexistent.txt
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
.......Issue: $: 'steps' is a required property
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
........................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................../usr/lib64/python3.14/tempfile.py:484: ResourceWarning: Implicitly cleaning up <HTTPError 404: 'missing'>
  _warnings.warn(self.warn_message, ResourceWarning)
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e60220>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e60400>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e606d0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e607c0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e608b0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e60a90>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e609a0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e60b80>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e60f40>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61120>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61300>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61c60>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61210>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e604f0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61030>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e613f0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e614e0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e615d0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e616c0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e617b0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e618a0>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61990>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61a80>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61b70>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e60e50>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e60c70>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/usr/lib64/python3.14/ast.py:46: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x7ff599e61d50>
  return compile(source, filename, mode, flags,
ResourceWarning: Enable tracemalloc to get the object allocation traceback
.............
----------------------------------------------------------------------
Ran 768 tests in 3.253s

OK

```

## coverage_report

- Command: `coverage report --fail-under=83`
- Exit code: `0`
- Status: `passed`
- Duration seconds: `0.791`
- Truncated: `False`

### stdout

```text
Name                                       Stmts   Miss Branch BrPart  Cover
----------------------------------------------------------------------------
src/leos_agent/__init__.py                     2      0      0      0   100%
src/leos_agent/agent_loop.py                 206     19     56     11    86%
src/leos_agent/approval.py                    87      2     16      2    96%
src/leos_agent/approval_exchange.py          120      5     34      3    95%
src/leos_agent/audit.py                      143      7     46     11    90%
src/leos_agent/causal.py                      92      1     18      1    98%
src/leos_agent/causal_contract.py            108     17     44      9    80%
src/leos_agent/cli.py                        476    194    188     32    57%
src/leos_agent/conflicts.py                   37      0     14      0   100%
src/leos_agent/core.py                        49      0      0      0   100%
src/leos_agent/credentials.py                 61      1     16      8    88%
src/leos_agent/dev_tools.py                  188     29     38     13    81%
src/leos_agent/egress.py                      25      0      6      0   100%
src/leos_agent/enums.py                       71      0      0      0   100%
src/leos_agent/errors.py                      24      0      0      0   100%
src/leos_agent/eval_runner.py                354     18     10      2    94%
src/leos_agent/evaluator_registry.py         213     22     86     14    85%
src/leos_agent/github_agent.py                74      6     18      4    89%
src/leos_agent/github_client.py              260     11     76      7    94%
src/leos_agent/github_tools.py               357     42    100     39    82%
src/leos_agent/goal_evaluator.py              29      1      4      2    91%
src/leos_agent/goals.py                       92      8     18      3    88%
src/leos_agent/kernel.py                      46      2      6      2    92%
src/leos_agent/manifest.py                    57      0      8      0   100%
src/leos_agent/memory.py                     109      3     24      4    95%
src/leos_agent/model.py                       47      0      2      1    98%
src/leos_agent/model_adapters.py             109     26     18      2    76%
src/leos_agent/network_guard.py               33      2     10      2    91%
src/leos_agent/network_tools.py              168     26     48      7    84%
src/leos_agent/planner.py                    150     12     58     14    88%
src/leos_agent/plans.py                       89      2      8      3    95%
src/leos_agent/policy.py                     396     71    158     22    80%
src/leos_agent/policy_manifest.py             51      4     12      4    87%
src/leos_agent/prompts.py                     30      1      2      1    94%
src/leos_agent/proof.py                      216      9     42      8    93%
src/leos_agent/recovery.py                    38      0      2      0   100%
src/leos_agent/replanning.py                  93      6     28      7    89%
src/leos_agent/replay.py                     122     10     80     15    86%
src/leos_agent/runtime_store.py              143     13     40     22    81%
src/leos_agent/sandbox.py                    155     15     44     10    87%
src/leos_agent/sanitization.py                74      2     38      2    96%
src/leos_agent/serialization.py               67      1      6      1    97%
src/leos_agent/simulation.py                  65      0      6      1    99%
src/leos_agent/sqlite_store.py               103     16     10      2    84%
src/leos_agent/state.py                       39      1     10      3    92%
src/leos_agent/task_queue.py                 242     24     50     10    88%
src/leos_agent/tool_manifest_registry.py      81     11     34      9    83%
src/leos_agent/tools.py                      149      7     28     10    90%
src/leos_agent/trace_viewer.py                68      0     20      0   100%
src/leos_agent/transactions.py               481     40    184     17    91%
----------------------------------------------------------------------------
TOTAL                                       6489    687   1764    340    86%

```
