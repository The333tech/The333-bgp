import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class ServiceCatalogMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.builtin = self.root / "config" / "service_catalog.builtin.json"
        self.legacy = self.root / "config" / "service_catalog.json"
        self.user = self.root / "data" / "service_catalog.user.json"
        self.removed = self.root / "data" / "service_removed_catalog.json"
        self.backups = self.root / "data" / "service_catalog_backups"

        self.patches = [
            patch.object(main, "SERVICE_BUILTIN_CATALOG_FILE", self.builtin),
            patch.object(main, "SERVICE_LEGACY_CATALOG_FILE", self.legacy),
            patch.object(main, "SERVICE_USER_CATALOG_FILE", self.user),
            patch.object(main, "SERVICE_REMOVED_CATALOG_FILE", self.removed),
            patch.object(main, "SERVICE_CATALOG_BACKUP_DIR", self.backups),
        ]
        for current in self.patches:
            current.start()

    def tearDown(self) -> None:
        for current in reversed(self.patches):
            current.stop()
        self.temp_dir.cleanup()

    def test_legacy_user_items_migrate_without_copying_builtins(self) -> None:
        write_json(
            self.builtin,
            [
                {"id": "alpha", "title": "Alpha new", "providers": []},
                {"id": "beta", "title": "Beta", "providers": []},
            ],
        )
        write_json(
            self.legacy,
            [
                {"id": "alpha", "title": "Alpha old", "providers": []},
                {"id": "custom", "title": "Custom", "providers": []},
            ],
        )
        write_json(
            self.user,
            {
                "version": 1,
                "services": [{"id": "existing-user", "title": "Existing", "providers": []}],
            },
        )
        write_json(
            self.removed,
            {
                "version": 1,
                "services": {
                    "beta": {
                        "service": {"id": "beta", "title": "Beta old", "providers": []}
                    }
                },
            },
        )

        result = main.migrate_service_catalog_storage()
        catalog = main.read_service_catalog()
        by_id = {item["id"]: item for item in catalog}

        self.assertEqual(result["migrated_ids"], ["custom"])
        self.assertEqual(set(by_id), {"alpha", "custom", "existing-user"})
        self.assertEqual(by_id["alpha"]["title"], "Alpha new")
        self.assertNotIn("beta", by_id)

        user_payload = json.loads(self.user.read_text(encoding="utf-8"))
        self.assertEqual(
            {item["id"] for item in user_payload["services"]},
            {"custom", "existing-user"},
        )

    def test_migration_is_idempotent_and_builtin_updates_remain_visible(self) -> None:
        write_json(self.builtin, [{"id": "alpha", "title": "Alpha v1", "providers": []}])
        write_json(self.legacy, [{"id": "custom", "title": "Custom", "providers": []}])
        write_json(self.removed, {"version": 1, "services": {}})

        first = main.migrate_service_catalog_storage()
        second = main.migrate_service_catalog_storage()
        write_json(self.builtin, [{"id": "alpha", "title": "Alpha v2", "providers": []}])
        catalog = {item["id"]: item for item in main.read_service_catalog()}

        self.assertEqual(first["migrated_count"], 1)
        self.assertEqual(second["migrated_count"], 0)
        self.assertEqual(catalog["alpha"]["title"], "Alpha v2")
        self.assertEqual(catalog["custom"]["title"], "Custom")

    def test_completed_legacy_migration_does_not_resurrect_removed_user_item(self) -> None:
        write_json(self.builtin, [{"id": "alpha", "title": "Alpha", "providers": []}])
        write_json(self.legacy, [{"id": "custom", "title": "Custom", "providers": []}])
        write_json(self.removed, {"version": 1, "services": {}})

        main.migrate_service_catalog_storage()
        main.write_service_catalog([{"id": "alpha", "title": "Alpha", "providers": []}])
        main.migrate_service_catalog_storage()

        self.assertEqual([item["id"] for item in main.read_service_catalog()], ["alpha"])

    def test_writing_merged_catalog_persists_only_user_items(self) -> None:
        write_json(self.builtin, [{"id": "alpha", "title": "Alpha", "providers": []}])
        write_json(self.removed, {"version": 1, "services": {}})
        main.migrate_service_catalog_storage()

        main.write_service_catalog(
            [
                {"id": "alpha", "title": "Alpha", "providers": []},
                {"id": "custom", "title": "Custom", "providers": []},
            ]
        )

        user_payload = json.loads(self.user.read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in user_payload["services"]], ["custom"])


