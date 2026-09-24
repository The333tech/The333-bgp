import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
        response = self.client.post("/api/update", json={"channel": "stable", "request_id": "a" * 32})

        self.assertEqual(response.status_code, 403)

    def test_runtime_status_requires_token_and_returns_allowlisted_payload(self) -> None:
        self.assertEqual(self.client.get("/api/runtime").status_code, 403)

        expected = {
            "ok": True,
            "containers": [{"key": "backend", "name": "the333-bgp-backend"}],
            "time": "2026-07-13T00:00:00+00:00",
        }
        with patch.object(updater, "container_runtime_status", return_value=expected):
            response = self.client.get(
                "/api/runtime",
                headers={"x-the333-updater-token": "unit-test-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)

    def test_unsupported_fields_and_version_injection_are_rejected(self) -> None:
        headers = {"x-the333-updater-token": "unit-test-token"}

        with patch.object(updater, "run_update") as run_update:
            extra_field = self.client.post(
                "/api/update",
                headers=headers,
                json={"channel": "stable", "request_id": "a" * 32, "manifest": "https://attacker.invalid"},
            )
            injected_version = self.client.post(
                "/api/update",
                headers=headers,
                json={"channel": "stable", "version": "0.5;id", "request_id": "a" * 32},
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
                json={"channel": "beta", "version": "0.5-beta.1", "request_id": "a" * 32},
            )

        self.assertEqual(response.status_code, 200)
        run_update.assert_called_once_with("beta", "0.5-beta.1", "a" * 32)

    def test_invalid_argument_types_and_options_never_start_an_update(self) -> None:
        headers = {"x-the333-updater-token": "unit-test-token"}
        invalid_payloads = [
            {"channel": value} for value in ([], {}, True, 0, "--help", "stable;id")
        ] + [
            {"version": value} for value in ([], {}, True, 82, "--help", "v1\n--help", "v" * 65)
        ]
        with patch.object(updater, "run_update") as run_update:
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    response = self.client.post(
                        "/api/update", headers=headers,
                        json={"request_id": "a" * 32, **payload},
                    )
                    self.assertEqual(response.status_code, 400)
            run_update.assert_not_called()

    def test_omitted_or_null_version_keeps_latest_version_selection(self) -> None:
        headers = {"x-the333-updater-token": "unit-test-token"}
        for payload in ({}, {"channel": None, "version": None}):
            with self.subTest(payload=payload), patch.object(
                updater, "run_update", return_value={"ok": False, "busy": True},
            ) as run_update:
                response = self.client.post(
                    "/api/update", headers=headers,
                    json={"request_id": "a" * 32, **payload},
                )
                self.assertEqual(response.status_code, 409)
                run_update.assert_called_once_with("stable", "", "a" * 32)

    def test_successful_update_restarts_server_even_if_response_headers_disconnect(self) -> None:
        with (
            patch.object(updater, "run_update", return_value={"ok": True}) as run_update,
            patch.object(updater.UpdaterHandler, "end_headers", side_effect=BrokenPipeError),
        ):
            with self.assertRaises(httpx.RemoteProtocolError):
                self.client.post(
                    "/api/update",
                    headers={"x-the333-updater-token": "unit-test-token"},
                    json={"channel": "beta", "version": "0.84b", "request_id": "a" * 32},
                )
            self.thread.join(timeout=2)
            self.assertTrue(self.server.restart_requested)
            self.assertFalse(self.thread.is_alive())
            run_update.assert_called_once()

    def test_failed_update_keeps_server_available(self) -> None:
        with patch.object(updater, "run_update", return_value={"ok": False}):
            response = self.client.post(
                "/api/update",
                headers={"x-the333-updater-token": "unit-test-token"},
                json={"request_id": "a" * 32},
            )
        self.assertEqual(response.status_code, 500)
        self.assertFalse(self.server.restart_requested)
        self.assertEqual(self.client.get("/health").status_code, 200)


class HostUpdaterHelpersTests(unittest.TestCase):
    def test_response_disconnect_is_tolerated_in_headers_body_and_flush(self) -> None:
        for stage in ("headers", "body", "flush"):
            for error in (BrokenPipeError, ConnectionResetError):
                with self.subTest(stage=stage, error=error):
                    handler = object.__new__(updater.UpdaterHandler)
                    handler.send_response = Mock()
                    handler.send_header = Mock()
                    handler.end_headers = Mock()
                    handler.wfile = Mock()
                    operation = {
                        "headers": handler.end_headers,
                        "body": handler.wfile.write,
                        "flush": handler.wfile.flush,
                    }[stage]
                    operation.side_effect = error
                    handler.send_json(200, {"ok": True})
                    operation.assert_called_once()

    def test_update_arguments_allow_only_supported_channels_and_version_tokens(self) -> None:
        for channel in ("stable", "beta"):
            for version in ("", "0.84b", "0.84.1-beta+build_1", "v" * 64):
                with self.subTest(channel=channel, version=version):
                    expected = ["update", "--non-interactive", "--channel", channel]
                    if version:
                        expected += ["--version", version]
                    self.assertEqual(updater.update_arguments(channel, version), expected)

    def test_invalid_update_input_has_no_side_effects(self) -> None:
        invalid_inputs = [
            (value, "0.84b", "a" * 32)
            for value in (None, [], {}, True, "", "preview", "--help", "beta;id")
        ] + [
            ("beta", value, "a" * 32)
            for value in (None, [], {}, 84, "--help", "../v1", "v1;id", "$(id)", "v1\x00", "v1\n", "v1 --help", "v" * 65)
        ] + [("beta", "0.84b", "../invalid")]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with (
                patch.object(updater, "PROJECT_DIR", root),
                patch.object(updater, "LOCK_PATH", root / "run" / "update.lock"),
                patch.object(updater, "write_result") as write_result,
                patch.object(updater.subprocess, "run") as run,
            ):
                for channel, version, request_id in invalid_inputs:
                    with self.subTest(channel=channel, version=version, request_id=request_id):
                        with self.assertRaises(ValueError):
                            updater.run_update(channel, version, request_id)
                run.assert_not_called()
                write_result.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def test_missing_or_escaped_script_does_not_start_or_record_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            scripts = root / "scripts"
            scripts.mkdir()
            outside = root / "outside.sh"
            outside.touch()
            script = scripts / "the333bgp.sh"
            with (
                patch.object(updater, "PROJECT_DIR", root),
                patch.object(updater, "write_result") as write_result,
                patch.object(updater.subprocess, "run") as run,
            ):
                for escaped in (False, True):
                    with self.subTest(escaped=escaped):
                        if escaped:
                            script.symlink_to(outside)
                        with self.assertRaisesRegex(RuntimeError, "update script is unavailable"):
                            updater.run_update("beta", "0.84b", "a" * 32)
                run.assert_not_called()
                write_result.assert_not_called()

    def test_update_delegates_validated_arguments_to_transaction_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            script = root / "scripts" / "the333bgp.sh"
            script.parent.mkdir()
            script.touch()
            with (
                patch.object(updater, "PROJECT_DIR", root),
                patch.object(updater, "LOCK_PATH", root / "run" / "update.lock"),
                patch.object(updater, "write_result") as write_result,
                patch.object(updater, "run_command", return_value={
                    "ok": True, "status": "succeeded", "stdout_tail": "done", "stderr_tail": "",
                }) as run,
            ):
                result = updater.run_update("beta", "0.84b", "a" * 32)
                self.assertTrue(result["ok"])
                self.assertEqual(run.call_args.args[0], root)
                self.assertEqual(run.call_args.args[1], [
                    "update", "--non-interactive", "--channel", "beta", "--version", "0.84b",
                ])
                self.assertEqual(run.call_args.args[2:5], ("a" * 32, "beta", "0.84b"))
                self.assertEqual(write_result.call_args_list[0].args[1]["status"], "running")
                self.assertEqual(write_result.call_args_list[1].args[1]["status"], "succeeded")

    def test_result_path_accepts_only_a_direct_child_of_result_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result_dir = Path(temporary_directory).resolve()
            request_id = "a" * 32

            with patch.object(updater, "RESULT_DIR", result_dir):
                self.assertEqual(
                    updater.result_path(request_id),
                    result_dir / f"{request_id}.json",
                )
                for invalid in (
                    "../" + request_id,
                    request_id + "/child",
                    "/tmp/" + request_id,
                    "A" * 32,
                    "a" * 31,
                    "a" * 33,
                ):
                    with self.subTest(request_id=invalid):
                        with self.assertRaises(ValueError):
                            updater.result_path(invalid)

    @unittest.skipUnless(hasattr(os, "O_DIRECTORY"), "requires POSIX directory operations")
    def test_write_result_replaces_symlink_without_touching_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result_dir = root / "results"
            result_dir.mkdir()
            outside = root / "outside.json"
            outside.write_text('{"protected": true}\n', encoding="utf-8")
            request_id = "b" * 32
            target = result_dir / f"{request_id}.json"
            target.symlink_to(outside)

            with (
                patch.object(updater, "RESULT_DIR", result_dir),
                patch.object(updater.os, "chown"),
                patch.object(updater, "prune_result_files"),
            ):
                updater.write_result(request_id, {"ok": True})

            self.assertFalse(target.is_symlink())
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"ok": True})
            self.assertEqual(outside.read_text(encoding="utf-8"), '{"protected": true}\n')

    def test_runtime_inspection_uses_only_fixed_container_names(self) -> None:
        def completed(command, **_kwargs):
            name = command[-1]
            payload = [{
                "State": {
                    "Running": True,
                    "Status": "running",
                    "StartedAt": "2026-07-13T00:00:00Z",
                    "Health": {"Status": "healthy"},
                }
            }]
            return updater.subprocess.CompletedProcess(command, 0, stdout=updater.json.dumps(payload), stderr="")

        with (
            patch.object(updater.subprocess, "run", side_effect=completed) as run,
            patch.object(updater, "now_iso", return_value="2026-07-13T00:01:00+00:00"),
        ):
            result = updater.container_runtime_status()

        self.assertTrue(result["ok"])
        self.assertEqual([item["name"] for item in result["containers"]], [item[2] for item in updater.RUNTIME_CONTAINERS])
        self.assertTrue(all(call.args[0][:2] == ["docker", "inspect"] for call in run.call_args_list))

    def test_child_environment_drops_stale_project_configuration(self) -> None:
        with patch.dict(
            updater.os.environ,
            {
                "PATH": "/test/bin",
                "LANG": "C.UTF-8",
                "PRODUCT_UPDATE_MANIFEST_URL": "https://stale.example/manifest.json",
                "CURL_CA_BUNDLE": "/tmp/stale-ca.crt",
                "HOST_UPDATER_TOKEN": "stale-token",
                "WEB_PASSWORD": "stale-password",
            },
            clear=True,
        ):
            environment = updater.public_environment()

        self.assertEqual(environment["PATH"], "/test/bin")
        self.assertEqual(environment["LANG"], "C.UTF-8")
        self.assertEqual(environment["THE333_PROJECT_DIR"], str(updater.PROJECT_DIR))
        self.assertEqual(environment["THE333_HOST_UPDATER_ACTIVE"], "true")
        self.assertNotIn("PRODUCT_UPDATE_MANIFEST_URL", environment)
        self.assertNotIn("CURL_CA_BUNDLE", environment)
        self.assertNotIn("HOST_UPDATER_TOKEN", environment)
        self.assertNotIn("WEB_PASSWORD", environment)

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
