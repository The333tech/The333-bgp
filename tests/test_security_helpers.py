import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
