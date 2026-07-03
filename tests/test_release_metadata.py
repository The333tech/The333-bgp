import json
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_is_present_in_manifest(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        manifest = json.loads((ROOT / "update-manifest.json").read_text(encoding="utf-8"))
        versions = manifest.get("versions", [])

        matching = [item for item in versions if str(item.get("version")) == version]
        self.assertEqual(len(matching), 1)
        self.assertIn(matching[0].get("channel"), {"stable", "beta"})
        self.assertEqual(manifest.get("latest", {}).get(matching[0]["channel"]), version)

    def test_release_urls_use_https(self) -> None:
        manifest = json.loads((ROOT / "update-manifest.json").read_text(encoding="utf-8"))

        for item in manifest.get("versions", []):
            with self.subTest(version=item.get("version")):
                archive_url = str(item.get("archive_url", ""))
                self.assertEqual(urlparse(archive_url).scheme, "https")

    def test_default_sources_are_safe_and_opt_in(self) -> None:
        sources = json.loads((ROOT / "config" / "default_sources.json").read_text(encoding="utf-8"))
        names = {str(item.get("name", "")) for item in sources}

        self.assertIn("manual-extra", names)
        self.assertIn("antifilter-download-allyouneed", names)
        self.assertFalse(any("cloudflare" in name for name in names))
        self.assertTrue(all(item.get("enabled") is False for item in sources))

        for item in sources:
            if item.get("type") == "url":
                with self.subTest(source=item.get("name")):
                    self.assertEqual(urlparse(str(item.get("url", ""))).scheme, "https")


if __name__ == "__main__":
    unittest.main()
