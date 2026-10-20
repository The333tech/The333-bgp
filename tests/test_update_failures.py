import json
import tempfile
import unittest
from pathlib import Path

from app.maintenance import read_state, write_state
from app.update_failures import FAILURES, public_failure, record_failure, safe_failure


class UpdateFailureTests(unittest.TestCase):
    def test_only_known_codes_and_valid_numbers_are_kept(self):
        for value in (None, "secret", [], {}, {"code": []}, {"code": {}}, {"code": "unknown-secret"}):
            with self.subTest(value=value):
                self.assertIsNone(safe_failure(value))
        for free, required in ((True, 10), (1, False), (-1, 10), (10, 10), (11, 10), (1, 2**64), ("1", 10)):
            with self.subTest(free=free, required=required):
                self.assertEqual(safe_failure({"code": "disk_space", "free_bytes": free,
                                               "required_bytes": required}), {"code": "disk_space"})
        self.assertEqual(safe_failure({"code": "release_download", "message": "secret", "stderr": "secret"}),
                         {"code": "release_download"})

    def test_disk_failure_has_exact_safe_numbers_and_action(self):
        unit = 1024 * 1024
        failure = public_failure({"status": "failed", "failure": {
            "code": "disk_space", "free_bytes": 1624 * unit, "required_bytes": 2048 * unit,
            "message": "private configuration", "path": "/secret/path",
        }})
        self.assertEqual(failure["code"], "disk_space")
        self.assertIn("1624 МБ", failure["message"])
        self.assertIn("2048 МБ", failure["message"])
        self.assertIn("424 МБ", failure["action"])
        self.assertNotIn("private", json.dumps(failure))
        self.assertNotIn("/secret", json.dumps(failure))
        fractional = public_failure({"status": "failed", "failure": {
            "code": "disk_space", "free_bytes": 1624 * unit - 1, "required_bytes": 2048 * unit,
        }})
        self.assertIn("425 МБ", fractional["action"])

    def test_every_known_failure_has_a_message_and_next_step(self):
        for code in FAILURES:
            with self.subTest(code=code):
                failure = public_failure({"status": "failed", "failure": {"code": code}})
                self.assertTrue(failure["message"])
                self.assertTrue(failure["action"])
                recovery = public_failure({"status": "recovery_required", "failure": {"code": code}})
                self.assertIn("Не запускай обновление повторно", recovery["action"])
        for status in ("running", "queued", "succeeded"):
            self.assertIsNone(public_failure({"status": status, "failure": {"code": "disk_space"}}))
        self.assertIsNone(public_failure({"status": "failed"}))
        self.assertIsNone(public_failure({"status": "failed", "failure": {"code": "unknown"}}))

    def test_recording_keeps_transaction_metadata_and_rejects_inactive_operations(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaises(ValueError):
                record_failure(directory, "disk_space")
            state = {"request_id": "a" * 32, "status": "running", "stage": "download",
                     "history": [{"stage": "backup", "time": "2026-10-20T01:00:00Z"}]}
            write_state(directory, state)
            record_failure(directory, "disk_space", 1624 * 1024**2, 2048 * 1024**2)
            saved = read_state(directory)
            self.assertEqual({key: saved[key] for key in state}, state)
            self.assertEqual(saved["failure"]["required_bytes"], 2048 * 1024**2)
            with self.assertRaises(ValueError):
                record_failure(directory, "unknown")


if __name__ == "__main__":
    unittest.main()
