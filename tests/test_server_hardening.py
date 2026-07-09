"""Tests for service-surface hardening: key rotation, rate limits, body caps."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leos_agent.server import ServerConfigurationError, create_app
from leos_agent.server.app import _TokenBucket

try:
    from fastapi.testclient import TestClient

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover - exercised only without fastapi installed
    HAVE_FASTAPI = False

_OLD_KEY = "old-rotation-key-0123456789abcdef01"
_NEW_KEY = "new-rotation-key-0123456789abcdef01"


@unittest.skipUnless(HAVE_FASTAPI, "requires the optional 'fastapi' package")
class ApiKeyHardeningTests(unittest.TestCase):
    def _app(self, api_key: str, **kwargs: object) -> TestClient:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        return TestClient(create_app(api_key=api_key, data_dir=Path(self._dir.name), **kwargs))

    def test_multiple_keys_allow_zero_downtime_rotation(self) -> None:
        http = self._app(f"{_OLD_KEY}, {_NEW_KEY}")
        for key in (_OLD_KEY, _NEW_KEY):
            response = http.post("/plans/validate", json={"plan": {}}, headers={"x-leos-api-key": key})
            self.assertEqual(response.status_code, 200, key)
        response = http.post("/plans/validate", json={"plan": {}}, headers={"x-leos-api-key": "z" * 40})
        self.assertEqual(response.status_code, 401)

    def test_weak_key_refuses_to_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ServerConfigurationError) as ctx:
                create_app(api_key="short-key", data_dir=Path(tmp))
            self.assertIn("32", str(ctx.exception))
            self.assertNotIn("short-key", str(ctx.exception))

    def test_one_weak_key_among_strong_ones_refuses_to_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ServerConfigurationError):
            create_app(api_key=f"{_OLD_KEY},tiny", data_dir=Path(tmp))


@unittest.skipUnless(HAVE_FASTAPI, "requires the optional 'fastapi' package")
class RateLimitTests(unittest.TestCase):
    def _app(self, **kwargs: object) -> TestClient:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        return TestClient(create_app(api_key=_OLD_KEY, data_dir=Path(self._dir.name), **kwargs))

    def test_write_endpoints_return_429_when_budget_exhausted(self) -> None:
        http = self._app(rate_limit_per_minute=2)
        auth = {"x-leos-api-key": _OLD_KEY}
        codes = [http.post("/approvals", json={"plan": {}}, headers=auth).status_code for _ in range(3)]
        self.assertEqual(codes[:2], [400, 400])  # invalid plan, but inside budget
        self.assertEqual(codes[2], 429)

    def test_read_endpoints_are_not_rate_limited(self) -> None:
        http = self._app(rate_limit_per_minute=1)
        auth = {"x-leos-api-key": _OLD_KEY}
        for _ in range(5):
            self.assertEqual(http.post("/plans/validate", json={"plan": {}}, headers=auth).status_code, 200)

    def test_zero_disables_rate_limiting(self) -> None:
        http = self._app(rate_limit_per_minute=0)
        auth = {"x-leos-api-key": _OLD_KEY}
        codes = {http.post("/approvals", json={"plan": {}}, headers=auth).status_code for _ in range(5)}
        self.assertEqual(codes, {400})

    def test_token_bucket_refills_over_time(self) -> None:
        clock = [0.0]
        bucket = _TokenBucket(1.0, 1.0, clock=lambda: clock[0])
        self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())
        clock[0] = 1.1
        self.assertTrue(bucket.allow())


@unittest.skipUnless(HAVE_FASTAPI, "requires the optional 'fastapi' package")
class BodySizeLimitTests(unittest.TestCase):
    def _app(self, **kwargs: object) -> TestClient:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        return TestClient(create_app(api_key=_OLD_KEY, data_dir=Path(self._dir.name), **kwargs))

    def test_oversized_body_is_rejected_with_413(self) -> None:
        http = self._app(max_body_bytes=256)
        auth = {"x-leos-api-key": _OLD_KEY}
        response = http.post("/plans/validate", json={"plan": {"pad": "x" * 1024}}, headers=auth)
        self.assertEqual(response.status_code, 413)

    def test_normal_body_passes(self) -> None:
        http = self._app(max_body_bytes=256)
        auth = {"x-leos-api-key": _OLD_KEY}
        response = http.post("/plans/validate", json={"plan": {}}, headers=auth)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
