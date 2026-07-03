import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix", "install.sh upgrade-flow harness requires a POSIX shell")
class InstallerUpgradeFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("bash") is None:
            self.skipTest("bash is not available")

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project = self.root / "opt" / "the333-bgp"
        self.source = self.root / "src"
        self.log = self.root / "harness.log"
        self.project.mkdir(parents=True)
        self.source.mkdir()

        self._create_existing_install()
        self._create_release_source()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, path: Path, content: str, mode: int | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if mode is not None:
            path.chmod(mode)

    def _copy_text(self, relative: str) -> None:
        self._write(self.source / relative, (ROOT / relative).read_text(encoding="utf-8"))

    def _create_existing_install(self) -> None:
        self._write(
            self.project / ".env",
            textwrap.dedent(
                """
                THE333_BIND_IP=192.168.56.10
                WEB_USER=admin
                WEB_PASSWORD_HASH=pbkdf2_sha256:old
                PRODUCT_VERSION=0.1
                PRODUCT_CHANNEL=stable
                HOST_UPDATER_TOKEN=legacy-token
                """
            ).lstrip(),
            0o600,
        )
        self._write(self.project / "VERSION", "0.1\n")
        self._write(self.project / "docker-compose.yml", "services: {}\n")
        self._write(self.project / "Dockerfile", "old root dockerfile\n")
        self._write(self.project / "entrypoint.sh", "old entrypoint\n")
        self._write(self.project / "app" / "updater.py", "old updater\n")
        self._write(self.project / "config" / "custom.json", '{"user": true}\n')
        self._write(self.project / "config" / "service_catalog.json", '[{"id": "legacy"}]\n')
        self._write(self.project / "data" / "state.json", '{"keep": true}\n')
        self._write(
            self.project / "scripts" / "the333bgp.sh",
            '#!/usr/bin/env bash\nprintf "old-control %s\\n" "$*" >> "${THE333_HARNESS_LOG:?}"\n',
            0o755,
        )

    def _create_release_source(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8")
        self._write(self.source / "VERSION", version)
        for relative in (
            "docker-compose.yml",
            "docker-compose.portal.yml",
            "docker-compose.tls.yml",
            "requirements.in",
            "requirements.txt",
            "CHANGELOG.md",
            "LICENSE",
            "SECURITY.md",
            "update-manifest.json",
            "update-manifest.example.json",
            ".env.example",
            ".dockerignore",
            ".gitignore",
            "install.sh",
        ):
            self._copy_text(relative)

        self._write(self.source / "app" / "main.py", "# release backend\n")
        self._write(self.source / "portal" / "package.json", '{"name": "portal"}\n')
        self._write(self.source / "docs" / "INSTALL.md", "# install\n")
        self._write(self.source / "deploy" / "systemd" / "the333-bgp-updater.service.in", "[Service]\n")
        self._write(self.source / "docker" / "gobgp-entrypoint.sh", "#!/usr/bin/env bash\n", 0o755)
        self._write(self.source / "docker" / "backend-entrypoint.sh", "#!/usr/bin/env bash\n", 0o755)
        self._write(self.source / "extras" / "docker-awg" / "README.md", "# docker-awg\n")
        self._write(self.source / "extras" / "docker-awg" / "entrypoint.sh", "#!/usr/bin/env bash\n", 0o755)
        self._write(self.source / "scripts" / "host-updater.py", "#!/usr/bin/env python3\n", 0o755)
        self._write(
            self.source / "scripts" / "the333bgp.sh",
            '#!/usr/bin/env bash\nprintf "new-control %s\\n" "$*" >> "${THE333_HARNESS_LOG:?}"\n',
            0o755,
        )
        self._write(self.source / "config" / "default_sources.json", "[]\n")
        self._write(self.source / "config" / "service_catalog.builtin.json", "[]\n")

    def _patched_installer(self) -> Path:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        installer = installer.replace(
            '\nmain "$@"\n',
            '\nif [[ "${THE333_INSTALL_TEST_HARNESS:-false}" != "true" ]]; then\n  main "$@"\nfi\n',
        )
        patched = self.root / "install-patched.sh"
        self._write(patched, installer, 0o755)
        return patched

    def test_existing_install_update_preserves_user_state_and_migrates_runtime(self) -> None:
        patched = self._patched_installer()
        harness = self.root / "run-upgrade-flow.sh"
        self._write(
            harness,
            textwrap.dedent(
                f"""
                #!/usr/bin/env bash
                set -Eeuo pipefail
                export THE333_INSTALL_TEST_HARNESS=true
                export THE333_PROJECT_DIR={self.project}
                export THE333_SOURCE_DIR={self.source}
                export THE333_HARNESS_LOG={self.log}
                source {patched}
                INSTALL_ACTION=update

                require_tty() {{ :; }}
                sudo_cmd() {{ "$@"; }}
                ensure_docker() {{ log "fake docker"; }}
                download_repo_if_needed() {{ printf '%s\\n' "${{THE333_SOURCE_DIR}}"; }}
                install_host_updater_service() {{ printf 'install_host_updater_service\\n' >> "${{THE333_HARNESS_LOG}}"; }}
                docker_compose() {{ {{ printf 'docker_compose'; printf ' %s' "$@"; printf '\\n'; }} >> "${{THE333_HARNESS_LOG}}"; }}
                wait_for_services() {{ printf 'wait_for_services %s\\n' "$*" >> "${{THE333_HARNESS_LOG}}"; }}

                existing_install_flow
                """
            ).lstrip(),
            0o755,
        )

        result = subprocess.run(
            ["bash", str(harness)],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")

        env = (self.project / ".env").read_text(encoding="utf-8")
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertIn("THE333_BIND_IP=192.168.56.10", env)
        self.assertIn("WEB_PASSWORD_HASH=pbkdf2_sha256:old", env)
        self.assertIn(f"PRODUCT_VERSION={version}", env)
        self.assertIn("PRODUCT_CHANNEL=beta", env)
        self.assertIn("PRODUCT_UPDATE_MODE=host-updater", env)
        self.assertIn("HOST_UPDATER_TOKEN=legacy-token", env)
        self.assertIn("BGP_REJECT_INBOUND_ROUTES=true", env)
        self.assertIn("SYSTEM_BACKUP_MAX_BYTES=134217728", env)

        self.assertTrue((self.project / "config" / "custom.json").exists())
        self.assertTrue((self.project / "config" / "service_catalog.json").exists())
        self.assertTrue((self.project / "config" / "service_catalog.builtin.json").exists())
        self.assertTrue((self.project / "data" / "state.json").exists())
        self.assertTrue((self.project / "extras" / "docker-awg" / "README.md").exists())
        self.assertTrue((self.project / "extras" / "docker-awg" / "entrypoint.sh").exists())
        self.assertFalse((self.project / "Dockerfile").exists())
        self.assertFalse((self.project / "entrypoint.sh").exists())
        self.assertFalse((self.project / "app" / "updater.py").exists())
        self.assertTrue(list(self.project.glob(".env.backup-before-v078-env-migration-*")))

        log = self.log.read_text(encoding="utf-8")
        self.assertIn("old-control status", log)
        self.assertIn("old-control backup", log)
        self.assertIn("install_host_updater_service", log)
        self.assertIn("docker_compose build", log)
        self.assertIn("docker_compose up -d --remove-orphans", log)
        self.assertIn("wait_for_services 192.168.56.10", log)
        self.assertIn("new-control status", log)


if __name__ == "__main__":
    unittest.main()
