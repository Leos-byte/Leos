"""Policy-level outbound network egress constraints."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address


@dataclass(frozen=True)
class EgressPolicy:
    """A small fail-closed egress allowlist used by policy profiles."""

    allowed_hosts: tuple[str, ...]
    allowed_methods: tuple[str, ...] = ("GET", "POST", "PATCH", "PUT", "DELETE")
    max_requests: int | None = None
    dns_rebind_protection: bool = True

    def allows(self, host: str, method: str = "GET") -> bool:
        normalized_host = host.strip().lower()
        normalized_method = method.strip().upper()
        if not normalized_host or "*" in normalized_host:
            return False
        if normalized_method not in {value.upper() for value in self.allowed_methods}:
            return False
        if _host_is_blocked_address(normalized_host):
            return False
        return normalized_host in {value.lower() for value in self.allowed_hosts}


def _host_is_blocked_address(host: str) -> bool:
    try:
        parsed = ip_address(host.strip("[]"))
    except ValueError:
        return host in {"localhost"}
    return (
        parsed.is_loopback
        or parsed.is_private
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )
