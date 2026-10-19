import argparse
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from delivery_smoke import DeliverySmoke, REPOSITORIES, candidate_archive, extract, fetch, validate_refs


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
