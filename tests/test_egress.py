from __future__ import annotations

import unittest

from leos_agent.egress import EgressPolicy, _host_is_blocked_address


class EgressPolicyTests(unittest.TestCase):
    def test_custom_allowed_methods(self) -> None:
        policy = EgressPolicy(allowed_hosts=("valid.host",), allowed_methods=("GET",))
        self.assertTrue(policy.allows("valid.host", "GET"))
        self.assertFalse(policy.allows("valid.host", "POST"))

    def test_allowed_methods_case_insensitive(self) -> None:
        policy = EgressPolicy(allowed_hosts=("host.com",), allowed_methods=("GET",))
        self.assertTrue(policy.allows("host.com", "get"))
        self.assertTrue(policy.allows("host.com", "Get"))

    def test_max_requests_parameter_accepted(self) -> None:
        policy = EgressPolicy(allowed_hosts=("host.com",), max_requests=1)
        self.assertTrue(policy.allows("host.com"))
        self.assertEqual(policy.max_requests, 1)

    def test_rejects_empty_host(self) -> None:
        policy = EgressPolicy(allowed_hosts=("valid.host",))
        self.assertFalse(policy.allows(""))
        self.assertFalse(policy.allows("   "))

    def test_rejects_wildcard_host(self) -> None:
        policy = EgressPolicy(allowed_hosts=("valid.host",))
        self.assertFalse(policy.allows("*"))
        self.assertFalse(policy.allows("*.com"))

    def test_host_normalization_strips_and_lowers(self) -> None:
        policy = EgressPolicy(allowed_hosts=("host.com",))
        self.assertTrue(policy.allows("  HOST.COM  "))

    def test_host_not_in_allowlist(self) -> None:
        policy = EgressPolicy(allowed_hosts=("allowed.com",))
        self.assertFalse(policy.allows("other.com"))

    def test_dns_rebind_protection_flag(self) -> None:
        policy = EgressPolicy(allowed_hosts=("allowed.com",), dns_rebind_protection=False)
        self.assertFalse(policy.dns_rebind_protection)
        self.assertTrue(policy.allows("allowed.com"))
        self.assertFalse(policy.allows("127.0.0.1"))


class HostIsBlockedAddressTests(unittest.TestCase):
    def test_ipv4_loopback(self) -> None:
        self.assertTrue(_host_is_blocked_address("127.0.0.1"))
        self.assertTrue(_host_is_blocked_address("127.0.0.2"))

    def test_ipv4_private(self) -> None:
        self.assertTrue(_host_is_blocked_address("192.168.1.1"))
        self.assertTrue(_host_is_blocked_address("10.0.0.1"))
        self.assertTrue(_host_is_blocked_address("172.16.0.1"))

    def test_ipv6_loopback(self) -> None:
        self.assertTrue(_host_is_blocked_address("::1"))

    def test_ipv6_private_ula(self) -> None:
        self.assertTrue(_host_is_blocked_address("fd00::1"))

    def test_ipv6_link_local(self) -> None:
        self.assertTrue(_host_is_blocked_address("fe80::1"))

    def test_ipv6_multicast(self) -> None:
        self.assertTrue(_host_is_blocked_address("ff02::1"))

    def test_ipv6_global_unicast_not_blocked(self) -> None:
        self.assertFalse(_host_is_blocked_address("2001:4860:4860::8888"))

    def test_localhost_string(self) -> None:
        self.assertTrue(_host_is_blocked_address("localhost"))

    def test_empty_string(self) -> None:
        self.assertFalse(_host_is_blocked_address(""))

    def test_bracketed_ipv6(self) -> None:
        self.assertTrue(_host_is_blocked_address("[::1]"))

    def test_public_ip_not_blocked(self) -> None:
        self.assertFalse(_host_is_blocked_address("8.8.8.8"))
        self.assertFalse(_host_is_blocked_address("93.184.216.34"))

    def test_normal_hostname_not_blocked(self) -> None:
        self.assertFalse(_host_is_blocked_address("api.github.com"))
        self.assertFalse(_host_is_blocked_address("example.com"))


if __name__ == "__main__":
    unittest.main()
