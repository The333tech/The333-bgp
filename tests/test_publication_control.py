import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main


class PublicationControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path_patch = patch.object(main, "PUBLICATION_CONTROL_FILE", Path(self.directory.name) / "publication_control.json")
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_pause_disables_all_live_peers_and_survives_repeated_call(self):
        state = {"192.0.2.1": 1, "2001:db8::10": 1, "192.0.2.3": 2}
        commands = []

        def cli(args, timeout=20):
            commands.append(args)
            if args[-1] == "disable":
                state[args[-2]] = 2
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(main, "run_cmd", side_effect=cli), patch.object(
            main, "gobgp_peer_inventory", side_effect=lambda: dict(state)
        ):
            first = main.pause_publication()
            self.assertEqual(first["mode"], "paused")
            self.assertEqual(main.read_publication_control()["mode"], "paused")
            main.pause_publication()
        self.assertEqual([args[-2] for args in commands if args[-1] == "disable"], ["192.0.2.1", "2001:db8::10"])
        self.assertEqual(first["resume_peers"], ["192.0.2.1", "2001:db8::10"])
        self.assertEqual(main.read_publication_control()["resume_peers"], first["resume_peers"])

    def test_pause_failure_never_reports_confirmed(self):
        with patch.object(main, "gobgp_peer_inventory", return_value={"192.0.2.1": 1}), patch.object(
            main, "run_cmd", return_value=subprocess.CompletedProcess([], 1, "", "offline")
        ):
            with self.assertRaises(RuntimeError):
                main.pause_publication()
        state = main.read_publication_control()
        self.assertEqual(state["mode"], "unconfirmed")
        self.assertFalse(state["confirmed"])

    def test_partial_failure_still_attempts_all_peers_and_retry_preserves_original_snapshot(self):
        state = {"192.0.2.1": 1, "192.0.2.2": 1, "192.0.2.3": 2}
        commands = []
        fail_first = True

        def cli(args, timeout=20):
            target = args[-2]
            commands.append(target)
            if target == "192.0.2.1" and fail_first:
                raise subprocess.TimeoutExpired(args, timeout)
            state[target] = 2
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(main, "gobgp_peer_inventory", side_effect=lambda: dict(state)), patch.object(main, "run_cmd", side_effect=cli):
            with self.assertRaises(RuntimeError):
                main.pause_publication()
            self.assertEqual(commands, ["192.0.2.1", "192.0.2.2"])
            fail_first = False
            self.assertEqual(main.pause_publication()["resume_peers"], ["192.0.2.1", "192.0.2.2"])

    def test_retry_after_unavailable_inventory_records_peers_before_disabling(self):
        address = "192.0.2.1"
        with patch.object(main, "gobgp_peer_inventory", side_effect=[RuntimeError("offline"), {address: 1}, {address: 2}]), patch.object(main, "run_cmd"):
            with self.assertRaises(RuntimeError):
                main.pause_publication()
            main.reassert_publication_pause()
        self.assertEqual(main.read_publication_control()["resume_peers"], [address])

    def test_empty_snapshot_can_resume_without_routes_or_enabling_manual_down_peers(self):
        main.write_publication_control("paused", True, [])
        with patch.object(main, "gobgp_peer_inventory", return_value={"192.0.2.1": 2}), patch.object(main, "read_last_good_snapshot") as snapshot:
            self.assertEqual(main.resume_publication()["mode"], "publishing")
            snapshot.assert_not_called()

    def test_pause_blocks_normal_apply_and_auto_restore(self):
        main.write_publication_control("paused", True)
        with patch.object(main, "gobgp_ready") as ready:
            with self.assertRaises(main.PublicationPaused):
                main.apply_prefixes(["8.8.8.8/32"])
            ready.assert_not_called()
        with patch.object(main, "collect_static_prefixes") as collect:
            with self.assertRaises(main.PublicationPaused):
                main.update_now()
            collect.assert_not_called()
        with patch.object(main, "reassert_publication_pause") as disable, patch.object(main, "apply_last_good") as restore:
            result = main.restore_after_gobgp_recovery()
        self.assertEqual(result["mode"], "publication_paused")
        disable.assert_called_once()
        restore.assert_not_called()

    def test_startup_reasserts_pause_without_restoring_routes(self):
        main.write_publication_control("paused", True)
        with patch.object(main, "reassert_publication_pause") as disable, patch.object(main, "apply_last_good") as restore:
            asyncio.run(main.startup_update_once())
        disable.assert_called_once()
        restore.assert_not_called()

    def test_resume_restores_last_good_before_enabling_peer(self):
        main.write_publication_control("paused", True, ["192.0.2.1", "192.0.2.2"])
        operations = []
        state = {"192.0.2.1": 2, "192.0.2.2": 2, "192.0.2.3": 2}

        def apply(prefixes, **kwargs):
            operations.append("apply")
            self.assertEqual(prefixes, ["8.8.8.8/32"])
            self.assertTrue(kwargs["allow_while_paused"])
            self.assertEqual(main.read_publication_control()["mode"], "resuming")

        def cli(args, timeout=20):
            operations.append(args[-2])
            state[args[-2]] = 1
            return subprocess.CompletedProcess(args, 0, "", "")

        with (
            patch.object(main, "read_last_good_snapshot", return_value=(["8.8.8.8/32"], {})),
            patch.object(main, "_apply_prefixes_locked", side_effect=apply),
            patch.object(main, "run_cmd", side_effect=cli),
            patch.object(main, "gobgp_peer_inventory", side_effect=lambda: dict(state)),
        ):
            result = main.resume_publication()
        self.assertEqual(operations, ["apply", "192.0.2.1", "192.0.2.2"])
        self.assertEqual(state["192.0.2.3"], 2)
        self.assertEqual(result["mode"], "publishing")

    def test_failed_resume_disables_peer_again(self):
        main.write_publication_control("paused", True)
        with (
            patch.object(main, "read_last_good_snapshot", return_value=(["8.8.8.8/32"], {})),
            patch.object(main, "_apply_prefixes_locked", side_effect=RuntimeError("RIB failed")),
            patch.object(main, "ensure_gobgp_peers_disabled") as disable,
            patch.object(main, "gobgp_peer_inventory", return_value={main.PEER_ADDRESS: 2}),
        ):
            with self.assertRaisesRegex(RuntimeError, "RIB failed"):
                main.resume_publication()
        disable.assert_called_once()
        self.assertEqual(main.read_publication_control()["mode"], "paused")

    def test_unconfirmed_state_requires_a_new_pause_before_resume(self):
        main.write_publication_control("unconfirmed", False)
        with patch.object(main, "read_last_good_snapshot") as snapshot:
            with self.assertRaisesRegex(RuntimeError, "не подтверждена"):
                main.resume_publication()
            snapshot.assert_not_called()

    def test_restoring_old_backup_cannot_clear_active_pause(self):
        main.write_publication_control("paused", True)
        with (
            patch.object(main, "validate_system_backup_zip_name", return_value=(Path("old.zip"), {}, [])),
            patch.object(main, "extract_system_backup_to_stage"),
            patch.object(main, "create_system_backup", return_value={"backup_name": "safety.zip"}),
            patch.object(main, "clear_restore_root") as clear,
            patch.object(main, "copy_staged_root", return_value=0) as copy,
            patch.object(main, "run_data_migrations"),
            patch.object(main, "ensure_sources_file"),
            patch.object(main, "ensure_community_profiles_file"),
            patch.object(main, "ensure_service_state_file"),
            patch.object(main, "reassert_publication_pause") as disable,
            patch.object(main, "update_now") as update,
            patch.object(main, "SYSTEM_RESTORE_STAGING_DIR", Path(self.directory.name) / "staging"),
        ):
            result = main._restore_system_backup_locked("old.zip")
        self.assertIn("publication_control.json", clear.call_args_list[0].kwargs["preserve_names"])
        self.assertIn("publication_control.json", copy.call_args_list[0].kwargs["skip_root_names"])
        disable.assert_called_once()
        update.assert_not_called()
        self.assertFalse(result["apply_routes"])

    def test_invalid_state_does_not_enable_publication(self):
        main.PUBLICATION_CONTROL_FILE.write_text("{invalid", encoding="utf-8")
        self.assertTrue(main.publication_is_paused())
        with patch.object(main, "run_cmd") as cli:
            with self.assertRaises(main.PublicationPaused):
                main.ensure_gobgp_neighbor_enabled()
            cli.assert_not_called()

    def test_inventory_distinguishes_empty_from_unavailable_and_rejects_invalid_address(self):
        def result(address, admin):
            return subprocess.CompletedProcess([], 0, json.dumps([{"state": {"neighbor_address": address, "admin_state": admin}}]), "")

        with patch.object(main, "run_cmd", return_value=result(main.PEER_ADDRESS, 2)):
            self.assertEqual(main.gobgp_peer_inventory(), {main.PEER_ADDRESS: 2})
        with patch.object(main, "run_cmd", return_value=result("invalid", 2)):
            with self.assertRaises(ValueError):
                main.gobgp_peer_inventory()
        with patch.object(main, "run_cmd", return_value=subprocess.CompletedProcess([], 0, "[]", "")):
            self.assertEqual(main.pause_publication()["resume_peers"], [])
            self.assertTrue(main.publication_status()["confirmed"])
        with patch.object(main, "run_cmd", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertFalse(main.publication_status()["confirmed"])

    def test_readiness_identifies_confirmed_and_unconfirmed_pause(self):
        main.write_publication_control("paused", True)
        status_file = Path(self.directory.name) / "status.json"
        status_file.write_text('{"ok": true}', encoding="utf-8")
        with (
            patch.object(main, "STATUS_FILE", status_file),
            patch.object(main, "read_lines", return_value=["8.8.8.8/32"]),
            patch.object(main, "gobgp_ready", return_value=True),
            patch.object(main, "gobgp_rib_count", return_value=0),
            patch.object(main, "gobgp_peer_inventory", return_value={"192.0.2.1": 2, "192.0.2.2": 2}),
        ):
            payload, status = main.build_readiness_payload()
        self.assertEqual(status, 200)
        self.assertEqual(payload["publication"]["mode"], "paused")
        self.assertTrue(payload["publication"]["confirmed"])
        with (
            patch.object(main, "STATUS_FILE", status_file),
            patch.object(main, "read_lines", return_value=["8.8.8.8/32"]),
            patch.object(main, "gobgp_ready", return_value=True),
            patch.object(main, "gobgp_rib_count", return_value=0),
            patch.object(main, "gobgp_peer_inventory", return_value={"192.0.2.1": 2, "192.0.2.2": 1}),
        ):
            payload, status = main.build_readiness_payload()
        self.assertEqual(status, 503)
        self.assertFalse(payload["publication"]["confirmed"])

    def test_api_requires_login_and_csrf_and_reports_actual_status(self):
        client = TestClient(main.app)
        try:
            with patch.object(main, "gobgp_peer_inventory", return_value={main.PEER_ADDRESS: 2}):
                self.assertEqual(client.get("/api/routes/publication").status_code, 401)
                login = client.post("/auth/login", json={"password": "unit-test-password"})
                self.assertEqual(login.status_code, 200)
                self.assertEqual(client.post("/api/routes/publication/pause").status_code, 403)
                with patch.object(main, "pause_publication", side_effect=lambda: main.write_publication_control("paused", True)):
                    response = client.post("/api/routes/publication/pause", headers={"X-CSRF-Token": login.json()["csrf_token"]})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["confirmed"])
                self.assertEqual(client.get("/api/routes/publication").json()["mode"], "paused")
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
