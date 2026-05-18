from __future__ import annotations

import os

from leos_agent import Secret
from leos_agent.github_tools import (
    GitHubCreateBranchTool,
    GitHubGetFileTool,
    GitHubOpenPRTool,
    GitHubReadIssueTool,
    GitHubUpdateFileTool,
    InMemoryGitHubClient,
)
from leos_agent.state import WorldState


def main() -> int:
    token = Secret(os.environ["GITHUB_TOKEN"]) if os.environ.get("GITHUB_TOKEN") else None
    token_args = {"token": token} if token is not None else {}
    state = WorldState()
    client = InMemoryGitHubClient()
    client.seed_issue("Leos-byte/Leos", 1, title="Example issue", body="Dry-run only")
    client.seed_file("Leos-byte/Leos", "main", "README.md", "# Leos\n")
    steps = [
        ("read issue", GitHubReadIssueTool(client), {"repo": "Leos-byte/Leos", "issue_number": 1, **token_args}),
        (
            "get file",
            GitHubGetFileTool(client),
            {"repo": "Leos-byte/Leos", "path": "README.md", "ref": "main", **token_args},
        ),
        (
            "create branch",
            GitHubCreateBranchTool(client),
            {"repo": "Leos-byte/Leos", "branch": "agent/example", "base": "main", **token_args},
        ),
        (
            "update file",
            GitHubUpdateFileTool(client),
            {
                "repo": "Leos-byte/Leos",
                "path": "README.md",
                "branch": "agent/example",
                "content": "# Leos\n\nDry-run demo.\n",
                "message": "docs: dry-run demo",
                "expected_previous": "# Leos\n",
                **token_args,
            },
        ),
        (
            "open PR",
            GitHubOpenPRTool(client),
            {
                "repo": "Leos-byte/Leos",
                "title": "Dry-run demo",
                "body": "No write performed.",
                "head": "agent/example",
                "base": "main",
                "idempotency_key": "github-rest-dry-run-demo",
                **token_args,
            },
        ),
    ]
    print("github rest agent dry-run")
    print("client: InMemoryGitHubClient")
    for label, tool, arguments in steps:
        result = tool.dry_run(arguments, state)
        print(f"{label}: {'ok' if result.ok else 'blocked'} - {result.message}")
    print("no write performed")
    print("token not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
