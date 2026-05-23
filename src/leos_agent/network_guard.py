"""Runtime egress guards for outbound HTTP clients."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .egress import EgressPolicy
from .errors import RuntimeEgressBlocked


@dataclass(frozen=True)
class RuntimeEgressDecision:
    allowed: bool
    host: str
    method: str
    reason: str | None = None


class RuntimeEgressGuard:
    """Deny-by-default runtime guard for outbound URL/method pairs."""

    def __init__(self, policy: EgressPolicy | None, *, allow_unrestricted: bool = False) -> None:
        self.policy = policy
        self.allow_unrestricted = allow_unrestricted

    def check_url(self, url: str, method: str) -> RuntimeEgressDecision:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        normalized_method = method.strip().upper()
        if self.allow_unrestricted:
            return RuntimeEgressDecision(True, host, normalized_method)
        if self.policy is None:
            return RuntimeEgressDecision(
                False,
                host,
                normalized_method,
                "runtime egress denied: no egress policy configured",
            )
        if not host:
            return RuntimeEgressDecision(False, host, normalized_method, "runtime egress denied: missing host")
        if not self.policy.allows(host, normalized_method):
            return RuntimeEgressDecision(
                False,
                host,
                normalized_method,
                f"runtime egress denied for {normalized_method} {host}",
            )
        return RuntimeEgressDecision(True, host, normalized_method)

    def require_url(self, url: str, method: str) -> RuntimeEgressDecision:
        decision = self.check_url(url, method)
        if not decision.allowed:
            raise RuntimeEgressBlocked(decision.reason or "runtime egress denied")
        return decision
