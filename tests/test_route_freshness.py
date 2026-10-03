import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class RouteFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        control_patch = patch.object(main, "PUBLICATION_CONTROL_FILE", Path(self.directory.name) / "publication_control.json")
        control_patch.start()
        self.addCleanup(control_patch.stop)
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
        self.settings = {"route_auto_update": {"enabled": True, "interval_minutes": 30}}

    def assess(self, updated_at):
        snapshot = {"updated_at": updated_at, "prefixes": ["1.1.1.0/24"]} if updated_at is not None else {}
        return main.route_freshness_status(self.settings, snapshot, self.now)

    def test_fresh_and_stale_use_three_intervals(self) -> None:
        fresh = self.assess("2026-10-03T10:45:00+00:00")
        stale = self.assess("2026-10-03T10:00:00+00:00")

        self.assertEqual(fresh["status"], "fresh")
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["age_seconds"], 7200)
        self.assertEqual(stale["threshold_seconds"], 5400)

    def test_short_interval_has_minimum_grace(self) -> None:
        self.settings["route_auto_update"]["interval_minutes"] = 5
        self.assertEqual(self.assess("2026-10-03T11:45:00Z")["status"], "fresh")
        self.assertEqual(self.assess("2026-10-03T11:25:00Z")["status"], "stale")

    def test_paused_and_disabled_do_not_warn(self) -> None:
        main.write_publication_control("paused", True)
        self.assertEqual(self.assess("2026-10-01T00:00:00Z")["status"], "paused")
        self.settings["route_auto_update"]["enabled"] = False
        self.assertEqual(self.assess("2026-10-01T00:00:00Z")["status"], "disabled")

    def test_missing_invalid_or_future_timestamp_is_unknown(self) -> None:
        for value in (None, "garbled", "2026-10-03T12:00:00", "2026-10-04T12:00:00Z"):
            with self.subTest(value=value):
                self.assertEqual(self.assess(value)["status"], "unknown")
        self.assertEqual(main.route_freshness_status(
            self.settings, {"updated_at": "2026-10-03T11:00:00Z", "prefixes": []}, self.now,
        )["status"], "unknown")

    def test_authenticated_diagnostics_exposes_freshness(self) -> None:
        snapshot_path = Path(self.directory.name) / "snapshot.json"
        main.write_json_atomic(snapshot_path, {"updated_at": "2020-01-01T00:00:00Z", "prefixes": ["1.1.1.0/24"]})
        main.app.dependency_overrides[main.require_auth] = lambda: "test"
        self.addCleanup(main.app.dependency_overrides.pop, main.require_auth, None)
        client = TestClient(main.app)
        self.addCleanup(client.close)

        with (
            patch.object(main, "LAST_GOOD_SNAPSHOT_FILE", snapshot_path),
            patch.object(main, "read_runtime_settings", return_value=self.settings),
            patch.object(main, "gobgp_ready", return_value=True),
            patch.object(main, "publication_status", return_value={"mode": "publishing"}),
            patch.object(main, "gobgp_rib_count", return_value=0),
            patch.object(main, "gobgp_text", return_value=""),
            patch.object(main, "gobgp_neighbor_detail", return_value=""),
        ):
            response = client.get("/api/diagnostics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route_freshness"]["status"], "stale")


if __name__ == "__main__":
    unittest.main()
