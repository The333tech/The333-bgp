import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402
from app.main import route_set_definition, safe_backup_archive_parts, safe_existing_named_child_path  # noqa: E402


class SafePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.pattern = re.compile(r"^backup-\d{8}\.json$")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_existing_allowlisted_file_is_returned(self) -> None:
        expected = self.root / "backup-20260627.json"
        expected.write_text("{}", encoding="utf-8")

        actual = safe_existing_named_child_path(
            self.root,
            expected.name,
            self.pattern,
            ".json",
        )

        self.assertEqual(actual, expected.resolve())

    def test_missing_allowlisted_file_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            safe_existing_named_child_path(
                self.root,
                "backup-20990101.json",
                self.pattern,
                ".json",
            )

        self.assertEqual(raised.exception.status_code, 404)

    def test_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            safe_existing_named_child_path(
                self.root,
                "../backup-20260627.json",
                self.pattern,
                ".json",
            )

        self.assertEqual(raised.exception.status_code, 400)

    def test_backup_archive_path_must_stay_in_supported_roots(self) -> None:
        self.assertEqual(
            safe_backup_archive_parts("data/sources.json"),
            ("data", "sources.json"),
        )

        for unsafe in ("../data/sources.json", "/etc/passwd", "data/../../etc/passwd"):
            with self.subTest(unsafe=unsafe), self.assertRaises(HTTPException):
                safe_backup_archive_parts(unsafe)

    def test_route_download_kind_uses_fixed_allowlist(self) -> None:
        self.assertEqual(route_set_definition("advertised")["label"], "Опубликованные")

        for unsafe in ("../../etc/passwd", "/etc/passwd", "custom-routes"):
            with self.subTest(unsafe=unsafe), self.assertRaises(HTTPException) as raised:
                route_set_definition(unsafe)
            self.assertEqual(raised.exception.status_code, 400)

    def test_gobgp_neighbor_detail_uses_valid_configured_peer_only(self) -> None:
        with (
            patch.object(main, "PEER_ADDRESS", "192.0.2.1"),
            patch.object(main, "gobgp_text", return_value="neighbor details\n") as gobgp_text,
        ):
            self.assertEqual(main.gobgp_neighbor_detail(), "neighbor details\n")
            gobgp_text.assert_called_once_with(["neighbor", "192.0.2.1"])

        with (
            patch.object(main, "PEER_ADDRESS", "not-an-ip"),
            patch.object(main, "gobgp_text") as gobgp_text,
        ):
            self.assertEqual(main.gobgp_neighbor_detail(), "")
            gobgp_text.assert_not_called()


class PublicRouteErrorTests(unittest.IsolatedAsyncioTestCase):
    def test_policy_messages_only_use_fixed_public_text(self) -> None:
        for reason, expected in main.RoutePolicyReviewRequired.MESSAGES.items():
            with self.subTest(reason=reason):
                error = main.RoutePolicyReviewRequired(reason)
                self.assertEqual(error.public_message, expected)
                self.assertEqual(str(error), expected)
        error = main.RoutePolicyReviewRequired("private-diagnostic-sentinel")
        self.assertNotIn("private-diagnostic-sentinel", error.public_message)
        self.assertNotIn("private-diagnostic-sentinel", str(error))

    async def test_manual_update_preserves_review_hint_but_hides_exception_details(self) -> None:
        for error in (RuntimeError("private-diagnostic-sentinel"),
                      main.RoutePolicyReviewRequired("private-diagnostic-sentinel"),
                      main.RoutePolicyReviewRequired("stale_preview")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(main, "update_now", side_effect=error), \
                    patch.object(main, "save_status"), patch.object(main, "append_update_history"), \
                    patch.object(main, "log_exception"):
                response = await main.update(SimpleNamespace(json=AsyncMock(return_value={})), "test")
            result = json.loads(response.body)
            self.assertEqual(response.status_code, 500)
            self.assertNotIn("private-diagnostic-sentinel", result["error"])
            if isinstance(error, main.RoutePolicyReviewRequired):
                self.assertEqual(result["error"], error.public_message)

    async def test_background_update_preserves_review_hint_but_hides_exception_details(self) -> None:
        for error in (RuntimeError("private-diagnostic-sentinel"),
                      main.RoutePolicyReviewRequired("private-diagnostic-sentinel"),
                      main.RoutePolicyReviewRequired("large_change")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(main, "claim_queued_job", return_value=True), \
                    patch.object(main, "guarded_mutation", side_effect=error), \
                    patch.object(main, "update_job_record") as record, patch.object(main, "log_exception"):
                await main.run_background_job("public-error-test", "route_update", lambda: None)
            result = record.call_args.args[1]
            self.assertEqual(result["status"], "failed")
            self.assertNotIn("private-diagnostic-sentinel", result["error"])
            if isinstance(error, main.RoutePolicyReviewRequired):
                self.assertEqual(result["error"], error.public_message)


if __name__ == "__main__":
    unittest.main()
