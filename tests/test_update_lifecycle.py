import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.maintenance import MaintenanceBusy, mutation_lease, read_state, write_state
from app.session_store import SessionStore, load_or_create_salt


class PersistentSessionTests(unittest.TestCase):
    def test_credential_salt_is_unique_per_installation_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first" / "credential.salt"
            second = Path(directory) / "second" / "credential.salt"
            salt = load_or_create_salt(first)
            self.assertEqual(len(salt), 32)
            self.assertEqual(load_or_create_salt(first), salt)
            self.assertNotEqual(load_or_create_salt(second), salt)
            if os.name == "posix":
                self.assertEqual(first.stat().st_mode & 0o777, 0o600)

    def test_restart_expiry_logout_and_changed_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.sqlite3"
            session = {"created_at": time.time(), "expires_at": time.time() + 300, "csrf_token": "csrf"}
            SessionStore(path, "credential-a").put("cookie-hash", session, 2)
            self.assertEqual(SessionStore(path, "credential-a").get("cookie-hash"), session)
            self.assertIsNone(SessionStore(path, "credential-b").get("cookie-hash"))
            SessionStore(path, "credential-a").delete("cookie-hash")
            self.assertIsNone(SessionStore(path, "credential-a").get("cookie-hash"))
            SessionStore(path, "credential-a").put("expired", {**session, "expires_at": 1}, 2)
            self.assertIsNone(SessionStore(path, "credential-a").get("expired"))

    def test_session_limit_removes_oldest(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.sqlite3", "credential")
            for index in range(3):
                store.put(str(index), {"created_at": index, "expires_at": time.time() + 300}, 2)
            self.assertIsNone(store.get("0"))
            self.assertIsNotNone(store.get("2"))


@unittest.skipUnless(os.name == "posix", "requires Linux file locks and process groups")
class UpdateCoordinationTests(unittest.TestCase):
    def setUp(self):
        from app.update_runner import prepare_runtime
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "VERSION").write_text("0.84.1b")
        self.directory = prepare_runtime(self.root, os.getuid(), os.getgid())

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_gate_and_corrupt_state_fail_closed(self):
        for directory in (self.root / "missing", self.directory):
            if directory == self.directory:
                (directory / "operation.json").write_text("{broken")
            with self.assertRaises(MaintenanceBusy):
                with mutation_lease(directory):
                    self.fail("writes must be blocked")

    def test_runtime_owner_comes_from_installed_env_not_sudo_identity(self):
        from app.update_runner import runtime_identity
        (self.root / ".env").write_text("PUID=1234\nPGID=2345\n")
        self.assertEqual(runtime_identity(self.root), (1234, 2345))
        (self.root / ".env").write_text("PUID=bad\nPGID=\n")
        owner = (self.root / ".env").stat()
        self.assertEqual(runtime_identity(self.root), (owner.st_uid, owner.st_gid))

    def test_host_gate_is_group_writable_for_project_cli_but_backend_mount_is_read_only(self):
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o770)
        self.assertEqual((self.directory / "writes.lock").stat().st_mode & 0o777, 0o660)
        self.assertEqual((self.directory / "runner.lock").stat().st_mode & 0o777, 0o660)

    def test_guard_blocks_on_marker_and_exclusive_lock_but_releases_on_failure(self):
        import fcntl
        with mutation_lease(self.directory):
            pass
        write_state(self.directory, {"status": "running"})
        with self.assertRaises(MaintenanceBusy):
            with mutation_lease(self.directory):
                pass
        write_state(self.directory, {"status": "failed"})
        with (self.directory / "writes.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(MaintenanceBusy):
                with mutation_lease(self.directory):
                    pass
        with mutation_lease(self.directory):
            pass

    def test_lost_owner_is_not_automatically_unlocked(self):
        from app.update_runner import run_command
        write_state(self.directory, {"status": "running", "request_id": "a" * 32})
        result = run_command(self.root, [], "b" * 32, "beta", "0.85b", {}, 1)
        self.assertTrue(result["busy"])
        self.assertEqual(read_state(self.directory)["request_id"], "a" * 32)

    def test_runner_outcomes_and_fixed_executable(self):
        from app.update_runner import run_command
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "the333bgp.sh").touch()
        for returncode, stage, mutated, expected in (
            (0, "readiness", True, "succeeded"), (1, "download", False, "failed"),
            (1, "rolled_back", True, "rolled_back"), (1, "restarting", True, "recovery_required"),
        ):
            with self.subTest(expected=expected):
                write_state(self.directory, {"status": "failed"})
                def finish(*args, **kwargs):
                    state = read_state(self.directory)
                    write_state(self.directory, {**state, "stage": stage, "mutated": mutated})
                    return returncode
                with patch("app.update_runner.subprocess.Popen") as popen, \
                     patch("app.update_runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
                    popen.return_value.wait.side_effect = finish
                    result = run_command(self.root, ["update", "--version", "0.85b"], "a" * 32,
                                         "beta", "0.85b", {}, 30)
                    self.assertEqual(popen.call_args.args[0][0], "/bin/bash")
                    self.assertTrue(popen.call_args.kwargs["start_new_session"])
                self.assertEqual(result["status"], expected)
                self.assertEqual(read_state(self.directory)["status"], expected)

    @unittest.skipUnless(Path("/bin/bash").is_file(), "requires Bash on Linux host")
    def test_runner_can_execute_and_retry_after_safe_preflight_failure(self):
        from app.update_runner import run_command
        scripts = self.root / "scripts"
        scripts.mkdir()
        controller = scripts / "the333bgp.sh"
        controller.write_text("#!/bin/bash\necho preflight-failed\nexit 7\n")
        failed = run_command(self.root, ["update"], "1" * 32, "beta", "0.85b",
                             dict(os.environ), 10)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("preflight-failed", failed["stdout_tail"])
        controller.write_text("#!/bin/bash\necho ready\n")
        succeeded = run_command(self.root, ["update"], "2" * 32, "beta", "0.85b",
                                dict(os.environ), 10)
        self.assertEqual(succeeded["status"], "succeeded")
        self.assertIn("ready", succeeded["stdout_tail"])

    @unittest.skipUnless(Path("/bin/bash").is_file(), "requires Bash on Linux host")
    def test_failed_backup_keeps_gate_closed_when_runtime_is_not_ready(self):
        from app.update_runner import run_command
        scripts = self.root / "scripts"
        scripts.mkdir()
        controller = scripts / "the333bgp.sh"
        controller.write_text(
            "#!/bin/bash\n"
            "if [ \"$1\" = status ]; then exit 1; fi\n"
            "python3 - <<'PY'\n"
            "import json, os\n"
            "from pathlib import Path\n"
            "path = Path(os.environ['THE333_UPDATE_OPERATION_FILE'])\n"
            "state = json.loads(path.read_text())\n"
            "state['stage'] = 'backup'\n"
            "path.write_text(json.dumps(state))\n"
            "PY\n"
            "exit 7\n"
        )
        result = run_command(self.root, ["update"], "3" * 32, "beta", "0.85b",
                             dict(os.environ), 10)
        self.assertEqual(result["status"], "recovery_required")
        with self.assertRaises(MaintenanceBusy):
            with mutation_lease(self.directory):
                pass

    def test_api_blocks_direct_writes_and_reads_status_without_ready(self):
        import app.main as main
        from fastapi.testclient import TestClient
        state = {"request_id": "a" * 32, "status": "running", "stage": "backup", "version": "0.85b"}
        write_state(self.directory, state)
        main.app.dependency_overrides[main.require_auth] = lambda: "test"
        try:
            with patch.object(main, "MAINTENANCE_DIR", self.directory), patch.object(main, "read_jobs_state", return_value={"jobs": []}), \
                 patch.object(main, "build_readiness_payload", side_effect=AssertionError("must not poll readiness")):
                client = TestClient(main.app)
                self.assertEqual(client.put("/api/runtime-settings", json={}).status_code, 409)
                response = client.get("/api/product/update/status")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["blocked"])
                self.assertEqual(response.json()["operation"]["stage"], "backup")
                client.close()
        finally:
            main.app.dependency_overrides.clear()

    def test_recovery_marker_cannot_be_hidden_by_newer_job(self):
        import app.main as main
        from fastapi.testclient import TestClient
        write_state(self.directory, {"status": "recovery_required", "stage": "restarting"})
        job = {"id": "b" * 32, "kind": "product_update", "status": "failed",
               "created_at": "2099-01-01T00:00:00+00:00", "payload": {"version": "0.85b"}}
        main.app.dependency_overrides[main.require_auth] = lambda: "test"
        try:
            with patch.object(main, "MAINTENANCE_DIR", self.directory), \
                 patch.object(main, "read_jobs_state", return_value={"jobs": [job]}):
                client = TestClient(main.app)
                payload = client.get("/api/product/update/status").json()
                self.assertTrue(payload["blocked"])
                self.assertEqual(payload["operation"]["status"], "recovery_required")
                client.close()
        finally:
            main.app.dependency_overrides.clear()

    def test_completed_update_reports_readiness_without_server_error(self):
        import app.main as main
        from fastapi.testclient import TestClient
        main.app.dependency_overrides[main.require_auth] = lambda: "test"
        try:
            with patch.object(main, "MAINTENANCE_DIR", self.directory), \
                 patch.object(main, "read_jobs_state", return_value={"jobs": []}), \
                 patch.object(main, "read_product_version", return_value="0.90b"):
                write_state(self.directory, {"request_id": "a" * 32, "status": "succeeded",
                                             "stage": "readiness", "version": "0.90b"})
                client = TestClient(main.app)
                try:
                    for ready, status_code in ((False, 503), (True, 200)):
                        with self.subTest(ready=ready), patch.object(
                            main, "build_readiness_payload", return_value=({"ready": ready}, status_code)
                        ) as readiness:
                            response = client.get("/api/product/update/status")
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(response.json()["ready"], ready)
                            self.assertFalse(response.json()["blocked"])
                            readiness.assert_called_once_with()
                finally:
                    client.close()
        finally:
            main.app.dependency_overrides.clear()

    def test_queued_update_blocks_other_writes_before_host_runner_starts(self):
        import app.main as main
        from fastapi.testclient import TestClient
        job = {"id": "b" * 32, "kind": "product_update", "status": "queued",
               "created_at": main.now_iso(), "payload": {"version": "0.85b"}}
        main.app.dependency_overrides[main.require_auth] = lambda: "test"
        try:
            with patch.object(main, "MAINTENANCE_DIR", self.directory), \
                 patch.object(main, "read_jobs_state", return_value={"jobs": [job]}), \
                 patch.object(main, "write_jobs_state") as write:
                client = TestClient(main.app)
                self.assertEqual(client.put("/api/runtime-settings", json={}).status_code, 409)
                payload = client.get("/api/product/update/status").json()
                self.assertTrue(payload["blocked"])
                self.assertEqual(payload["operation"]["status"], "queued")
                write.assert_not_called()
                client.close()
        finally:
            main.app.dependency_overrides.clear()

    def test_scheduler_does_not_fetch_or_apply_during_maintenance(self):
        import app.main as main
        write_state(self.directory, {"status": "running"})
        with patch.object(main, "MAINTENANCE_DIR", self.directory), \
             patch.object(main, "refresh_service_candidates_if_due") as fetch, \
             patch.object(main, "update_now") as apply:
            with self.assertRaises(MaintenanceBusy):
                main.scheduled_route_update()
            fetch.assert_not_called()
            apply.assert_not_called()

    def test_timeout_terminates_descendants_and_keeps_writes_blocked(self):
        import subprocess
        import sys
        from app.update_runner import run_command
        escaped = self.root / "descendant-finished"
        child = f"import time; from pathlib import Path; time.sleep(2); Path({str(escaped)!r}).touch()"
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
        real_popen = subprocess.Popen
        with patch("app.update_runner.subprocess.Popen",
                   side_effect=lambda command, **options: real_popen([sys.executable, "-c", parent], **options)):
            result = run_command(self.root, ["update"], "c" * 32, "beta", "0.85b", dict(os.environ), 1)
        self.assertTrue(result["timeout"])
        self.assertEqual(result["status"], "recovery_required")
        time.sleep(2)
        self.assertFalse(escaped.exists())
        with self.assertRaises(MaintenanceBusy):
            with mutation_lease(self.directory):
                pass

    def test_manual_recovery_requires_verified_runtime_and_matching_version(self):
        import subprocess
        from app.update_runner import recover_operation
        state = {"status": "recovery_required", "stage": "restarting",
                 "request_id": "d" * 32, "previous_version": "0.84.1b", "version": "0.85b",
                 "mutated": True, "history": []}
        write_state(self.directory, state)
        (self.root / "VERSION").write_text("0.85b")
        with patch("app.update_runner.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(RuntimeError, "status check failed"):
                recover_operation(self.root, dict(os.environ))
        self.assertEqual(read_state(self.directory)["status"], "recovery_required")
        with patch("app.update_runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            result = recover_operation(self.root, dict(os.environ))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["stage"], "verified_recovery")

    def test_manual_recovery_cannot_override_active_runner_or_unknown_version(self):
        import fcntl
        from app.update_runner import recover_operation
        write_state(self.directory, {"status": "running", "request_id": "e" * 32,
                                     "previous_version": "0.84.1b", "version": "0.85b"})
        with (self.directory / "runner.lock").open("r+") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "still active"):
                recover_operation(self.root, dict(os.environ))
        (self.root / "VERSION").write_text("unrecognized")
        with self.assertRaisesRegex(RuntimeError, "differs"):
            recover_operation(self.root, dict(os.environ))

    def test_manual_recovery_of_interrupted_preflight_without_selected_version(self):
        import subprocess
        from app.update_runner import recover_operation
        write_state(self.directory, {"status": "running", "request_id": "f" * 32,
                                     "previous_version": "0.84.1b", "version": "",
                                     "mutated": False, "history": []})
        with patch("app.update_runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            result = recover_operation(self.root, dict(os.environ))
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
