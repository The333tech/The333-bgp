import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("WEB_PASSWORD", "unit-test-password")
import app.main as main  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def release(version, *, beta=True, draft=False, digest=True):
    return {"tag_name": f"v{version}", "prerelease": beta, "draft": draft, "assets": [{
        "name": f"the333-bgp-v{version}.tar.gz",
        "browser_download_url": f"https://github.com/example/releases/{version}.tar.gz",
        "digest": f"sha256:{'a' * 64}" if digest else None,
    }]}


def unordered_releases():
    return [release("0.93.1b"), release("0.94b"), release("0.9b"), release("0.94.1b"),
            release("0.93", beta=False), release("0.94", beta=False),
            release("9.9b", draft=True), release("8.8b", digest=False)]


class UpdateDiscoveryTests(unittest.TestCase):
    def test_backend_selects_newest_verified_version_in_each_channel(self):
        manifest = main.update_manifest_from_github_releases(unordered_releases())
        self.assertEqual(manifest["latest"], {"stable": "0.94", "beta": "0.94.1b"})
        self.assertEqual([item["version"] for item in manifest["versions"] if item["recommended"]],
                         ["0.94.1b", "0.94"])

    def test_shared_manifest_cache_is_short_lived_and_returns_independent_copies(self):
        with (patch.object(main, "PRODUCT_UPDATE_MANIFEST_URL", "https://example.com/releases"),
              patch.object(main, "UPDATE_MANIFEST_CACHE", None),
              patch.object(main.time, "monotonic", return_value=100) as clock,
              patch.object(main, "fetch_remote_json", return_value=(unordered_releases(), {})) as fetch):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: main.load_update_manifest_cached(), range(4)))
            self.assertEqual(fetch.call_count, 1)
            results[0]["latest"]["beta"] = "modified"
            self.assertEqual(main.load_update_manifest_cached()["latest"]["beta"], "0.94.1b")
            clock.return_value = 161
            self.assertEqual(main.load_update_manifest_cached()["latest"]["beta"], "0.94.1b")
            self.assertEqual(fetch.call_count, 2)
            self.assertFalse(fetch.call_args.kwargs["use_cache"])

    def test_network_failure_is_not_presented_as_a_fresh_cached_release_list(self):
        with (patch.object(main, "PRODUCT_UPDATE_MANIFEST_URL", "https://example.com/releases"),
              patch.object(main, "UPDATE_MANIFEST_CACHE", None),
              patch.object(main.time, "monotonic", return_value=100) as clock,
              patch.object(main, "fetch_remote_json", side_effect=OSError("offline")) as fetch):
            self.assertTrue(main.load_update_manifest_cached()["manifest_unavailable"])
            main.load_update_manifest_cached()
            self.assertEqual(fetch.call_count, 1)
            clock.return_value = 116
            main.load_update_manifest_cached()
            self.assertEqual(fetch.call_count, 2)

    def test_unconfigured_manifest_never_contacts_the_network(self):
        with (patch.object(main, "PRODUCT_UPDATE_MANIFEST_URL", ""),
              patch.object(main, "UPDATE_MANIFEST_CACHE", None),
              patch.object(main, "fetch_remote_json") as fetch):
            self.assertIsNone(main.load_update_manifest_cached()["manifest_url"])
            fetch.assert_not_called()

    def test_updates_endpoint_disallows_http_caching(self):
        with patch.object(main, "load_update_manifest", new=AsyncMock(return_value={"ok": True})):
            response = asyncio.run(main.api_product_updates("test"))
        self.assertEqual(response.headers["cache-control"], "no-store")


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "requires a POSIX shell")
class ShellReleaseDiscoveryTests(unittest.TestCase):
    def normalize(self, relative, releases):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "controller.sh"
            script.write_text((ROOT / relative).read_text().replace('\nmain "$@"\n', '\n'))
            fixture = root / "releases.json"
            fixture.write_text(json.dumps(releases))
            if relative == "install.sh":
                body = 'source "$1"\nnormalize_release_index | select_install_version_json'
            else:
                body = ('source "$1"\nCHANNEL=beta\nMANIFEST_URL=https://example.com/releases\n'
                        'download_manifest_file() { cp "$FIXTURE" "$2"; }\n'
                        'fetch_manifest | select_version_json')
            return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", body, "test", str(script)],
                                  input=json.dumps(releases), capture_output=True, text=True, check=False,
                                  env={**os.environ, "FIXTURE": str(fixture), "TMPDIR": str(root),
                                       "THE333_CHANNEL": "beta", "THE333_VERSION": ""})

    def test_installer_and_updater_choose_newest_verified_release_not_first_entry(self):
        for script in ("install.sh", "scripts/the333bgp.sh"):
            with self.subTest(script=script):
                result = self.normalize(script, unordered_releases())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["version"], "0.94.1b")

    def test_no_verified_releases_fail_without_selecting_an_unsigned_archive(self):
        for script in ("install.sh", "scripts/the333bgp.sh"):
            with self.subTest(script=script):
                result = self.normalize(script, [release("9.9b", digest=False)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
