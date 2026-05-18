# GitHub REST Agent Dry-Run Demo

This example shows the GitHub software-engineering flow without performing any
real GitHub write. It uses `InMemoryGitHubClient` by default and calls tool
`dry_run` methods for:

```text
read issue -> get file -> create branch -> update file -> open PR
```

```bash
python examples/github_rest_agent/run_dry_run.py
```

If `GITHUB_TOKEN` is present, the script wraps it in `Secret` and never prints
the token. The default demo still does not access the network.

For real GitHub read-only experiments, instantiate `GitHubRESTClient` and pass
it to the same GitHub tools. Real write operations must run through
`PolicyEngine`, `ApprovalGate`, and `TransactionManager`; do not call write tool
`execute` methods directly in production workflows.

Recommended fine-grained token scopes for real write tests:

- `contents:read`
- `contents:write`
- `pull_requests:write`
- `issues:read`
- `issues:write`

Do not put personal access tokens in command-line arguments, task files, audit
logs, or trace output.
