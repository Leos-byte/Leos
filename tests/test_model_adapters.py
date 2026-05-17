from __future__ import annotations

import json
import unittest
from unittest import mock

from leos_agent import AuditLog, ModelRequest, Secret
from leos_agent.model import ModelCallError, StructuredOutputError
from leos_agent.model_adapters import AnthropicModelClient, LocalHTTPModelClient, OpenAIModelClient


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class ModelAdapterTests(unittest.TestCase):
    def test_local_http_model_client_parses_json_response(self) -> None:
        body = b'{"text": "ok", "parsed_json": {"answer": 1}, "model": "local"}'
        with mock.patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            response = LocalHTTPModelClient("http://model.local").generate(ModelRequest(prompt="hi"))

        self.assertEqual(response.text, "ok")
        self.assertEqual(response.parsed_json, {"answer": 1})
        self.assertEqual(response.model, "local")

    def test_local_http_invalid_json_raises_structured_error(self) -> None:
        with (
            mock.patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(b"not-json")),
            self.assertRaises(StructuredOutputError),
        ):
            LocalHTTPModelClient("http://model.local").generate(ModelRequest(prompt="hi"))

    def test_missing_optional_sdks_raise_clear_errors(self) -> None:
        with mock.patch("importlib.import_module", side_effect=ImportError("missing")):
            with self.assertRaisesRegex(ModelCallError, "openai"):
                OpenAIModelClient(model="x").generate(ModelRequest(prompt="hi"))
            with self.assertRaisesRegex(ModelCallError, "anthropic"):
                AnthropicModelClient(model="x").generate(ModelRequest(prompt="hi"))

    def test_audit_preview_is_truncated(self) -> None:
        audit = AuditLog()
        body = json.dumps({"text": "x" * 1000}).encode("utf-8")
        with mock.patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(body)):
            LocalHTTPModelClient("http://model.local", audit_log=audit).generate(ModelRequest(prompt="p" * 1000))

        payloads = [event.payload for event in audit.events]
        self.assertLess(len(payloads[0]["prompt_preview"]), 530)
        self.assertLess(len(payloads[1]["response_preview"]), 530)

    def test_secret_values_are_rejected_before_prompting(self) -> None:
        with self.assertRaises(ModelCallError):
            LocalHTTPModelClient("http://model.local").generate(
                ModelRequest(prompt="hi", metadata={"token": Secret("x")})
            )


if __name__ == "__main__":
    unittest.main()
