from __future__ import annotations

import base64
import json
import unittest
import urllib.error
from collections.abc import Mapping
from io import BytesIO
from typing import Any
from unittest import mock

from leos_agent.github_client import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubConflictError,
    GitHubHTTPResponse,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubRESTClient,
    UrllibGitHubTransport,
)


class FakeGitHubTransport:
    def __init__(self, responses: list[GitHubHTTPResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> GitHubHTTPResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        if not self.responses:
            raise AssertionError("No fake GitHub response queued")
        return self.responses.pop(0)


def _json_response(status: int, payload: Any, headers: dict[str, str] | None = None) -> GitHubHTTPResponse:
    return GitHubHTTPResponse(status, json.dumps(payload).encode("utf-8"), headers or {})


class GitHubRESTClientTests(unittest.TestCase):
    def test_read_issue_success_uses_authorization_and_redacts_return(self) -> None:
        transport = FakeGitHubTransport(
            [
                _json_response(
                    200,
                    {
                        "number": 1,
                        "title": "Bug",
                        "body": "Fix it",
                        "state": "open",
                        "html_url": "https://github.com/o/r/issues/1",
                    },
                )
            ]
        )
        client = GitHubRESTClient(transport=transport)

        issue = client.read_issue("o/r", 1, token="ghp_secret")

        self.assertEqual(issue["title"], "Bug")
        self.assertEqual(issue["body"], "Fix it")
        self.assertEqual(issue["state"], "open")
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer ghp_secret")
        self.assertNotIn("ghp_secret", repr(issue))

    def test_get_file_decodes_base64_content(self) -> None:
        encoded = base64.b64encode(b"hello\n").decode("ascii")
        transport = FakeGitHubTransport([_json_response(200, {"content": encoded, "encoding": "base64", "sha": "s1"})])
        client = GitHubRESTClient(transport=transport)

        data = client.get_file("o/r", "app.py", "main")

        self.assertEqual(data["content"], "hello\n")
        self.assertEqual(data["sha"], "s1")

    def test_get_file_rejects_non_base64_encoding(self) -> None:
        transport = FakeGitHubTransport([_json_response(200, {"content": "plain", "encoding": "utf-8", "sha": "s1"})])

        with self.assertRaises(GitHubAPIError):
            GitHubRESTClient(transport=transport).get_file("o/r", "app.py", "main")

    def test_get_file_rejects_invalid_base64(self) -> None:
        transport = FakeGitHubTransport([_json_response(200, {"content": "//8=", "encoding": "base64", "sha": "s1"})])

        with self.assertRaises(GitHubAPIError):
            GitHubRESTClient(transport=transport).get_file("o/r", "app.py", "main")

    def test_update_file_requires_expected_guard(self) -> None:
        client = GitHubRESTClient(transport=FakeGitHubTransport([]))

        with self.assertRaises(GitHubConflictError):
            client.update_file("o/r", "app.py", "agent/fix", "new", "msg")

    def test_update_file_with_expected_sha_puts_sha_and_base64_content(self) -> None:
        transport = FakeGitHubTransport(
            [_json_response(200, {"content": {"sha": "new-sha"}, "commit": {"sha": "commit-sha"}})]
        )
        client = GitHubRESTClient(transport=transport)

        updated = client.update_file("o/r", "app.py", "agent/fix", "new", "msg", expected_sha="old-sha")

        body = json.loads(transport.calls[0]["body"].decode("utf-8"))
        self.assertEqual(body["sha"], "old-sha")
        self.assertEqual(base64.b64decode(body["content"]).decode("utf-8"), "new")
        self.assertEqual(updated["sha"], "new-sha")
        self.assertEqual(updated["commit_sha"], "commit-sha")

    def test_update_file_expected_previous_mismatch_does_not_put(self) -> None:
        encoded = base64.b64encode(b"old").decode("ascii")
        transport = FakeGitHubTransport([_json_response(200, {"content": encoded, "encoding": "base64", "sha": "s1"})])
        client = GitHubRESTClient(transport=transport)

        with self.assertRaises(GitHubConflictError):
            client.update_file("o/r", "app.py", "agent/fix", "new", "msg", expected_previous="different")

        self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_update_file_expected_previous_success_gets_and_puts(self) -> None:
        encoded = base64.b64encode(b"old").decode("ascii")
        transport = FakeGitHubTransport(
            [
                _json_response(200, {"content": encoded, "encoding": "base64", "sha": "old-sha"}),
                _json_response(200, {"content": {"sha": "new-sha"}, "commit": {"sha": "commit-sha"}}),
            ]
        )
        client = GitHubRESTClient(transport=transport)

        updated = client.update_file("o/r", "app.py", "agent/fix", "new", "msg", expected_previous="old")

        self.assertEqual([call["method"] for call in transport.calls], ["GET", "PUT"])
        self.assertEqual(updated["previous"]["content"], "old")

    def test_update_file_requires_message(self) -> None:
        with self.assertRaises(GitHubAPIError):
            GitHubRESTClient(transport=FakeGitHubTransport([])).update_file(
                "o/r", "app.py", "agent/fix", "new", "", expected_sha="old"
            )

    def test_open_pr_idempotency_returns_existing_without_post(self) -> None:
        marker = "<!-- leos-idempotency-key: key-1 -->"
        transport = FakeGitHubTransport(
            [
                _json_response(
                    200,
                    [{"number": 7, "title": "Fix", "body": f"body\n{marker}", "state": "open", "html_url": "url"}],
                )
            ]
        )
        client = GitHubRESTClient(transport=transport)

        pr = client.open_pr("o/r", "Fix", "body", "agent/fix", "main", idempotency_key="key-1")

        self.assertTrue(pr["already_exists"])
        self.assertEqual(pr["number"], 7)
        self.assertEqual([call["method"] for call in transport.calls], ["GET"])

    def test_open_pr_create_posts_idempotency_marker(self) -> None:
        transport = FakeGitHubTransport(
            [
                _json_response(200, []),
                _json_response(201, {"number": 8, "title": "Fix", "state": "open", "html_url": "url"}),
            ]
        )
        client = GitHubRESTClient(transport=transport)

        pr = client.open_pr("o/r", "Fix", "body", "agent/fix", "main", idempotency_key="key-2")

        post_body = json.loads(transport.calls[1]["body"].decode("utf-8"))
        self.assertIn("<!-- leos-idempotency-key: key-2 -->", post_body["body"])
        self.assertFalse(pr["already_exists"])
        self.assertEqual(pr["html_url"], "url")

    def test_open_pr_without_idempotency_posts_without_lookup(self) -> None:
        transport = FakeGitHubTransport(
            [_json_response(201, {"number": 10, "title": "Fix", "state": "open", "html_url": "url"})]
        )

        pr = GitHubRESTClient(transport=transport).open_pr("o/r", "Fix", "body", "agent/fix", "main")

        self.assertEqual([call["method"] for call in transport.calls], ["POST"])
        self.assertIsNone(pr["idempotency_key"])

    def test_open_pr_with_no_matching_marker_posts(self) -> None:
        transport = FakeGitHubTransport(
            [
                _json_response(200, [{"number": 1, "body": "different", "state": "open"}]),
                _json_response(201, {"number": 2, "title": "Fix", "state": "open", "html_url": "url"}),
            ]
        )

        pr = GitHubRESTClient(transport=transport).open_pr(
            "o/r", "Fix", "body", "agent/fix", "main", idempotency_key="new-key"
        )

        self.assertEqual([call["method"] for call in transport.calls], ["GET", "POST"])
        self.assertFalse(pr["already_exists"])

    def test_create_branch_existing_branch_returns_existing(self) -> None:
        transport = FakeGitHubTransport(
            [
                _json_response(200, {"object": {"sha": "base-sha"}}),
                _json_response(422, {"message": "Reference already exists"}),
                _json_response(200, {"object": {"sha": "existing-sha"}}),
            ]
        )
        client = GitHubRESTClient(transport=transport)

        branch = client.create_branch("o/r", "agent/fix", "main")

        self.assertTrue(branch["already_exists"])
        self.assertEqual(branch["sha"], "existing-sha")

    def test_create_branch_propagates_non_existing_error(self) -> None:
        transport = FakeGitHubTransport(
            [
                _json_response(200, {"object": {"sha": "base-sha"}}),
                _json_response(500, {"message": "server error"}),
            ]
        )

        with self.assertRaises(GitHubAPIError):
            GitHubRESTClient(transport=transport).create_branch("o/r", "agent/fix", "main")

    def test_create_branch_requires_base_sha(self) -> None:
        transport = FakeGitHubTransport([_json_response(200, {"object": {}})])

        with self.assertRaises(GitHubAPIError):
            GitHubRESTClient(transport=transport).create_branch("o/r", "agent/fix", "main")

    def test_delete_branch_refuses_protected_names(self) -> None:
        client = GitHubRESTClient(transport=FakeGitHubTransport([]))

        for branch in ("main", "master", "trunk", "release"):
            with self.assertRaises(GitHubConflictError):
                client.delete_branch("o/r", branch)

    def test_delete_branch_404_is_success(self) -> None:
        transport = FakeGitHubTransport([_json_response(404, {"message": "missing"})])

        GitHubRESTClient(transport=transport).delete_branch("o/r", "agent/fix")

        self.assertEqual(transport.calls[0]["method"], "DELETE")

    def test_comment_close_pr_delete_comment_and_ci_status(self) -> None:
        transport = FakeGitHubTransport(
            [
                _json_response(200, {"state": "closed"}),
                _json_response(201, {"id": 11, "html_url": "comment-url"}),
                GitHubHTTPResponse(204, b"", {}),
                _json_response(200, {"state": "weird", "statuses": [{"context": "ci"}]}),
            ]
        )
        client = GitHubRESTClient(transport=transport)

        client.close_pr("o/r", 2)
        comment = client.comment("o/r", 1, "body")
        client.delete_comment("o/r", 11)
        status = client.ci_status("o/r", "abc123")

        self.assertEqual(comment["id"], 11)
        self.assertEqual(status["state"], "unknown")
        self.assertEqual([call["method"] for call in transport.calls], ["PATCH", "POST", "DELETE", "GET"])

    def test_delete_comment_404_is_success(self) -> None:
        transport = FakeGitHubTransport([_json_response(404, {"message": "missing"})])

        GitHubRESTClient(transport=transport).delete_comment("o/r", 9)

        self.assertEqual(transport.calls[0]["method"], "DELETE")

    def test_rate_limit_error(self) -> None:
        transport = FakeGitHubTransport(
            [_json_response(403, {"message": "rate limited"}, {"x-ratelimit-remaining": "0"})]
        )

        with self.assertRaises(GitHubRateLimitError):
            GitHubRESTClient(transport=transport).read_issue("o/r", 1)

    def test_auth_error(self) -> None:
        transport = FakeGitHubTransport([_json_response(401, {"message": "bad credentials"})])

        with self.assertRaises(GitHubAuthError):
            GitHubRESTClient(transport=transport).read_issue("o/r", 1)

    def test_not_found_error(self) -> None:
        transport = FakeGitHubTransport([_json_response(404, {"message": "missing"})])

        with self.assertRaises(GitHubNotFoundError):
            GitHubRESTClient(transport=transport).read_issue("o/r", 1)

    def test_token_not_in_error(self) -> None:
        transport = FakeGitHubTransport([_json_response(500, {"message": "server saw ghp_secret"})])

        with self.assertRaises(GitHubAPIError) as ctx:
            GitHubRESTClient(transport=transport).read_issue("o/r", 1, token="ghp_secret")

        self.assertNotIn("ghp_secret", str(ctx.exception))
        self.assertNotIn("ghp_secret", repr(ctx.exception))
        self.assertNotIn("ghp_secret", ctx.exception.response_body_preview or "")

    def test_invalid_repo_rejected(self) -> None:
        client = GitHubRESTClient(transport=FakeGitHubTransport([]))

        for repo in ("https://github.com/o/r", "../r", "owner/", "/owner/repo"):
            with self.assertRaises(ValueError):
                client.read_issue(repo, 1)

    def test_invalid_branch_ref_and_path_rejected(self) -> None:
        client = GitHubRESTClient(transport=FakeGitHubTransport([]))

        for branch in ("", "/bad", "bad..branch"):
            with self.assertRaises(GitHubAPIError):
                client.create_branch("o/r", branch, "main")
        with self.assertRaises(GitHubAPIError):
            client.get_file("o/r", "../secret", "main")
        with self.assertRaises(GitHubAPIError):
            client.ci_status("o/r", "../ref")

    def test_invalid_json_response_raises_api_error(self) -> None:
        transport = FakeGitHubTransport([GitHubHTTPResponse(200, b"not-json", {})])

        with self.assertRaises(GitHubAPIError):
            GitHubRESTClient(transport=transport).read_issue("o/r", 1)

    def test_urllib_transport_success_and_http_error(self) -> None:
        response = mock.Mock()
        response.status = 200
        response.headers.items.return_value = [("x", "y")]
        response.read.return_value = b"{}"
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=None)
        with mock.patch("urllib.request.urlopen", return_value=response):
            result = UrllibGitHubTransport().request(
                "GET", "https://api.github.com/r", headers={}, body=None, timeout_seconds=1
            )
        self.assertEqual(result.status_code, 200)

        error = urllib.error.HTTPError(
            "https://api.github.com/r",
            404,
            "missing",
            hdrs={},
            fp=BytesIO(b'{"message":"missing"}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            result = UrllibGitHubTransport().request(
                "GET", "https://api.github.com/r", headers={}, body=None, timeout_seconds=1
            )
        self.assertEqual(result.status_code, 404)

    def test_urllib_transport_url_error(self) -> None:
        with (
            mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")),
            self.assertRaises(GitHubAPIError),
        ):
            UrllibGitHubTransport().request("GET", "https://api.github.com/r", headers={}, body=None, timeout_seconds=1)


if __name__ == "__main__":
    unittest.main()
