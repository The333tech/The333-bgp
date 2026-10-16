import copy
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release-image-matrix.py"
MODULE = runpy.run_path(str(SCRIPT))


class PublishedImageScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = {
            "product": "The333-BGP",
            "versions": [{
                "version": "1.2b",
                "images": {
                    name: f"{repository}@sha256:{'a' * 64}"
                    for name, repository in MODULE["REPOSITORIES"].items()
                },
            }],
        }

    def test_scans_each_immutable_image_on_both_architectures(self) -> None:
        rows = MODULE["image_matrix"](self.manifest, "1.2b")["include"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(
            {(row["service"], row["platform"]) for row in rows},
            {(service, f"linux/{architecture}")
             for service in ("core", "backend", "portal")
             for architecture in ("amd64", "arm64")},
        )

    def test_rejects_missing_and_duplicate_releases(self) -> None:
        for versions in ([], self.manifest["versions"] * 2, [{"version": "1.1b"}], None):
            with self.subTest(versions=versions), self.assertRaises(ValueError):
                MODULE["image_matrix"]({**self.manifest, "versions": versions}, "1.2b")

    def test_rejects_other_products_and_invalid_versions(self) -> None:
        for value in (None, [], {**self.manifest, "product": "Other"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE["image_matrix"](value, "1.2b")
        for version in ("v1.2b", "1.2b\nextra", "${{ secrets.TOKEN }}", "../1.2b"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                MODULE["image_matrix"](self.manifest, version)

    def test_rejects_partial_or_unexpected_image_sets(self) -> None:
        for images in (None, {}, {"core": "test"}, {**self.manifest["versions"][0]["images"], "extra": "test"}):
            manifest = copy.deepcopy(self.manifest)
            manifest["versions"][0]["images"] = images
            with self.subTest(images=images), self.assertRaises(ValueError):
                MODULE["image_matrix"](manifest, "1.2b")

    def test_rejects_tags_other_repositories_and_malformed_digests(self) -> None:
        for reference in (
            "ghcr.io/the333tech/the333-bgp-core:latest",
            f"ghcr.io/other/the333-bgp-core@sha256:{'a' * 64}",
            f"ghcr.io/the333tech/the333-bgp-portal@sha256:{'a' * 64}",
            f"ghcr.io/the333tech/the333-bgp-core@sha256:{'a' * 63}",
            f"ghcr.io/the333tech/the333-bgp-core@sha256:{'a' * 64}\nextra",
            None,
        ):
            manifest = copy.deepcopy(self.manifest)
            manifest["versions"][0]["images"]["core"] = reference
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                MODULE["image_matrix"](manifest, "1.2b")

    def test_cli_checks_valid_invalid_missing_and_oversized_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            cases = (
                (json.dumps(self.manifest).encode(), True),
                (b"not JSON", False),
                (b" " * (MODULE["MAX_MANIFEST_BYTES"] + 1), False),
                (None, False),
            )
            for payload, valid in cases:
                if payload is None:
                    path.unlink()
                else:
                    path.write_bytes(payload)
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), str(path), "1.2b"],
                    capture_output=True, text=True, check=False,
                )
                with self.subTest(valid=valid, size=len(payload or b"")):
                    self.assertEqual(result.returncode == 0, valid)
                    if valid:
                        self.assertEqual(len(json.loads(result.stdout)["include"]), 6)
                    else:
                        self.assertEqual(result.stdout, "")
                        self.assertNotIn("Traceback", result.stderr)

    def test_workflow_is_read_only_and_preserves_failed_scan_reports(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "published-security.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("--exclude-drafts", workflow)
        self.assertIn("--limit 1", workflow)
        self.assertIn("--pattern update-manifest.json", workflow)
        self.assertIn("python3 scripts/release-image-matrix.py", workflow)
        self.assertIn("GRYPE_PLATFORM: ${{ matrix.platform }}", workflow)
        self.assertIn("severity-cutoff: high", workflow)
        self.assertIn("fail-build: true", workflow)
        self.assertIn("output-file: scan-report.json", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("path: scan-report.json", workflow)
        self.assertIn("--pattern the333-bgp.spdx.json", workflow)
        self.assertIn("path: source-scan-report.json", workflow)
        self.assertIn("Scan published source SBOM", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("packages: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("packages: write", workflow)
        self.assertNotIn("continue-on-error", workflow)


if __name__ == "__main__":
    unittest.main()
