"""Concrete model client adapters with optional provider dependencies."""

from __future__ import annotations

import importlib
import json
import urllib.error
import urllib.request
from dataclasses import asdict
from typing import Any

from .audit import AuditLog
from .model import ModelCallError, ModelRequest, ModelResponse, ModelUsage, StructuredOutputError
from .tools import Secret

PREVIEW_LIMIT = 512


def _preview(text: str, limit: int = PREVIEW_LIMIT) -> str:
    return text[:limit] + ("...[truncated]" if len(text) > limit else "")


def _reject_secrets(value: Any) -> None:
    if isinstance(value, Secret):
        raise ModelCallError("Secret values must not be passed to model prompts")
    if isinstance(value, str) and "<secret>" in value.lower():
        raise ModelCallError("Redacted secret marker must not be passed to model prompts")
    if isinstance(value, dict):
        for item in value.values():
            _reject_secrets(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _reject_secrets(item)


def _reject_model_request(request: ModelRequest) -> None:
    _reject_secrets(request.prompt)
    _reject_secrets(request.system)
    _reject_secrets(request.schema)
    _reject_secrets(request.metadata)


def _audit_request(audit_log: AuditLog | None, provider: str, request: ModelRequest) -> None:
    if audit_log is None:
        return
    audit_log.record(
        "model.requested",
        "Model request sent",
        provider=provider,
        model=request.model,
        prompt_preview=_preview(request.prompt),
        system_preview=_preview(request.system or ""),
        metadata={k: "<redacted>" if isinstance(v, Secret) else v for k, v in request.metadata.items()},
    )


def _audit_response(audit_log: AuditLog | None, provider: str, response: ModelResponse) -> None:
    if audit_log is None:
        return
    audit_log.record(
        "model.responded",
        "Model response received",
        provider=provider,
        model=response.model,
        response_preview=_preview(response.text),
        usage=asdict(response.usage) if response.usage else None,
    )


class LocalHTTPModelClient:
    """Minimal JSON-over-HTTP model adapter using the Python standard library."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 30.0, audit_log: AuditLog | None = None) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.audit_log = audit_log

    def generate(self, request: ModelRequest) -> ModelResponse:
        _reject_model_request(request)
        _audit_request(self.audit_log, "local_http", request)
        payload = json.dumps(
            {
                "prompt": request.prompt,
                "system": request.system,
                "schema": request.schema,
                "model": request.model,
                "temperature": request.temperature,
                "metadata": request.metadata,
            },
            default=str,
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:  # nosec B310
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise ModelCallError(f"Local HTTP model call failed: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError(f"Local HTTP model returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise StructuredOutputError("Local HTTP model response must be a JSON object")
        text = str(data.get("text", ""))
        usage_data = data.get("usage")
        usage = ModelUsage(**usage_data) if isinstance(usage_data, dict) else None
        model = str(data.get("model", request.model))
        parsed_json = data.get("parsed_json")
        response = ModelResponse(text=text, parsed_json=parsed_json, model=model, usage=usage, raw=data)
        _audit_response(self.audit_log, "local_http", response)
        return response


class OpenAIModelClient:
    """OpenAI SDK adapter loaded only when the optional SDK is installed."""

    def __init__(self, *, model: str, api_key: Secret | None = None, audit_log: AuditLog | None = None) -> None:
        self.model = model
        self.api_key = api_key
        self.audit_log = audit_log

    def generate(self, request: ModelRequest) -> ModelResponse:
        _reject_model_request(request)
        try:
            openai = importlib.import_module("openai")
        except ImportError as exc:
            raise ModelCallError("OpenAIModelClient requires the optional 'openai' package") from exc
        _audit_request(self.audit_log, "openai", request)
        try:
            client = openai.OpenAI(api_key=self.api_key.unwrap() if self.api_key else None)
            result = client.responses.create(
                model=request.model if request.model != "unknown" else self.model,
                input=request.prompt,
                temperature=request.temperature,
            )
            text = str(getattr(result, "output_text", ""))
        except Exception as exc:  # noqa: BLE001 - provider errors are normalized
            raise ModelCallError(f"OpenAI model call failed: {exc}") from exc
        response = ModelResponse(
            text=text, model=request.model if request.model != "unknown" else self.model, raw=result
        )
        _audit_response(self.audit_log, "openai", response)
        return response


class AnthropicModelClient:
    """Anthropic SDK adapter loaded only when the optional SDK is installed."""

    def __init__(self, *, model: str, api_key: Secret | None = None, audit_log: AuditLog | None = None) -> None:
        self.model = model
        self.api_key = api_key
        self.audit_log = audit_log

    def generate(self, request: ModelRequest) -> ModelResponse:
        _reject_model_request(request)
        try:
            anthropic = importlib.import_module("anthropic")
        except ImportError as exc:
            raise ModelCallError("AnthropicModelClient requires the optional 'anthropic' package") from exc
        _audit_request(self.audit_log, "anthropic", request)
        try:
            client = anthropic.Anthropic(api_key=self.api_key.unwrap() if self.api_key else None)
            result = client.messages.create(
                model=request.model if request.model != "unknown" else self.model,
                max_tokens=1024,
                temperature=request.temperature,
                messages=[{"role": "user", "content": request.prompt}],
            )
            content = getattr(result, "content", [])
            text = "".join(str(getattr(block, "text", "")) for block in content)
        except Exception as exc:  # noqa: BLE001 - provider errors are normalized
            raise ModelCallError(f"Anthropic model call failed: {exc}") from exc
        response = ModelResponse(
            text=text,
            model=request.model if request.model != "unknown" else self.model,
            raw=result,
        )
        _audit_response(self.audit_log, "anthropic", response)
        return response
