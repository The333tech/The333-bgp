import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "the333_host_updater",
    ROOT / "scripts" / "host-updater.py",
)
assert SPEC and SPEC.loader
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


class HostUpdaterApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp_dir.name) / "updater.sock"
        self.server = updater.UnixHTTPServer(str(self.socket_path), updater.UpdaterHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(uds=str(self.socket_path)),
            base_url="http://localhost",
            timeout=2,
            trust_env=False,
        )
        self.token_patch = patch.object(updater, "UPDATER_TOKEN", "unit-test-token")
        self.token_patch.start()

    def tearDown(self) -> None:
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.token_patch.stop()
        self.temp_dir.cleanup()

    def test_health_is_available_over_unix_socket(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_update_requires_token(self) -> None:
        response = self.client.post("/api/update", json={"channel": "stable"})

        self.assertEqual(response.status_code, 403)

    def test_unsupported_fields_and_version_injection_are_rejected(self) -> None:
        headers = {"x-the333-updater-token": "unit-test-token"}

        with patch.object(updater, "run_update") as run_update:
            extra_field = self.client.post(
                "/api/update",
                headers=headers,
                json={"channel": "stable", "manifest": "https://attacker.invalid"},
            )
            injected_version = self.client.post(
                "/api/update",
                headers=headers,
                json={"channel": "stable", "version": "0.5;id"},
            )

        self.assertEqual(extra_field.status_code, 400)
        self.assertEqual(injected_version.status_code, 400)
        run_update.assert_not_called()

    def test_valid_request_uses_fixed_arguments(self) -> None:
        headers = {"x-the333-updater-token": "unit-test-token"}

        with patch.object(
            updater,
            "run_update",
            return_value={"ok": True, "channel": "beta", "version": "0.5-beta.1"},
        ) as run_update:
            response = self.client.post(
                "/api/update",
                headers=headers,
                json={"channel": "beta", "version": "0.5-beta.1"},
            )

        self.assertEqual(response.status_code, 200)
        run_update.assert_called_once_with("beta", "0.5-beta.1")


class HostUpdaterHelpersTests(unittest.TestCase):
    def test_sensitive_environment_values_are_redacted(self) -> None:
        with patch.dict(
            updater.os.environ,
            {"HOST_UPDATER_TOKEN": "secret-token", "WEB_PASSWORD": "secret-password"},
            clear=False,
        ):
            value = updater.redact_output("secret-token secret-password harmless")

        self.assertNotIn("secret-token", value)
        self.assertNotIn("secret-password", value)
        self.assertIn("harmless", value)


if __name__ == "__main__":
    unittest.main()
