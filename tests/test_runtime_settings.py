import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class RuntimeSettingsTests(unittest.TestCase):
    def test_defaults_are_materialized_and_route_settings_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "runtime_settings.json"
            with patch.object(main, "RUNTIME_SETTINGS_FILE", settings_path):
                defaults = main.read_runtime_settings()
                updated = main.update_runtime_settings(
                    {"route_auto_update": {"enabled": False, "interval_minutes": 45}}
                )

                self.assertTrue(settings_path.is_file())
                self.assertEqual(defaults["version"], 1)
                self.assertFalse(updated["route_auto_update"]["enabled"])
                self.assertEqual(updated["route_auto_update"]["interval_minutes"], 45)
                with self.assertRaisesRegex(main.HTTPException, "допустимо"):
                    main.update_runtime_settings(
                        {"route_auto_update": {"interval_minutes": 1}}
                    )

    def test_backup_bookkeeping_does_not_change_state_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime_settings.json"
            settings = main.default_runtime_settings()
            path.write_text(json.dumps(settings), encoding="utf-8")
            with patch.object(main, "RUNTIME_SETTINGS_FILE", path):
                first = main.system_backup_state_sha256([(path, "data/runtime_settings.json")])

                settings["automatic_backup"]["last_checked_at"] = "2026-07-13T01:00:00+00:00"
                settings["automatic_backup"]["last_result"] = "unchanged"
                path.write_text(json.dumps(settings), encoding="utf-8")
                second = main.system_backup_state_sha256([(path, "data/runtime_settings.json")])

                settings["automatic_backup"]["interval_days"] = 7
                path.write_text(json.dumps(settings), encoding="utf-8")
                third = main.system_backup_state_sha256([(path, "data/runtime_settings.json")])

            self.assertEqual(first, second)
            self.assertNotEqual(second, third)

    def test_automatic_backup_reuses_current_archive_when_state_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            config_dir = root / "config"
            backup_dir = data_dir / "system_backups"
            settings_path = data_dir / "runtime_settings.json"
            data_dir.mkdir()
            config_dir.mkdir()
            (data_dir / "sources.json").write_text("[]\n", encoding="utf-8")
            (config_dir / "custom.list").write_text("example.com\n", encoding="utf-8")

            replacements = {
                "DATA_DIR": data_dir,
                "CONFIG_DIR": config_dir,
                "SYSTEM_BACKUP_DIR": backup_dir,
                "SYSTEM_RESTORE_STAGING_DIR": data_dir / ".restore_staging",
                "RUNTIME_SETTINGS_FILE": settings_path,
                "HOST_UPDATER_RESULT_DIR": data_dir / "host-updater-results",
                "GOBGP_STATE_DIR": data_dir / "gobgp-state",
                "JOBS_FILE": data_dir / "jobs.json",
                "LEGACY_GOBGP_GENERATION_FILE": data_dir / "gobgp_generation",
                "LEGACY_GOBGP_CONFIG_FILE": data_dir / "gobgpd.toml",
                "SYSTEM_BACKUP_MAX_BYTES": 4 * 1024 * 1024,
            }
            with ExitStack() as stack:
                for name, value in replacements.items():
                    stack.enter_context(patch.object(main, name, value))
                settings = main.read_runtime_settings()
                settings["automatic_backup"]["enabled"] = True
                main.write_runtime_settings(settings)
                first = main.create_system_backup(trigger="manual")
                second = main.run_automatic_backup_check()

            self.assertFalse(first.get("skipped", False))
            self.assertTrue(second["skipped"])
            self.assertEqual(second["reason"], "state_unchanged")
            self.assertEqual(len(list(backup_dir.glob("*.zip"))), 1)

    def test_automatic_backup_due_uses_last_check_and_interval(self) -> None:
        settings = main.default_runtime_settings()
        settings["automatic_backup"].update(
            {
                "enabled": True,
                "interval_days": 2,
                "last_checked_at": "2026-07-10T00:00:00+00:00",
            }
        )
        now_value = datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp()
        self.assertTrue(main.automatic_backup_is_due(settings, now_value))


if __name__ == "__main__":
    unittest.main()
