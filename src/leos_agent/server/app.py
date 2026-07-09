"""FastAPI application factory: a pure transport shell over the operator flow.

The service inlines no gating decisions. Every write request delegates to
``apply_operator_plan`` (``github_operator.py``), which enforces the full
signed-apply path: the ``LEOS_ENABLE_REAL_GITHUB_WRITES`` environment gate,
plan/approval/decision digest + profile + plan_id matching,
``PolicyEngine.from_profile``, runtime egress enforcement, the
``PreparedFileApprovalGate`` (signed, expiring, consume-once decisions), and a
VERIFIED-only success check. Boundary auth (a static API key compared in
constant time) protects the transport only — it never substitutes for the
approval gate.

FastAPI is an optional dependency imported lazily inside :func:`create_app`;
when absent a typed :class:`ServerUnavailable` is raised (mirroring
``SandboxUnavailable``). Secrets (GitHub token, approval HMAC secret) are read
from the server environment at request time and never accepted in request
bodies or echoed in responses.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..approval import ApprovalPacket, render_approval_packet_html
from ..approval_exchange import (
    build_decision_for_packet,
    read_approval_packet,
    sign_approval_decision,
    write_approval_decision,
)
from ..errors import LeosError
from ..github_operator import (
    PROFILE,
    apply_operator_plan,
    build_approval_bundle,
    build_signed_decision_bundle,
    create_draft_plan,
    validate_operator_plan,
)
from ..github_tools import GitHubClient
from ..policy import PolicyEngine
from ..tools import Secret
from ..trace_viewer import render_trace_html

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class ServerUnavailable(LeosError):
    """Raised when the optional FastAPI dependency is missing."""


class ServerConfigurationError(LeosError):
    """Raised when the service is started without required configuration."""


MIN_API_KEY_LENGTH = 32


class _TokenBucket:
    """Thread-safe in-memory token bucket (no external dependencies)."""

    def __init__(self, capacity: float, refill_per_second: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._clock = clock
        self._tokens = capacity
        self._updated = clock()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self._clock()
            self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._refill_per_second)
            self._updated = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


def _parse_api_keys(raw: str) -> tuple[str, ...]:
    """Split a comma-separated key list and enforce minimum strength.

    Multiple keys enable zero-downtime rotation: serve old + new together,
    move clients over, then drop the old key.
    """
    keys = tuple(candidate.strip() for candidate in raw.split(",") if candidate.strip())
    if not keys:
        raise ServerConfigurationError("an API key is required (api_key= or LEOS_SERVER_API_KEY); refusing to start")
    weak = sum(1 for candidate in keys if len(candidate) < MIN_API_KEY_LENGTH)
    if weak:
        raise ServerConfigurationError(
            f"{weak} API key(s) are shorter than {MIN_API_KEY_LENGTH} characters; refusing to start with weak keys"
        )
    return keys


def create_app(
    *,
    api_key: str | None = None,
    data_dir: Path,
    github_client: GitHubClient | None = None,
    inbox_dir: Path | None = None,
    rate_limit_per_minute: int = 60,
    max_body_bytes: int = 1_000_000,
) -> Any:
    """Build the FastAPI app.

    ``api_key`` (or ``LEOS_SERVER_API_KEY``) is mandatory — the service fails
    closed rather than starting unauthenticated. Multiple comma-separated keys
    are accepted for zero-downtime rotation; every key must be at least
    ``MIN_API_KEY_LENGTH`` characters. ``data_dir`` holds audit logs and
    approval receipts. ``github_client`` is injectable for tests and for
    callers that manage their own transport. ``inbox_dir`` (optional) enables
    the web approval inbox over a ``FileApprovalGate``-compatible exchange
    directory (``packets/`` and ``decisions/`` inside it).
    ``rate_limit_per_minute`` throttles the write endpoints (0 disables);
    ``max_body_bytes`` rejects oversized request bodies with 413 (0 disables).
    """
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Request
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:
        raise ServerUnavailable("the service layer requires the optional 'fastapi' package") from exc

    raw_keys = api_key or os.environ.get("LEOS_SERVER_API_KEY")
    if not raw_keys:
        raise ServerConfigurationError("an API key is required (api_key= or LEOS_SERVER_API_KEY); refusing to start")
    api_keys = _parse_api_keys(raw_keys)
    audits_dir = data_dir / "audits"
    receipts_dir = data_dir / "receipts"

    def require_api_key(x_leos_api_key: str | None = Header(default=None)) -> None:
        provided = x_leos_api_key or ""
        # Compare against every configured key so timing does not reveal
        # which key (if any) matched.
        matched = False
        for candidate in api_keys:
            if hmac.compare_digest(provided, candidate):
                matched = True
        if not matched:
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    write_bucket = (
        _TokenBucket(float(rate_limit_per_minute), rate_limit_per_minute / 60.0) if rate_limit_per_minute > 0 else None
    )

    def require_write_budget() -> None:
        if write_bucket is not None and not write_bucket.allow():
            raise HTTPException(status_code=429, detail="write rate limit exceeded; retry later")

    app = FastAPI(title="Leos Operator Service", docs_url=None, redoc_url=None, openapi_url=None)
    authed = [Depends(require_api_key)]
    write_guarded = [Depends(require_api_key), Depends(require_write_budget)]

    if max_body_bytes > 0:

        @app.middleware("http")
        async def limit_body_size(request: Request, call_next: Callable[[Request], Any]) -> Any:
            length = request.headers.get("content-length")
            if length is not None and length.isdigit() and int(length) > max_body_bytes:
                return JSONResponse(status_code=413, content={"detail": "request body too large"})
            return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, Any]:
        try:
            PolicyEngine.from_profile(PROFILE)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"policy profile unavailable: {type(exc).__name__}") from exc
        return {
            "status": "ready",
            "profile": PROFILE,
            "writes_enabled": os.environ.get("LEOS_ENABLE_REAL_GITHUB_WRITES") == "1",
        }

    @app.post("/plans", dependencies=authed)
    def post_plans(body: dict[str, Any]) -> dict[str, Any]:
        repo = str(body.get("repo", ""))
        issue_number = body.get("issue_number")
        if not repo or not isinstance(issue_number, int):
            raise HTTPException(status_code=400, detail="repo and issue_number are required")
        try:
            return create_draft_plan(repo, issue_number, client=github_client)
        except (LeosError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"draft plan failed: {exc}") from exc

    @app.post("/plans/validate", dependencies=authed)
    def post_plans_validate(body: dict[str, Any]) -> dict[str, Any]:
        plan = body.get("plan")
        if not isinstance(plan, dict):
            raise HTTPException(status_code=400, detail="plan object is required")
        issues = validate_operator_plan(plan)
        return {"ready": not issues, "issues": issues}

    @app.post("/approvals", dependencies=write_guarded)
    def post_approvals(body: dict[str, Any]) -> dict[str, Any]:
        plan = body.get("plan")
        if not isinstance(plan, dict):
            raise HTTPException(status_code=400, detail="plan object is required")
        expires = float(body.get("expires_in_seconds", 900.0))
        try:
            return build_approval_bundle(plan, expires_in_seconds=expires)
        except (LeosError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"approval bundle failed: {exc}") from exc

    @app.post("/approvals/decide", dependencies=write_guarded)
    def post_approvals_decide(body: dict[str, Any]) -> dict[str, Any]:
        approval = body.get("approval")
        if not isinstance(approval, dict):
            raise HTTPException(status_code=400, detail="approval bundle is required")
        secret = os.environ.get("LEOS_APPROVAL_HMAC_SECRET")
        if not secret:
            raise HTTPException(status_code=403, detail="LEOS_APPROVAL_HMAC_SECRET is required")
        try:
            return build_signed_decision_bundle(
                approval,
                decision_value=str(body.get("decision", "deny")),
                approver=str(body.get("approver", "")),
                signature_secret=secret,
                reason=str(body["reason"]) if body.get("reason") is not None else None,
            )
        except (LeosError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"decision bundle failed: {exc}") from exc

    @app.post("/apply", dependencies=write_guarded)
    def post_apply(body: dict[str, Any]) -> dict[str, Any]:
        plan = body.get("plan")
        approval = body.get("approval")
        decision = body.get("decision")
        if not isinstance(plan, dict) or not isinstance(approval, dict) or not isinstance(decision, dict):
            raise HTTPException(status_code=400, detail="plan, approval, and decision objects are required")
        plan_id = _safe_plan_id(str(plan.get("plan_id", "")))
        token_value = os.environ.get("LEOS_GITHUB_TOKEN")
        if not token_value:
            raise HTTPException(status_code=403, detail="LEOS_GITHUB_TOKEN is required")
        secret = os.environ.get("LEOS_APPROVAL_HMAC_SECRET")
        if not secret:
            raise HTTPException(status_code=403, detail="LEOS_APPROVAL_HMAC_SECRET is required")
        audit_path = audits_dir / plan_id / f"apply-{time.time_ns()}.jsonl"
        result = apply_operator_plan(
            plan,
            approval,
            decision,
            token=Secret(token_value),
            signature_secret=secret,
            audit_path=audit_path,
            receipt_dir=receipts_dir,
            client=github_client,
        )
        if not result.ok:
            raise HTTPException(status_code=403, detail=result.message)
        data = {k: v for k, v in result.data.items() if k != "audit_path"}
        return {"ok": True, "message": result.message, "data": data}

    @app.get("/audit/{plan_id}", dependencies=authed)
    def get_audit(plan_id: str) -> dict[str, Any]:
        return {"events": _load_audit_records(audits_dir, _safe_plan_id(plan_id))}

    @app.get("/trace/{plan_id}", dependencies=authed)
    def get_trace(plan_id: str) -> Any:
        records = _load_audit_records(audits_dir, _safe_plan_id(plan_id))
        return HTMLResponse(render_trace_html(records, title=f"Leos Trace {plan_id}"))

    if inbox_dir is not None:
        packet_dir = inbox_dir / "packets"
        decision_dir = inbox_dir / "decisions"

        def _read_packet(approval_id: str) -> ApprovalPacket:
            if not _SAFE_ID.match(approval_id):
                raise HTTPException(status_code=400, detail="invalid approval id")
            path = packet_dir / f"{approval_id}.json"
            if not path.exists():
                raise HTTPException(status_code=404, detail="unknown approval packet")
            return read_approval_packet(path)

        @app.get("/inbox", dependencies=authed)
        def get_inbox() -> dict[str, Any]:
            pending = []
            for path in sorted(packet_dir.glob("*.json")) if packet_dir.is_dir() else []:
                if (decision_dir / path.name).exists():
                    continue  # already decided
                packet = read_approval_packet(path)
                pending.append(
                    {
                        "approval_id": packet.approval_id,
                        "plan_id": packet.plan_id,
                        "tool_name": packet.tool_name,
                        "risk_level": packet.risk_level,
                        "action_summary": packet.action_summary,
                        "expires_at": packet.expires_at,
                        "profile": packet.profile,
                    }
                )
            return {"pending": pending}

        @app.get("/inbox/{approval_id}", dependencies=authed)
        def get_inbox_packet(approval_id: str) -> Any:
            packet = _read_packet(approval_id)
            return HTMLResponse(render_approval_packet_html(packet))

        @app.post("/inbox/{approval_id}/decide", dependencies=write_guarded)
        def post_inbox_decide(approval_id: str, body: dict[str, Any]) -> dict[str, Any]:
            packet = _read_packet(approval_id)
            secret = os.environ.get("LEOS_APPROVAL_HMAC_SECRET")
            if not secret:
                raise HTTPException(status_code=403, detail="LEOS_APPROVAL_HMAC_SECRET is required")
            decision_path = decision_dir / f"{approval_id}.json"
            if decision_path.exists():
                raise HTTPException(status_code=409, detail="a decision was already recorded for this packet")
            try:
                decision = build_decision_for_packet(
                    packet,
                    str(body.get("decision", "")),
                    str(body.get("approver", "")) or None,
                    reason=str(body["reason"]) if body.get("reason") is not None else None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"invalid decision: {exc}") from exc
            signature = sign_approval_decision(decision, secret)
            decision_dir.mkdir(parents=True, exist_ok=True)
            write_approval_decision(decision, decision_path, signature=signature)
            return {
                "approval_id": packet.approval_id,
                "decision": decision.decision.value,
                "signature_algorithm": "hmac-sha256",
            }

    def _safe_plan_id(plan_id: str) -> str:
        if not _SAFE_ID.match(plan_id):
            raise HTTPException(status_code=400, detail="invalid plan id")
        return plan_id

    def _load_audit_records(root: Path, plan_id: str) -> list[dict[str, Any]]:
        plan_dir = root / plan_id
        files = sorted(plan_dir.glob("apply-*.jsonl")) if plan_dir.is_dir() else []
        if not files:
            raise HTTPException(status_code=404, detail="no audit records for this plan")
        records: list[dict[str, Any]] = []
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        return records

    return app
