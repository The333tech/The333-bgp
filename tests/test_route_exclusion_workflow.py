import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class RouteExclusionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, filename in (
            ("ROUTE_EXCLUSIONS_FILE", "route_exclusions.json"),
            ("LAST_GOOD_SNAPSHOT_FILE", "last_good_route_snapshot.json"),
            ("LAST_GOOD_FILE", "last_good_prefixes.txt"),
            ("ADVERTISED_FILE", "advertised_prefixes.txt"),
        ):
            mocked = patch.object(main, name, root / filename)
            mocked.start()
            self.addCleanup(mocked.stop)
        main.write_last_good_snapshot(["1.1.1.0/24", "8.8.8.0/24"], {})
        main.write_prefixes_file(main.ADVERTISED_FILE, ["1.1.1.0/24", "8.8.8.0/24"])
        for name, result in (
            ("collect_static_prefixes", (["1.1.1.0/24", "8.8.8.0/24"], {"source_stats": []})),
            ("collect_service_prefixes_for_update", ([], {"enabled": False})),
            ("build_community_route_plan", {
                "prefixes": ["1.1.1.0/24", "8.8.8.0/24"],
                "route_communities": {"1.1.1.0/24": [], "8.8.8.0/24": []},
                "profiles": [],
            }),
            ("publication_is_paused", False),
        ):
            mocked = patch.object(main, name, return_value=result)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_preview_does_not_persist_or_apply(self) -> None:
        with patch.object(main, "apply_prefixes") as apply:
            result = main.preview_route_exclusions(["1.1.1.0/25"])

        self.assertEqual(result["current_count"], 2)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["change"]["coverage_removed_addresses"], 128)
        self.assertTrue(result["change"]["requires_approval"])
        self.assertFalse(main.ROUTE_EXCLUSIONS_FILE.exists())
        apply.assert_not_called()

    def test_explicit_apply_backs_up_and_persists_exclusions(self) -> None:
        preview = main.preview_route_exclusions(["1.1.1.0/25"])
        with (
            patch.object(main, "create_system_backup", return_value={"backup_name": "before.zip"}) as backup,
            patch.object(main, "apply_prefixes", return_value={"added": 1, "deleted": 1}) as apply,
            patch.object(main, "save_status"),
            patch.object(main, "append_update_history"),
        ):
            result = main.apply_route_exclusions(
                preview["exclusions"], preview["active_sha256"],
                preview["change"]["current_sha256"], preview["change"]["candidate_sha256"],
            )

        self.assertTrue(result["ok"])
        backup.assert_called_once()
        applied = apply.call_args.args[0]
        self.assertEqual(applied, ["1.1.1.128/25", "8.8.8.0/24"])
        self.assertEqual(main.read_route_exclusions_state()["active"], ["1.1.1.0/25"])
        self.assertIsNone(main.read_route_exclusions_state()["pending"])
        self.assertEqual(main.read_last_good_snapshot()[0], applied)

    def test_changed_snapshot_rejects_old_approval_before_backup(self) -> None:
        preview = main.preview_route_exclusions(["1.1.1.0/25"])
        main.write_last_good_snapshot(["8.8.8.0/24"], {})
        with patch.object(main, "create_system_backup") as backup:
            with self.assertRaisesRegex(RuntimeError, "повторите предпросмотр"):
                main.apply_route_exclusions(
                    preview["exclusions"], preview["active_sha256"],
                    preview["change"]["current_sha256"], preview["change"]["candidate_sha256"],
                )
        backup.assert_not_called()

    def test_failed_apply_leaves_pending_and_blocks_auto_update(self) -> None:
        preview = main.preview_route_exclusions(["1.1.1.0/25"])
        with (
            patch.object(main, "create_system_backup", return_value={"backup_name": "before.zip"}),
            patch.object(main, "apply_prefixes", side_effect=RuntimeError("router offline")),
        ):
            with self.assertRaisesRegex(RuntimeError, "router offline"):
                main.apply_route_exclusions(
                    preview["exclusions"], preview["active_sha256"],
                    preview["change"]["current_sha256"], preview["change"]["candidate_sha256"],
                )
        self.assertIsNotNone(main.read_route_exclusions_state()["pending"])
        with self.assertRaisesRegex(RuntimeError, "не завершено"):
            main.update_now(trigger="auto")

    def test_failed_backup_does_not_start_policy_change(self) -> None:
        preview = main.preview_route_exclusions(["1.1.1.0/25"])
        with patch.object(main, "create_system_backup", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                main.apply_route_exclusions(
                    preview["exclusions"], preview["active_sha256"],
                    preview["change"]["current_sha256"], preview["change"]["candidate_sha256"],
                )
        self.assertFalse(main.ROUTE_EXCLUSIONS_FILE.exists())

    def test_large_coverage_swap_is_blocked_for_ordinary_update(self) -> None:
        main.build_community_route_plan.return_value = {
            "prefixes": ["9.9.9.0/24", "8.8.8.0/24"],
            "route_communities": {"9.9.9.0/24": [], "8.8.8.0/24": []}, "profiles": [],
        }
        with patch.object(main, "apply_prefixes") as apply:
            with self.assertRaisesRegex(RuntimeError, "предпросмотра"):
                main.update_now(trigger="auto")
        apply.assert_not_called()

    def test_incomplete_profile_blocks_preview_and_update(self) -> None:
        main.build_community_route_plan.return_value = {
            "prefixes": ["1.1.1.0/24", "8.8.8.0/24"],
            "route_communities": {"1.1.1.0/24": [], "8.8.8.0/24": []},
            "profiles": [{"id": "profile-a", "enabled": True, "errors": ["fetch failed"]}],
        }
        with self.assertRaisesRegex(RuntimeError, "community profile"):
            main.preview_route_exclusions([])
        with self.assertRaisesRegex(RuntimeError, "community profile"):
            main.update_now(trigger="auto")

    def test_active_exclusion_is_used_by_ordinary_update(self) -> None:
        main.write_json_atomic(main.ROUTE_EXCLUSIONS_FILE, {"version": 1, "active": ["1.1.1.0/25"], "pending": None})
        with (
            patch.object(main, "MAX_DELTA_PERCENT", 1000),
            patch.object(main, "apply_prefixes", return_value={"added": 1, "deleted": 1}) as apply,
            patch.object(main, "save_status"),
            patch.object(main, "append_update_history"),
        ):
            main.update_now(trigger="auto")
        self.assertEqual(apply.call_args.args[0], ["1.1.1.128/25", "8.8.8.0/24"])

    def test_corrupt_policy_and_snapshot_fail_closed(self) -> None:
        main.ROUTE_EXCLUSIONS_FILE.write_text("{", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            main.update_now(trigger="auto")
        main.ROUTE_EXCLUSIONS_FILE.unlink()
        main.LAST_GOOD_SNAPSHOT_FILE.write_text("{", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            main.preview_route_exclusions([])

    def test_api_rejects_unauthenticated_and_invalid_preview(self) -> None:
        client = TestClient(main.app)
        try:
            self.assertEqual(client.post("/api/routes/exclusions/preview", json={"exclusions": []}).status_code, 401)
            main.app.dependency_overrides[main.require_auth] = lambda: "test"
            try:
                self.assertEqual(client.post("/api/routes/exclusions/preview", json={"exclusions": ["0.0.0.0/0"]}).status_code, 400)
                self.assertEqual(client.post("/api/routes/exclusions/apply/job", json={"exclusions": []}).status_code, 400)
            finally:
                main.app.dependency_overrides.pop(main.require_auth, None)
        finally:
            client.close()

    def test_api_preview_requires_session_csrf(self) -> None:
        client = TestClient(main.app)
        try:
            with (
                patch.object(main, "WEB_PASSWORD", "unit-test-password"),
                patch.object(main, "WEB_PASSWORD_HASH", ""),
                patch.object(main, "SESSION_COOKIE_SECURE", False),
            ):
                login = client.post("/auth/login", json={"password": "unit-test-password"})
                self.assertEqual(login.status_code, 200)
                body = {"exclusions": ["1.1.1.0/25"]}
                self.assertEqual(client.post("/api/routes/exclusions/preview", json=body).status_code, 403)
                response = client.post(
                    "/api/routes/exclusions/preview", json=body,
                    headers={"X-CSRF-Token": login.json()["csrf_token"]},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["candidate_count"], 2)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
