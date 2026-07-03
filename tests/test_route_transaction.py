import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class RouteTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.neighbor_enable_patcher = patch.object(main, "ensure_gobgp_neighbor_enabled")
        self.neighbor_enable = self.neighbor_enable_patcher.start()

    def tearDown(self) -> None:
        self.neighbor_enable_patcher.stop()

    def run_apply(
        self,
        current: set[str],
        target: list[str],
        *,
        previous_communities: dict[str, list[str]] | None = None,
        target_communities: dict[str, list[str]] | None = None,
        fail_add_once: set[str] | None = None,
        fail_del_once: set[str] | None = None,
        persist_adds: bool = True,
    ) -> tuple[dict[str, object], set[str], list[tuple[str, str]], object, object]:
        state = set(current)
        operations: list[tuple[str, str]] = []
        add_failures = set(fail_add_once or set())
        del_failures = set(fail_del_once or set())

        def fake_add(prefix: str, communities: list[str] | None = None) -> tuple[bool, str]:
            operations.append(("add", prefix))
            if prefix in add_failures:
                add_failures.remove(prefix)
                return False, "simulated add failure"
            if persist_adds:
                state.add(prefix)
            return True, ""

        def fake_del(prefix: str) -> tuple[bool, str]:
            operations.append(("del", prefix))
            if prefix in del_failures:
                del_failures.remove(prefix)
                return False, "simulated delete failure"
            state.discard(prefix)
            return True, ""

        with (
            patch.object(main, "gobgp_ready", return_value=True),
            patch.object(main, "gobgp_current_prefixes", side_effect=lambda: set(state)),
            patch.object(main, "gobgp_add", side_effect=fake_add),
            patch.object(main, "gobgp_del", side_effect=fake_del),
            patch.object(main, "read_lines", return_value=sorted(current)),
            patch.object(main, "read_route_attributes", return_value=previous_communities or {}),
            patch.object(main, "write_prefixes_file") as write_prefixes,
            patch.object(main, "write_route_attributes") as write_attributes,
        ):
            result = main.apply_prefixes(
                target,
                route_communities=target_communities or {},
            )

        return result, state, operations, write_prefixes, write_attributes

    def test_success_adds_before_deleting_and_persists_after_verification(self) -> None:
        result, state, operations, write_prefixes, write_attributes = self.run_apply(
            {"10.0.0.0/24", "20.0.0.0/24"},
            ["20.0.0.0/24", "30.0.0.0/24"],
        )

        self.assertEqual(state, {"20.0.0.0/24", "30.0.0.0/24"})
        self.assertEqual(operations, [("add", "30.0.0.0/24"), ("del", "10.0.0.0/24")])
        self.assertEqual(result["added"], 1)
        self.assertEqual(result["deleted"], 1)
        write_prefixes.assert_called_once()
        write_attributes.assert_called_once()

    def test_add_failure_keeps_original_rib_and_does_not_persist(self) -> None:
        state = {"10.0.0.0/24"}

        with self.assertRaisesRegex(RuntimeError, "rollback completed"):
            self.run_apply(
                state,
                ["10.0.0.0/24", "20.0.0.0/24"],
                fail_add_once={"20.0.0.0/24"},
            )

    def test_delete_failure_removes_route_added_before_failure(self) -> None:
        state = {"10.0.0.0/24"}
        operations: list[tuple[str, str]] = []
        live_state = set(state)
        delete_failed = False

        def fake_add(prefix: str, communities: list[str] | None = None) -> tuple[bool, str]:
            operations.append(("add", prefix))
            live_state.add(prefix)
            return True, ""

        def fake_del(prefix: str) -> tuple[bool, str]:
            nonlocal delete_failed
            operations.append(("del", prefix))
            if prefix == "10.0.0.0/24" and not delete_failed:
                delete_failed = True
                return False, "simulated delete failure"
            live_state.discard(prefix)
            return True, ""

        with (
            patch.object(main, "gobgp_ready", return_value=True),
            patch.object(main, "gobgp_current_prefixes", side_effect=lambda: set(live_state)),
            patch.object(main, "gobgp_add", side_effect=fake_add),
            patch.object(main, "gobgp_del", side_effect=fake_del),
            patch.object(main, "read_lines", return_value=sorted(state)),
            patch.object(main, "read_route_attributes", return_value={}),
            patch.object(main, "write_prefixes_file") as write_prefixes,
            patch.object(main, "write_route_attributes") as write_attributes,
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback completed"):
                main.apply_prefixes(["20.0.0.0/24"])

        self.assertEqual(live_state, state)
        self.assertEqual(operations[-1], ("del", "20.0.0.0/24"))
        write_prefixes.assert_not_called()
        write_attributes.assert_not_called()

    def test_attribute_failure_restores_previous_route(self) -> None:
        state = {"10.0.0.0/24"}
        live_state = set(state)
        failed = False
        add_communities: list[list[str]] = []

        def fake_add(prefix: str, communities: list[str] | None = None) -> tuple[bool, str]:
            nonlocal failed
            add_communities.append(list(communities or []))
            if not failed:
                failed = True
                return False, "simulated attribute add failure"
            live_state.add(prefix)
            return True, ""

        def fake_del(prefix: str) -> tuple[bool, str]:
            live_state.discard(prefix)
            return True, ""

        with (
            patch.object(main, "gobgp_ready", return_value=True),
            patch.object(main, "gobgp_current_prefixes", side_effect=lambda: set(live_state)),
            patch.object(main, "gobgp_add", side_effect=fake_add),
            patch.object(main, "gobgp_del", side_effect=fake_del),
            patch.object(main, "read_lines", return_value=sorted(state)),
            patch.object(main, "read_route_attributes", return_value={"10.0.0.0/24": []}),
            patch.object(main, "write_prefixes_file") as write_prefixes,
            patch.object(main, "write_route_attributes") as write_attributes,
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback completed"):
                main.apply_prefixes(
                    ["10.0.0.0/24"],
                    route_communities={"10.0.0.0/24": ["64500:530:1"]},
                )

        self.assertEqual(live_state, state)
        self.assertIn([], add_communities)
        write_prefixes.assert_not_called()
        write_attributes.assert_not_called()

    def test_verification_mismatch_rolls_back_and_does_not_persist(self) -> None:
        state: set[str] = set()

        with (
            patch.object(main, "gobgp_ready", return_value=True),
            patch.object(main, "gobgp_current_prefixes", side_effect=lambda: set(state)),
            patch.object(main, "gobgp_add", return_value=(True, "")),
            patch.object(main, "gobgp_del", return_value=(True, "")),
            patch.object(main, "read_lines", return_value=[]),
            patch.object(main, "read_route_attributes", return_value={}),
            patch.object(main, "write_prefixes_file") as write_prefixes,
            patch.object(main, "write_route_attributes") as write_attributes,
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback completed"):
                main.apply_prefixes(["10.0.0.0/24"])

        write_prefixes.assert_not_called()
        write_attributes.assert_not_called()

    def test_neighbor_enable_failure_rolls_back_and_does_not_persist(self) -> None:
        state = {"10.0.0.0/24"}
        self.neighbor_enable.side_effect = RuntimeError("simulated neighbor enable failure")

        with self.assertRaisesRegex(RuntimeError, "rollback completed"):
            self.run_apply(state, ["10.0.0.0/24", "20.0.0.0/24"])

    def test_recovery_uses_last_good_trigger(self) -> None:
        with patch.object(main, "apply_last_good", return_value={"ok": True}) as restore:
            result = main.restore_after_gobgp_recovery()

        restore.assert_called_once_with(False, "gobgp_recovery")
        self.assertTrue(result["gobgp_recovery_ok"])

    def test_update_operations_are_serialized(self) -> None:
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        call_count = 0
        count_lock = threading.Lock()

        def fake_update(*args: object, **kwargs: object) -> dict[str, bool]:
            nonlocal call_count
            with count_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_entered.set()
                release_first.wait(timeout=2)
            else:
                second_entered.set()
            return {"ok": True}

        with patch.object(main, "_update_now_locked", side_effect=fake_update):
            first = threading.Thread(target=main.update_now)
            second = threading.Thread(target=main.update_now)
            first.start()
            self.assertTrue(first_entered.wait(timeout=1))
            second.start()
            time.sleep(0.05)
            self.assertFalse(second_entered.is_set())
            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertTrue(second_entered.is_set())

    def test_last_good_snapshot_roundtrip_preserves_route_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot_path = root / "last_good_route_snapshot.json"
            legacy_path = root / "last_good_prefixes.txt"

            with (
                patch.object(main, "LAST_GOOD_SNAPSHOT_FILE", snapshot_path),
                patch.object(main, "LAST_GOOD_FILE", legacy_path),
            ):
                main.write_last_good_snapshot(
                    ["20.0.0.0/24", "11.0.0.0/24"],
                    {"20.0.0.0/24": ["64500:530:2"]},
                )
                prefixes, route_communities = main.read_last_good_snapshot()

            self.assertEqual(prefixes, ["11.0.0.0/24", "20.0.0.0/24"])
            self.assertEqual(route_communities["20.0.0.0/24"], ["64500:530:2"])
            self.assertEqual(legacy_path.read_text(encoding="utf-8").splitlines(), prefixes)


class UpdateSafetyTests(unittest.TestCase):
    def test_drop_above_default_threshold_is_rejected(self) -> None:
        previous = [f"10.0.{index}.0/24" for index in range(100)]
        target = previous[:64]

        with (
            patch.object(main, "read_lines", return_value=previous),
            patch.object(main, "MAX_DELTA_PERCENT", 35),
            patch.object(main, "MIN_EXPECTED_PREFIXES", 0),
        ):
            with self.assertRaisesRegex(RuntimeError, "exceeds MAX_DELTA_PERCENT"):
                main.validate_update_safety(target)

    def test_drop_at_threshold_is_allowed(self) -> None:
        previous = [f"10.0.{index}.0/24" for index in range(100)]
        target = previous[:65]

        with (
            patch.object(main, "read_lines", return_value=previous),
            patch.object(main, "MAX_DELTA_PERCENT", 35),
            patch.object(main, "MIN_EXPECTED_PREFIXES", 0),
        ):
            result = main.validate_update_safety(target)

        self.assertTrue(result["ok"])
        self.assertEqual(result["drop_percent"], 35.0)

    def test_failed_enabled_url_source_is_rejected(self) -> None:
        source_meta = {
            "source_stats": [
                {
                    "name": "remote-list",
                    "enabled": True,
                    "skipped": False,
                    "type": "url",
                    "accepted": 0,
                    "error": "download failed",
                }
            ]
        }

        with self.assertRaisesRegex(RuntimeError, "route collection incomplete"):
            main.validate_route_collection_health(source_meta, {"enabled": False})

    def test_disabled_and_successful_sources_pass_collection_health(self) -> None:
        source_meta = {
            "source_stats": [
                {
                    "name": "disabled-list",
                    "enabled": False,
                    "skipped": True,
                    "type": "url",
                    "accepted": 0,
                    "error": None,
                },
                {
                    "name": "active-list",
                    "enabled": True,
                    "skipped": False,
                    "type": "url",
                    "accepted": 10,
                    "error": None,
                },
            ]
        }

        result = main.validate_route_collection_health(source_meta, {"enabled": False})

        self.assertTrue(result["ok"])

    def test_failed_enabled_service_is_rejected(self) -> None:
        service_meta = {
            "enabled": True,
            "service_stats": [
                {
                    "id": "example",
                    "enabled": True,
                    "accepted": 0,
                    "error": "provider failed",
                }
            ],
        }

        with self.assertRaisesRegex(RuntimeError, "services=.*example"):
            main.validate_route_collection_health({"source_stats": []}, service_meta)


if __name__ == "__main__":
    unittest.main()
