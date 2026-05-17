# Software Engineering Agent Demo

Runs a local, network-free Leos agent loop against a temporary Python project.
The loop patches a failing implementation, runs tests, writes an audit log,
replays the audit log, and renders a markdown trace.

```bash
python examples/software_engineering_agent/run_demo.py
```

No external API, GitHub token, or network access is required.
