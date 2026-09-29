import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class RouteLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.snapshot = Path(self.temp_dir.name) / "last_good_route_snapshot.json"
        self.snapshot_patch = patch.object(main, "LAST_GOOD_SNAPSHOT_FILE", self.snapshot)
        self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        main.app.dependency_overrides[main.require_auth] = lambda: "test"
        self.addCleanup(main.app.dependency_overrides.pop, main.require_auth, None)

    def save_snapshot(self) -> None:
        main.write_json_atomic(self.snapshot, {
            "version": 1,
            "updated_at": "2026-09-29T00:00:00+00:00",
            "prefixes": ["1.1.0.0/16", "1.1.1.0/24", "8.8.8.0/24"],
            "route_communities": {"1.1.1.0/24": ["64500:510:1"]},
        })

    def lookup(self, query: str):
        return self.client.post("/api/routes/lookup", json={"query": query})

    def test_ip_uses_longest_match_and_snapshot_attributes(self) -> None:
        self.save_snapshot()

        response = self.lookup("1.1.1.42")
        payload = response.json()

        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(payload["kind"], "ip")
        self.assertEqual(payload["snapshot_route_count"], 3)
        self.assertEqual(payload["snapshot_updated_at"], "2026-09-29T00:00:00+00:00")
        self.assertEqual(payload["addresses"][0]["matches"], [
            {"prefix": "1.1.1.0/24", "communities": ["64500:510:1"]},
            {"prefix": "1.1.0.0/16", "communities": []},
        ])
        self.assertFalse(payload["origin_available"])

    def test_no_match_is_not_reported_as_router_state(self) -> None:
        self.save_snapshot()

        payload = self.lookup("192.168.1.1").json()

        self.assertEqual(payload["addresses"][0]["matches"], [])
        self.assertEqual(payload["addresses"][0]["match_count"], 0)

    def test_domain_resolves_ipv4_without_http_fetch(self) -> None:
        self.save_snapshot()
        fake_answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.42", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.42", 0)),
        ]

        with patch.object(socket, "getaddrinfo", return_value=fake_answers) as resolver:
            payload = self.lookup("Example.COM").json()

        self.assertEqual(payload["normalized"], "example.com")
        self.assertEqual([item["address"] for item in payload["addresses"]], ["1.1.1.42", "8.8.8.8"])
        self.assertIsNone(payload["dns_error"])
        resolver.assert_called_once()

    def test_dns_failure_is_explicit_not_a_false_negative(self) -> None:
        self.save_snapshot()
        with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror()):
            payload = self.lookup("example.com").json()

        self.assertEqual(payload["addresses"], [])
        self.assertIsNotNone(payload["dns_error"])

    def test_empty_or_large_dns_answer_is_not_silently_complete(self) -> None:
        self.save_snapshot()
        with patch.object(socket, "getaddrinfo", return_value=[]):
            empty = self.lookup("example.com").json()
        self.assertEqual(empty["addresses"], [])
        self.assertIsNotNone(empty["dns_error"])

        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"203.0.113.{index}", 0))
            for index in range(1, 19)
        ]
        with patch.object(socket, "getaddrinfo", return_value=answers):
            large = self.lookup("example.com").json()
        self.assertTrue(large["dns_truncated"])
        self.assertEqual(len(large["addresses"]), 16)

    def test_rejects_invalid_input_and_missing_snapshot(self) -> None:
        self.save_snapshot()
        for query in ("", "localhost", "2001:db8::1", "1.1.1.1/32", "a..example.com"):
            self.assertEqual(self.lookup(query).status_code, 400)
        self.assertEqual(self.client.post("/api/routes/lookup", json={}).status_code, 400)

        self.snapshot.unlink()
        self.assertEqual(self.lookup("1.1.1.1").status_code, 503)

    def test_corrupt_snapshot_does_not_fall_back_to_current_files(self) -> None:
        self.snapshot.write_text("{", encoding="utf-8")

        response = self.lookup("1.1.1.1")

        self.assertEqual(response.status_code, 503)

    def test_requires_authentication(self) -> None:
        main.app.dependency_overrides.pop(main.require_auth, None)

        self.assertEqual(self.lookup("1.1.1.1").status_code, 401)

    def test_session_requires_csrf_for_lookup_post(self) -> None:
        self.save_snapshot()
        main.app.dependency_overrides.pop(main.require_auth, None)
        with (
            patch.object(main, "WEB_PASSWORD", "unit-test-password"),
            patch.object(main, "WEB_PASSWORD_HASH", ""),
            patch.object(main, "SESSION_COOKIE_SECURE", False),
        ):
            login = self.client.post("/auth/login", json={"password": "unit-test-password"})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(self.lookup("1.1.1.1").status_code, 403)
            response = self.client.post(
                "/api/routes/lookup",
                json={"query": "1.1.1.1"},
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
            )
            self.assertEqual(response.status_code, 200)
        with main.AUTH_LOCK:
            main.AUTH_SESSIONS.clear()


if __name__ == "__main__":
    unittest.main()
