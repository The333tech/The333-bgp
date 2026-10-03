import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class RouteProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.snapshot = Path(self.temp_dir.name) / "snapshot.json"
        main.write_json_atomic(self.snapshot, {
            "updated_at": "2026-10-03T00:00:00Z",
            "prefixes": ["1.1.1.0/24", "10.0.0.0/8"],
            "route_communities": {},
        })
        for name, value in (
            ("LAST_GOOD_SNAPSHOT_FILE", self.snapshot),
            ("PUBLICATION_CONTROL_FILE", Path(self.temp_dir.name) / "publication.json"),
            ("ROUTE_PROBE_LAST_AT", 0.0),
        ):
            mocked = patch.object(main, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.rib_patch = patch.object(main, "gobgp_current_prefixes", return_value={"1.1.1.0/24"})
        self.mock_rib = self.rib_patch.start()
        self.addCleanup(self.rib_patch.stop)
        self.tcp_patch = patch.object(main, "probe_route_tcp_443", return_value=True)
        self.mock_tcp = self.tcp_patch.start()
        self.addCleanup(self.tcp_patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        main.app.dependency_overrides[main.require_auth] = lambda: "test"
        self.addCleanup(main.app.dependency_overrides.pop, main.require_auth, None)

    def probe(self, address: str):
        return self.client.post("/api/routes/probe", json={"address": address})

    def test_connectivity_is_not_called_vpn_health(self) -> None:
        response = self.probe("1.1.1.42")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.json()["tcp_443_connected"])
        self.assertEqual(response.json()["checked_from"], "backend_container")
        self.assertNotIn("vpn_ok", response.json())
        self.mock_tcp.assert_called_once_with("1.1.1.42")

    def test_connection_failure_is_reported_without_network_details(self) -> None:
        self.mock_tcp.return_value = False

        response = self.probe("1.1.1.42")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["tcp_443_connected"])

    def test_socket_probe_uses_only_tcp_443_and_closes_connection(self) -> None:
        self.tcp_patch.stop()
        with patch.object(socket, "create_connection") as connect:
            self.assertTrue(main.probe_route_tcp_443("1.1.1.42"))
        connect.assert_called_once_with(("1.1.1.42", 443), timeout=3)
        connect.return_value.__enter__.assert_called_once()
        connect.return_value.__exit__.assert_called_once()

        with patch.object(socket, "create_connection", side_effect=TimeoutError):
            self.assertFalse(main.probe_route_tcp_443("1.1.1.42"))

    def test_rejects_private_invalid_and_nonmatching_addresses(self) -> None:
        for address in ("10.0.0.1", "127.0.0.1", "169.254.1.1", "203.0.113.1", "::1", "bad"):
            self.assertEqual(self.probe(address).status_code, 400)
        self.assertEqual(self.probe("8.8.8.8").status_code, 409)
        self.assertEqual(self.client.post("/api/routes/probe", json={}).status_code, 400)
        self.mock_tcp.assert_not_called()

    def test_paused_or_missing_rib_never_connects(self) -> None:
        main.write_publication_control("paused", True)
        self.assertEqual(self.probe("1.1.1.42").status_code, 409)
        self.mock_rib.assert_not_called()
        main.write_publication_control("publishing", True)
        self.mock_rib.return_value = set()
        self.assertEqual(self.probe("1.1.1.42").status_code, 409)
        self.mock_tcp.assert_not_called()

    def test_rib_error_does_not_leak_cli_output(self) -> None:
        self.mock_rib.side_effect = RuntimeError("secret CLI detail")

        response = self.probe("1.1.1.42")

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("secret CLI detail", response.text)
        self.mock_tcp.assert_not_called()

    def test_repeat_probe_is_rate_limited(self) -> None:
        self.assertEqual(self.probe("1.1.1.42").status_code, 200)
        self.assertEqual(self.probe("1.1.1.42").status_code, 429)
        self.mock_tcp.assert_called_once()

    def test_requires_auth_and_session_csrf(self) -> None:
        main.app.dependency_overrides.pop(main.require_auth, None)
        self.assertEqual(self.probe("1.1.1.42").status_code, 401)
        with (
            patch.object(main, "WEB_PASSWORD", "unit-test-password"),
            patch.object(main, "WEB_PASSWORD_HASH", ""),
            patch.object(main, "SESSION_COOKIE_SECURE", False),
        ):
            login = self.client.post("/auth/login", json={"password": "unit-test-password"})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(self.probe("1.1.1.42").status_code, 403)
            response = self.client.post(
                "/api/routes/probe", json={"address": "1.1.1.42"},
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
            )
            self.assertEqual(response.status_code, 200)
        with main.AUTH_LOCK:
            main.AUTH_SESSIONS.clear()


if __name__ == "__main__":
    unittest.main()
