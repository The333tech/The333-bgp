import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class CriticalStateIntegrityTests(unittest.TestCase):
    def test_invalid_sources_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sources.json"
            path.write_text("{broken", encoding="utf-8")
            with (
                patch.object(main, "DATA_DIR", Path(temp_dir)),
                patch.object(main, "SOURCES_FILE", path),
            ):
                with self.assertRaisesRegex(RuntimeError, "invalid JSON state file"):
                    main.read_sources_config()
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_invalid_service_state_does_not_become_empty_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "service_state.json"
            path.write_text('{"version":1,"services":[]}', encoding="utf-8")
            with (
                patch.object(main, "DATA_DIR", Path(temp_dir)),
                patch.object(main, "SERVICE_STATE_FILE", path),
            ):
                with self.assertRaisesRegex(RuntimeError, "services must be a JSON object"):
                    main.read_service_state()

    def test_atomic_json_write_replaces_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            main.write_json_atomic(path, {"version": 1, "items": [1, 2, 3]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["items"], [1, 2, 3])
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_gobgp_runtime_state_is_excluded_from_portal_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_state = root / "gobgp-state"
            runtime_state.mkdir()
            generation = runtime_state / "gobgp_generation"
            generation.write_text("test-generation\n", encoding="utf-8")
            with patch.object(main, "GOBGP_STATE_DIR", runtime_state):
                self.assertTrue(main.system_backup_skip_path(generation))

    def test_service_dns_cache_removes_invalid_entries_and_persists_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "service_dns_cache.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "domains": {
                            "collector.github.com": {
                                "ips": {
                                    "0.0.0.0": {"last_status": "current"},
                                    "8.8.8.8": {"last_status": "current"},
                                    "not-an-ip": {"last_status": "stale"},
                                }
                            },
                            "broken domain": {"ips": {"1.1.1.1": {}}},
                            "invalid.example": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(main, "SERVICE_DNS_CACHE_FILE", path):
                cache = main.read_service_dns_cache()

            self.assertEqual(set(cache["domains"]), {"collector.github.com"})
            self.assertEqual(
                set(cache["domains"]["collector.github.com"]["ips"]),
                {"8.8.8.8"},
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["domains"], cache["domains"])
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_corrupted_service_dns_cache_recovers_to_empty_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "service_dns_cache.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.object(main, "SERVICE_DNS_CACHE_FILE", path):
                cache = main.read_service_dns_cache()

            self.assertEqual(cache["version"], 1)
            self.assertEqual(cache["domains"], {})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["domains"], {})


class RouteHistoryProvenanceTests(unittest.TestCase):
    def test_history_records_selected_inputs_and_final_combined_count(self) -> None:
        result = {
            "ok": True,
            "mode": "mvp_static_update",
            "time": "2026-07-12T00:00:00+00:00",
            "prefix_summary": {"count": 10},
            "meta": {
                "final_count": 10,
                "final_count_with_services": 14,
                "route_set_sha256": "abc",
                "source_stats": [
                    {"name": "main", "enabled": True, "selected": True},
                    {"name": "off", "enabled": False, "selected": False},
                ],
                "service_routes": {
                    "service_stats": [
                        {"id": "telegram", "enabled": True, "selected": True},
                        {"id": "youtube", "enabled": True, "selected": False},
                    ]
                },
            },
            "apply": {"advertised_count": 14, "added": 1, "deleted": 0},
        }
        record = main.compact_history_record(result, "manual")
        self.assertEqual(record["final_count"], 14)
        self.assertEqual(record["selected_sources"], ["main"])
        self.assertEqual(record["selected_services"], ["telegram"])
        self.assertEqual(record["route_set_sha256"], "abc")

    def test_route_set_fingerprint_is_order_independent(self) -> None:
        first = main.route_set_sha256(["10.0.1.0/24", "10.0.0.0/24"])
        second = main.route_set_sha256(["10.0.0.0/24", "10.0.1.0/24"])
        self.assertEqual(first, second)


class ReadinessTests(unittest.TestCase):
    def test_clean_unconfigured_install_is_ready_for_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            advertised = root / "advertised.txt"
            last_good = root / "last-good.txt"
            status = root / "status.json"
            with (
                patch.object(main, "ADVERTISED_FILE", advertised),
                patch.object(main, "LAST_GOOD_FILE", last_good),
                patch.object(main, "STATUS_FILE", status),
                patch.object(main, "gobgp_ready", return_value=True),
                patch.object(main, "gobgp_rib_count", return_value=0),
            ):
                payload, status_code = main.build_readiness_payload()

            self.assertEqual(status_code, 200)
            self.assertTrue(payload["ready"])
            self.assertTrue(payload["unconfigured"])
            self.assertEqual(payload["advertised_count"], 0)


class ProductVersionTests(unittest.TestCase):
    def test_beta_to_stable_and_newer_beta_ordering(self) -> None:
        self.assertTrue(main.product_version_is_newer("0.82", "0.82b"))
        self.assertTrue(main.product_version_is_newer("0.82b", "0.78"))
        self.assertFalse(main.product_version_is_newer("0.82b", "0.82b"))
        self.assertFalse(main.product_version_is_newer("0.78", "0.82b"))

    def test_github_release_without_verified_digest_is_ignored(self) -> None:
        release = {
            "tag_name": "v9.9",
            "prerelease": False,
            "assets": [
                {
                    "name": "the333-bgp-v9.9.tar.gz",
                    "browser_download_url": "https://github.com/example/release.tar.gz",
                    "digest": None,
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "no usable release assets"):
            main.update_manifest_from_github_releases([release])


if __name__ == "__main__":
    unittest.main()