class SystemBackupConfigTests(unittest.TestCase):
    def test_builtin_config_is_excluded_but_custom_config_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            config_dir = root / "config"
            backup_dir = data_dir / "system_backups"
            staging_dir = data_dir / ".restore_staging"
            data_dir.mkdir()
            config_dir.mkdir()
            (data_dir / "sources.json").write_text("[]", encoding="utf-8")
            (data_dir / "gobgp_generation").write_text("runtime", encoding="utf-8")
            (data_dir / "gobgpd.toml").write_text("legacy-secret", encoding="utf-8")
            (config_dir / "service_catalog.builtin.json").write_text("[]", encoding="utf-8")
            (config_dir / "custom-provider.list").write_text("example.com", encoding="utf-8")

            with (
                patch.object(main, "DATA_DIR", data_dir),
                patch.object(main, "CONFIG_DIR", config_dir),
                patch.object(main, "SYSTEM_BACKUP_DIR", backup_dir),
                patch.object(main, "SYSTEM_RESTORE_STAGING_DIR", staging_dir),
                patch.object(main, "JOBS_FILE", data_dir / "jobs.json"),
                patch.object(main, "LEGACY_GOBGP_GENERATION_FILE", data_dir / "gobgp_generation"),
                patch.object(main, "GOBGP_STATE_DIR", data_dir / "gobgp-state"),
                patch.object(main, "LEGACY_GOBGP_CONFIG_FILE", data_dir / "gobgpd.toml"),
                patch.object(main, "SYSTEM_BACKUP_MAX_BYTES", 1024 * 1024),
            ):
                files, _, _, config_count = main.collect_system_backup_files()

            archive_names = {archive_name for _, archive_name in files}
            self.assertIn("config/custom-provider.list", archive_names)
            self.assertNotIn("config/service_catalog.builtin.json", archive_names)
            self.assertNotIn("data/gobgp_generation", archive_names)
            self.assertNotIn("data/gobgpd.toml", archive_names)
            self.assertEqual(config_count, 1)

    def test_restore_copy_cannot_overwrite_builtin_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stage = root / "stage"
            target = root / "target"
            stage.mkdir()
            target.mkdir()
            (stage / "service_catalog.builtin.json").write_text("old", encoding="utf-8")
            (stage / "custom.list").write_text("custom", encoding="utf-8")
            (target / "service_catalog.builtin.json").write_text("current", encoding="utf-8")

            copied = main.copy_staged_root(
                stage,
                target,
                skip_root_names=main.BUILTIN_CONFIG_FILENAMES,
            )

            self.assertEqual(copied, 1)
            self.assertEqual(
                (target / "service_catalog.builtin.json").read_text(encoding="utf-8"),
                "current",
            )
            self.assertEqual((target / "custom.list").read_text(encoding="utf-8"), "custom")


class DefaultSourcesMigrationTests(unittest.TestCase):
    def test_defaults_update_while_user_state_and_custom_sources_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            defaults_file = root / "config" / "default_sources.json"
            sources_file = root / "data" / "sources.json"
            quarantine_file = root / "data" / "sources.quarantine.json"
            write_json(
                defaults_file,
                [
                    {
                        "name": "remote",
                        "type": "url",
                        "enabled": False,
                        "url": "https://new.example/routes.txt",
                    },
                    {
                        "name": "manual-extra",
                        "type": "static",
                        "enabled": False,
                        "prefixes": [],
                        "manual_entries": [],
                    },
                ],
            )
            write_json(
                sources_file,
                [
                    {
                        "name": "remote",
                        "type": "url",
                        "enabled": True,
                        "url": "https://old.example/routes.txt",
                    },
                    {
                        "name": "manual-extra",
                        "type": "static",
                        "enabled": True,
                        "prefixes": [],
                        "manual_entries": ["example.com"],
                    },
                    {
                        "name": "custom-safe",
                        "type": "url",
                        "enabled": False,
                        "url": "https://custom.example/routes.txt",
                    },
                    {
                        "name": "custom-http",
                        "type": "url",
                        "enabled": True,
                        "url": "http://unsafe.example/routes.txt",
                    },
                ],
            )

            with (
                patch.object(main, "DEFAULT_SOURCES_FILE", defaults_file),
                patch.object(main, "SOURCES_FILE", sources_file),
                patch.object(main, "SOURCES_QUARANTINE_FILE", quarantine_file),
            ):
                result = main.migrate_default_sources()

            merged = {
                item["name"]: item
                for item in json.loads(sources_file.read_text(encoding="utf-8"))
            }
            quarantine = json.loads(quarantine_file.read_text(encoding="utf-8"))

            self.assertTrue(merged["remote"]["enabled"])
            self.assertEqual(merged["remote"]["url"], "https://new.example/routes.txt")
            self.assertEqual(merged["manual-extra"]["manual_entries"], ["example.com"])
            self.assertIn("custom-safe", merged)
            self.assertNotIn("custom-http", merged)
            self.assertEqual(result["quarantined_count"], 1)
            self.assertEqual(quarantine["sources"][0]["source"]["name"], "custom-http")


if __name__ == "__main__":
    unittest.main()
