# Software Engineering Agent Demo

Runs a local, network-free Leos agent loop against a temporary Python project.
The loop patches a failing implementation, runs tests, writes an audit log,
replays the audit log, and renders a markdown trace.

```bash
python examples/software_engineering_agent/run_demo.py
```

No external API, GitHub token, or network access is required.

The demo proves goal evaluation separately from action verification. The patch
step can be verified as a file action, but the goal only succeeds when the test
runner records `tests_ok=True` in world state. If tests fail or no test result is
observed, the `tests pass` success criterion must not be marked succeeded.
