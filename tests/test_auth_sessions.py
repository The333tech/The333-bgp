import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.requests import Request


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class LifespanTests(unittest.TestCase):
    def test_lifespan_runs_migrations_and_startup_once(self) -> None:
        with (
            patch.object(main, "run_data_migrations") as migrations,
            patch.object(main, "startup", new=AsyncMock()) as startup,
            TestClient(main.app),
        ):
            pass

        migrations.assert_called_once_with()
        startup.assert_awaited_once_with()


def make_request(client_host: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers or [],
            "client": (client_host, 12345),
            "server": ("testserver", 80),
        }
    )


class AuthSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.originals = {
            "WEB_PASSWORD": main.WEB_PASSWORD,
            "WEB_PASSWORD_HASH": main.WEB_PASSWORD_HASH,
            "AUTH_MAX_FAILURES": main.AUTH_MAX_FAILURES,
            "AUTH_ALLOW_BASIC": main.AUTH_ALLOW_BASIC,
            "AUTH_TRUSTED_PROXY_CIDRS": main.AUTH_TRUSTED_PROXY_CIDRS,
            "SESSION_COOKIE_SECURE": main.SESSION_COOKIE_SECURE,
            "SESSION_TTL_SECONDS": main.SESSION_TTL_SECONDS,
        }
        main.WEB_PASSWORD = "unit-test-password"
        main.WEB_PASSWORD_HASH = ""
        main.AUTH_MAX_FAILURES = 3
        main.AUTH_ALLOW_BASIC = False
        main.AUTH_TRUSTED_PROXY_CIDRS = "172.16.0.0/12"
        main.SESSION_COOKIE_SECURE = False
        main.SESSION_TTL_SECONDS = 3600
        with main.AUTH_LOCK:
            main.AUTH_FAILURES.clear()
            main.AUTH_SESSIONS.clear()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        for key, value in self.originals.items():
            setattr(main, key, value)
        with main.AUTH_LOCK:
            main.AUTH_FAILURES.clear()
            main.AUTH_SESSIONS.clear()

    def login(self):
        return self.client.post("/auth/login", json={"password": "unit-test-password"})

    def test_login_uses_httponly_strict_cookie_and_returns_only_session_metadata(self) -> None:
        response = self.login()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"ok", "csrf_token", "expires_at"})
        self.assertNotIn("unit-test-password", response.text)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertNotIn("secure", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_csrf_is_required_and_logout_invalidates_session(self) -> None:
        login_response = self.login()
        csrf_token = login_response.json()["csrf_token"]

        self.assertEqual(self.client.get("/auth/session").status_code, 200)
        self.assertEqual(self.client.post("/auth/logout").status_code, 403)
        self.assertEqual(
            self.client.post("/auth/logout", headers={"X-CSRF-Token": csrf_token}).status_code,
            200,
        )
        self.assertEqual(self.client.get("/auth/session").status_code, 401)

    def test_repeated_bad_passwords_are_rate_limited(self) -> None:
        for _ in range(main.AUTH_MAX_FAILURES):
            response = self.client.post("/auth/login", json={"password": "wrong-password"})
            self.assertEqual(response.status_code, 401)

        blocked = self.client.post("/auth/login", json={"password": "unit-test-password"})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_forwarded_headers_are_only_trusted_from_configured_proxy(self) -> None:
        headers = [(b"x-forwarded-for", b"198.51.100.22"), (b"x-forwarded-proto", b"https")]
        direct_request = make_request("203.0.113.9", headers)
        proxy_request = make_request("172.20.0.5", headers)

        self.assertEqual(main.auth_client_key(direct_request), "203.0.113.9")
        self.assertFalse(main.request_uses_https(direct_request))
        self.assertEqual(main.auth_client_key(proxy_request), "198.51.100.22")
        self.assertTrue(main.request_uses_https(proxy_request))

    def test_invalid_proxy_cidr_is_ignored_without_breaking_login(self) -> None:
        main.AUTH_TRUSTED_PROXY_CIDRS = "not-a-network,172.16.0.0/12"
        request = make_request("172.20.0.5", [(b"x-forwarded-for", b"198.51.100.22")])

        self.assertEqual(main.auth_client_key(request), "198.51.100.22")


if __name__ == "__main__":
    unittest.main()
