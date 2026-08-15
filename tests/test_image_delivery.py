import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATOR = ROOT / "scripts" / "migrate-env.py"


def image_refs(digest_character: str = "a") -> dict[str, str]:
    digest = digest_character * 64
    return {
        "core": f"ghcr.io/the333tech/the333-bgp-core@sha256:{digest}",
        "backend": f"ghcr.io/the333tech/the333-bgp-backend@sha256:{digest}",
        "portal": f"ghcr.io/the333tech/the333-bgp-portal@sha256:{digest}",
    }


class ImageDeliveryTests(unittest.TestCase):
    def _run_migration(self, project: Path, version: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(MIGRATOR),
                "--env",
                str(project / ".env"),
                "--project-dir",
                str(project),
                "--version",
                version,
                "--channel",
                "beta",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _project(self, root: Path, mode: str, version: str = "1.0") -> Path:
        project = root / "project"
        (project / "data" / "secrets").mkdir(parents=True)
        (project / ".env").write_text(
            "\n".join(
                [
                    f"PRODUCT_VERSION={version}",
                    "PRODUCT_CHANNEL=beta",
                    f"THE333_IMAGE_MODE={mode}",
                    "THE333_GOBGP_IMAGE=",
                    "THE333_BACKEND_IMAGE=",
                    "THE333_PORTAL_IMAGE=",
                    "ROUTER_ID=192.0.2.10",
                    "PEER_ADDRESS=192.0.2.1",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return project

    def test_compose_keeps_source_build_and_accepts_immutable_images(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        portal = (ROOT / "docker-compose.portal.yml").read_text(encoding="utf-8")
        self.assertIn("${THE333_GOBGP_IMAGE:-the333-bgp-core:", compose)
        self.assertIn("${THE333_BACKEND_IMAGE:-the333-bgp-backend:", compose)
        self.assertIn("${THE333_PORTAL_IMAGE:-the333-bgp-portal:", portal)
        self.assertEqual(compose.count("build:"), 2)
        self.assertEqual(portal.count("build:"), 1)

    def test_source_migration_clears_stale_prebuilt_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(Path(directory), "source")
            refs = image_refs()
            env_path = project / ".env"
            text = env_path.read_text(encoding="utf-8")
            text = text.replace("THE333_GOBGP_IMAGE=", f"THE333_GOBGP_IMAGE={refs['core']}")
            text = text.replace("THE333_BACKEND_IMAGE=", f"THE333_BACKEND_IMAGE={refs['backend']}")
            text = text.replace("THE333_PORTAL_IMAGE=", f"THE333_PORTAL_IMAGE={refs['portal']}")
            env_path.write_text(text, encoding="utf-8")

            result = self._run_migration(project, "1.1")
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            migrated = env_path.read_text(encoding="utf-8")
            self.assertIn("THE333_IMAGE_MODE=source", migrated)
            self.assertIn("THE333_GOBGP_IMAGE=\n", migrated)
            self.assertIn("THE333_BACKEND_IMAGE=\n", migrated)
            self.assertIn("THE333_PORTAL_IMAGE=\n", migrated)

    def test_prebuilt_migration_uses_new_release_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(Path(directory), "prebuilt")
            old_refs = image_refs("a")
            env_path = project / ".env"
            text = env_path.read_text(encoding="utf-8")
            text = text.replace("THE333_GOBGP_IMAGE=", f"THE333_GOBGP_IMAGE={old_refs['core']}")
            text = text.replace("THE333_BACKEND_IMAGE=", f"THE333_BACKEND_IMAGE={old_refs['backend']}")
            text = text.replace("THE333_PORTAL_IMAGE=", f"THE333_PORTAL_IMAGE={old_refs['portal']}")
            env_path.write_text(text, encoding="utf-8")

            new_refs = image_refs("b")
            (project / "update-manifest.json").write_text(
                json.dumps({"versions": [{"version": "1.1", "images": new_refs}]}),
                encoding="utf-8",
            )
            result = self._run_migration(project, "1.1")
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            migrated = env_path.read_text(encoding="utf-8")
            for reference in new_refs.values():
                self.assertIn(reference, migrated)
            for reference in old_refs.values():
                self.assertNotIn(reference, migrated)

    def test_prebuilt_migration_rejects_wrong_repository_without_modifying_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(Path(directory), "prebuilt")
            before = (project / ".env").read_text(encoding="utf-8")
            refs = image_refs()
            refs["backend"] = refs["backend"].replace("the333-bgp-backend", "untrusted/backend")
            (project / "update-manifest.json").write_text(
                json.dumps({"versions": [{"version": "1.1", "images": refs}]}),
                encoding="utf-8",
            )
            result = self._run_migration(project, "1.1")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("immutable", result.stderr)
            self.assertEqual((project / ".env").read_text(encoding="utf-8"), before)

    def test_installer_and_updater_never_silently_fallback(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        updater = (ROOT / "scripts" / "the333bgp.sh").read_text(encoding="utf-8")
        self.assertIn("THE333_IMAGE_MODE must be source or prebuilt", installer)
        self.assertIn("docker_compose pull the333-gobgp-core", installer)
        self.assertIn("docker_compose up -d --no-build", installer)
        self.assertIn("compose pull the333-gobgp-core", updater)
        self.assertIn("compose up -d --no-build", updater)
        self.assertIn("scripts/the333bgp.sh image-mode source", updater)
        self.assertNotIn("pull the333-gobgp-core || docker_compose build", installer)
        self.assertNotIn("pull the333-gobgp-core || compose build", updater)

    def test_installed_env_is_authoritative_for_installer_image_mode(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        function = installer.split("runtime_image_mode() {", 1)[1].split("\n}\n", 1)[0]
        env_read = function.index('if [[ -f "${PROJECT_DIR}/.env" ]]')
        process_fallback = function.index('${THE333_IMAGE_MODE:-source}')
        self.assertLess(env_read, process_fallback)

    def test_mode_switch_does_not_restart_runtime_after_preflight_failure(self) -> None:
        updater = (ROOT / "scripts" / "the333bgp.sh").read_text(encoding="utf-8")
        function = updater.split("set_image_mode() {", 1)[1].split("\n}\n", 1)[0]
        preflight = function.split("if ! check_update_disk_space; then", 1)[1].split("fi", 1)[0]
        preparation = function.split("if ! build_update_images strict; then", 1)[1].split("fi", 1)[0]
        self.assertIn('restore_image_mode_env "${backup_env}"', preflight)
        self.assertIn('restore_image_mode_env "${backup_env}"', preparation)
        self.assertNotIn("build_and_restart", preflight)
        self.assertNotIn("build_and_restart", preparation)

    def test_ghcr_workflows_are_multiarch_scanned_and_attested(self) -> None:
        candidate = (ROOT / ".github" / "workflows" / "ghcr-candidate.yml").read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        for workflow in (candidate, release):
            self.assertIn("packages: write", workflow)
            self.assertIn("linux/amd64,linux/arm64", workflow)
            self.assertIn("provenance: mode=max", workflow)
            self.assertIn("sbom: true", workflow)
            self.assertIn("only-fixed: true", workflow)
            self.assertIn("push-to-registry: true", workflow)
        self.assertIn("candidate-${{ github.sha }}", candidate)
        self.assertIn("workflow_call:", candidate)
        self.assertIn("build-ghcr-candidate", ci)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", ci)
        self.assertIn("uses: ./.github/workflows/ghcr-candidate.yml", ci)
        self.assertIn('matches[0]["images"] = images', release)
        self.assertIn("@sha256:[0-9a-f]", release)

    def test_multiarch_builder_cross_compiles_and_candidate_is_clean_installed(self) -> None:
        backend = (ROOT / "docker" / "backend.Dockerfile").read_text(encoding="utf-8")
        core = (ROOT / "docker" / "gobgp.Dockerfile").read_text(encoding="utf-8")
        builder = (ROOT / "docker" / "build-gobgp.sh").read_text(encoding="utf-8")
        candidate = (ROOT / ".github" / "workflows" / "ghcr-candidate.yml").read_text(encoding="utf-8")

        for dockerfile in (backend, core):
            self.assertIn("FROM --platform=$BUILDPLATFORM golang:", dockerfile)
            self.assertIn("ARG TARGETOS", dockerfile)
            self.assertIn("ARG TARGETARCH", dockerfile)
        self.assertIn('GOOS="${target_os}" GOARCH="${target_arch}"', builder)
        self.assertIn("Smoke-test clean prebuilt install", candidate)
        self.assertIn("bash ./install.sh --non-interactive", candidate)
        self.assertIn("{{.Config.Image}}", candidate)
        self.assertIn("THE333_IMAGE_MODE", candidate)


if __name__ == "__main__":
    unittest.main()
