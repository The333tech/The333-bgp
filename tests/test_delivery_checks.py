import argparse
import hashlib
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from delivery_smoke import DeliverySmoke, REPOSITORIES, candidate_archive, extract, fetch, validate_refs, verify_release_assets


class DeliveryCheckTests(unittest.TestCase):
    def test_destructive_test_is_disabled_on_normal_hosts(self):
        for environment in ({}, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                with patch("delivery_smoke.run") as command, self.assertRaisesRegex(RuntimeError, "disposable GitHub-hosted"):
                    DeliverySmoke(argparse.Namespace())
                command.assert_not_called()

    def test_only_complete_immutable_project_images_are_allowed(self):
        refs = {name: repository + "@sha256:" + "a" * 64 for name, repository in REPOSITORIES.items()}
        self.assertEqual(validate_refs(refs), refs)
        for wrong in ({}, {**refs, "backend": "untrusted/backend:latest"}, {**refs, "portal": refs["portal"].replace("a" * 64, "a" * 63)}):
            with self.assertRaises(RuntimeError):
                validate_refs(wrong)

    def test_download_helper_refuses_plaintext_without_network_request(self):
        with patch("urllib.request.urlopen") as request, self.assertRaisesRegex(RuntimeError, "HTTPS"):
            fetch("http://localhost/release.tar.gz")
        request.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "archive helper invokes host python3")
    def test_fixture_changes_are_confined_to_temporary_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "base"
            original.mkdir()
            (original / "VERSION").write_text("0.92b\n")
            (original / "keep.txt").write_text("distribution content\n")
            archive = root / "base.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                output.add(original, arcname="The333")
            refs = {name: repository + "@sha256:" + "a" * 64 for name, repository in REPOSITORIES.items()}
            candidate = candidate_archive(root / "candidate", refs, "9000.2b", broken=True, base_archive=archive)
            result = root / "result"
            extract(candidate, result)
            self.assertEqual((result / "VERSION").read_text(), "9000.2b\n")
            self.assertEqual((original / "VERSION").read_text(), "0.92b\n")
            self.assertEqual((result / "keep.txt").read_text(), "distribution content\n")
            self.assertEqual(json.loads((result / "update-manifest.json").read_text())["versions"][0]["images"], refs)
            self.assertEqual(json.loads((result / "docker-compose.portal.yml").read_text())["services"]["the333-portal"]["entrypoint"], ["/__the333_delivery_missing_entrypoint"])

    @unittest.skipUnless(os.name == "posix", "archive helper invokes host python3")
    def test_release_gate_checks_all_assets_and_uses_the_exact_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            version = "0.93b"
            refs = {name: repository + "@sha256:" + "a" * 64 for name, repository in REPOSITORIES.items()}
            manifest = json.dumps({"versions": [{"version": version, "images": refs}]})
            (source / "VERSION").write_text(version)
            (source / "update-manifest.json").write_text(manifest)
            assets = root / "assets"
            assets.mkdir()
            archive = assets / f"the333-bgp-v{version}.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                output.add(source, arcname="release")
            (assets / "update-manifest.json").write_text(manifest)
            (assets / "the333-bgp.spdx.json").write_text("{}")
            sums = assets / "SHA256SUMS"
            sums.write_text("\n".join(f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.name}" for item in sorted(assets.iterdir())))
            self.assertEqual(verify_release_assets(assets, refs, version, root / "verified"), archive)
            wrong_refs = {**refs, "core": refs["core"].replace("a" * 64, "b" * 64)}
            with self.assertRaisesRegex(RuntimeError, "images differ"):
                verify_release_assets(assets, wrong_refs, version, root / "wrong-images")
            (assets / "the333-bgp.spdx.json").write_text("corrupt")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                verify_release_assets(assets, refs, version, root / "wrong-checksum")
