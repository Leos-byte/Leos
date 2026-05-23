# Software Engineering Agent Demo

> **Leos is not a production autonomous employee and not a general open-world agent.**

This demo runs a local, network-free Leos agent loop against a temporary Python
project. The loop patches a failing implementation, runs tests, writes an audit
log, replays the audit log, and renders a markdown trace.

```bash
python examples/software_engineering_agent/run_demo.py
```

No external API, GitHub token, or network access is required. The demo runs
entirely locally.

## What the demo proves

- **Goal evaluation is separate from action verification.** The patch step can
  be verified as a file action, but the goal only succeeds when the test runner
  records `tests_ok=True` in world state. If tests fail or no test result is
  observed, the `tests pass` success criterion must not be marked succeeded.
- **Audit logging, replay, and trace rendering** work end-to-end in a
  deterministic local environment.
- **Policy enforcement** via `policy.developer.json` grants file read/write and
  code execution but disables network access.
