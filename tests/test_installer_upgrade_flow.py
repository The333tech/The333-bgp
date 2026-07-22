import hashlib
import os
import shutil
import subprocess
import tarfile
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

    def _patched_update_controller(self) -> Path:
        controller = (ROOT / "scripts" / "the333bgp.sh").read_text(encoding="utf-8")
        controller = controller.replace(
            '\nmain "$@"\n',
            '\nif [[ "${THE333_UPDATE_TEST_HARNESS:-false}" != "true" ]]; then\n  main "$@"\nfi\n',
        )
        patched = self.root / "the333bgp-patched.sh"
        self._write(patched, controller, 0o755)
        return patched

    def _environment_with_failing_curl(self) -> dict[str, str]:
        bin_dir = self.root / "bin"
        self._write(
            bin_dir / "curl",
            '#!/usr/bin/env sh\necho "simulated curl failure" >&2\nexit 77\n',
            0o755,
        )
        environment = os.environ.copy()
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
        return environment

    def _environment_with_recording_curl(self) -> tuple[dict[str, str], Path]:
        bin_dir = self.root / "recording-bin"
        request_log = self.root / "curl-url.log"
        self._write(
            bin_dir / "curl",
            textwrap.dedent(
                """
                #!/usr/bin/env bash
                set -Eeuo pipefail
                output=""
                url=""
                while [[ $# -gt 0 ]]; do
                  case "$1" in
                    --output)
                      output="$2"
                      shift 2
                      ;;
                    https://*)
                      url="$1"
                      shift
                      ;;
                    *)
                      shift
                      ;;
                  esac
                done
                printf '%s\\n' "${url}" > "${THE333_CURL_URL_LOG:?}"
                cat > "${output:?}" <<'JSON'
                {
                  "latest": {"stable": null, "beta": "0.82b"},
                  "versions": [
                    {
                      "version": "0.82b",
                      "channel": "beta",
                      "archive_url": "https://release.example/the333-bgp-v0.82b.tar.gz",
                      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
                    }
                  ]
                }
                JSON
                """
            ).lstrip(),
            0o755,
        )
        environment = os.environ.copy()
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
        environment["THE333_CURL_URL_LOG"] = str(request_log)
        return environment, request_log

    def test_existing_install_update_bootstraps_verified_release_controller(self) -> None:
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
        self.assertIn("THE333_BIND_IP=192.168.56.10", env)
        self.assertIn("WEB_PASSWORD_HASH=pbkdf2_sha256:old", env)
        self.assertIn("PRODUCT_VERSION=0.1", env)
        self.assertIn("PRODUCT_CHANNEL=stable", env)
        self.assertIn("HOST_UPDATER_TOKEN=legacy-token", env)

        self.assertTrue((self.project / "config" / "custom.json").exists())
        self.assertTrue((self.project / "config" / "service_catalog.json").exists())
        self.assertTrue((self.project / "data" / "state.json").exists())
        self.assertTrue((self.project / "Dockerfile").exists())
        self.assertTrue((self.project / "entrypoint.sh").exists())
        self.assertTrue((self.project / "app" / "updater.py").exists())

        log = self.log.read_text(encoding="utf-8")
        self.assertIn("old-control status", log)
        self.assertIn("new-control update --manifest", log)
        self.assertIn("--channel beta --non-interactive", log)
        self.assertNotIn("old-control update", log)

    def test_update_controller_reports_manifest_download_failure_without_python_traceback(self) -> None:
        environment = self._environment_with_failing_curl()
        environment["THE333_PROJECT_DIR"] = str(self.root / "empty-project")
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "the333bgp.sh"),
                "check-update",
                "--manifest",
                "https://release.example/manifest.json",
                "--channel",
                "beta",
            ],
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("update manifest download failed", result.stderr)
        self.assertNotIn("JSONDecodeError", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_download_release_returns_only_the_extracted_directory(self) -> None:
        archive_root = self.root / "archive-root" / "The333-bgp-v0.82b"
        self._write(archive_root / "app" / "main.py", "# release backend\n")
        archive = self.root / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            handle.add(archive_root, arcname="The333-bgp-v0.82b")

        self._copy_text("scripts/extract-release.py")
        archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        environment = os.environ.copy()
        environment.update(
            {
                "THE333_PROJECT_DIR": str(self.source),
                "THE333_RELEASE_ARCHIVE": str(archive),
                "THE333_UPDATE_TEST_HARNESS": "true",
            }
        )
        bin_dir = self.root / "archive-bin"
        self._write(
            bin_dir / "curl",
            textwrap.dedent(
                """
                #!/usr/bin/env bash
                set -Eeuo pipefail
                output=""
                while [[ $# -gt 0 ]]; do
                  if [[ "$1" == "--output" ]]; then
                    output="$2"
                    shift 2
                  else
                    shift
                  fi
                done
                cp "${THE333_RELEASE_ARCHIVE:?}" "${output:?}"
                """
            ).lstrip(),
            0o755,
        )
        environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
        version_json = (
            '{"archive_url":"https://release.example/the333-bgp-v0.82b.tar.gz",'
            f'"sha256":"{archive_sha256}"}}'
        )
        patched = self._patched_update_controller()

        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; download_release "$2"',
                "bash",
                str(patched),
                version_json,
            ],
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        output_lines = [line for line in result.stdout.splitlines() if line]
        self.assertEqual(len(output_lines), 1, msg=result.stdout)
        extracted = Path(output_lines[0])
        self.assertTrue((extracted / "app" / "main.py").is_file())

    def test_update_controller_prefers_current_project_env_over_stale_parent_env(self) -> None:
        environment, request_log = self._environment_with_recording_curl()
        environment.update(
            {
                "THE333_PROJECT_DIR": str(self.project),
                "PRODUCT_UPDATE_MANIFEST_URL": "https://stale.example/manifest.json",
            }
        )
        with (self.project / ".env").open("a", encoding="utf-8") as handle:
            handle.write("PRODUCT_UPDATE_MANIFEST_URL=https://current.example/manifest.json\n")

        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "the333bgp.sh"),
                "check-update",
                "--channel",
                "beta",
            ],
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        self.assertEqual(request_log.read_text(encoding="utf-8").strip(), "https://current.example/manifest.json")

    def test_explicit_manifest_still_overrides_current_project_env(self) -> None:
        environment, request_log = self._environment_with_recording_curl()
        environment["THE333_PROJECT_DIR"] = str(self.project)
        with (self.project / ".env").open("a", encoding="utf-8") as handle:
            handle.write("PRODUCT_UPDATE_MANIFEST_URL=https://current.example/manifest.json\n")

        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "the333bgp.sh"),
                "check-update",
                "--manifest",
                "https://explicit.example/manifest.json",
                "--channel",
                "beta",
            ],
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        self.assertEqual(request_log.read_text(encoding="utf-8").strip(), "https://explicit.example/manifest.json")

    def test_bootstrap_installer_reports_index_download_failure_without_python_traceback(self) -> None:
        patched = self._patched_installer()
        environment = self._environment_with_failing_curl()
        environment.update(
            {
                "THE333_INSTALL_TEST_HARNESS": "true",
                "PRODUCT_UPDATE_MANIFEST_URL": "https://release.example/index.json",
            }
        )
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; download_repo_if_needed', "bash", str(patched)],
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release index download failed", result.stderr)
        self.assertNotIn("JSONDecodeError", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def _run_update_backup_harness(
        self,
        *,
        archive_failure: bool = False,
        archive_signal: str = "",
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        patched = self._patched_update_controller()
        scenario = archive_signal.lower() if archive_signal else ("failure" if archive_failure else "success")
        event_log = self.root / f"backup-{scenario}.log"
        harness = self.root / f"run-backup-{scenario}.sh"
        self._write(
            harness,
            textwrap.dedent(
                """
                #!/usr/bin/env bash
                set -Eeuo pipefail
                export THE333_UPDATE_TEST_HARNESS=true
                source "$1"
                BACKUP_DIR="$2"
                event_log="$3"

                docker_cli() {
                  case "$1" in
                    container)
                      printf 'container-inspect\n' >> "${event_log}"
                      return 0
                      ;;
                    inspect)
                      printf 'running-inspect\n' >> "${event_log}"
                      printf 'true\n'
                      return 0
                      ;;
                    stop|start)
                      printf '%s\n' "$1" >> "${event_log}"
                      return 0
                      ;;
                    *)
                      return 0
                      ;;
                  esac
                }
                create_update_backup_archive() {
                  printf 'archive\n' >> "${event_log}"
                  if [[ "${THE333_ARCHIVE_SIGNAL:-}" == "TERM" ]]; then
                    kill -TERM $$
                  fi
                  if [[ "${THE333_ARCHIVE_FAIL:-false}" == "true" ]]; then
                    return 42
                  fi
                  printf 'verified fixture\n' > "$1"
                }
                wait_backend_after_backup() {
                  printf 'wait\n' >> "${event_log}"
                }
                verify_update_backup_archive() {
                  printf 'verify\n' >> "${event_log}"
                  [[ -s "$1" ]]
                }

                make_backup
                """
            ).lstrip(),
            0o755,
        )
        environment = os.environ.copy()
        environment["THE333_ARCHIVE_FAIL"] = "true" if archive_failure else "false"
        environment["THE333_ARCHIVE_SIGNAL"] = archive_signal
        result = subprocess.run(
            ["bash", str(harness), str(patched), str(self.root / "update-backups"), str(event_log)],
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        events = event_log.read_text(encoding="utf-8").splitlines() if event_log.exists() else []
        return result, events

    def test_update_backup_pauses_and_restores_running_backend(self) -> None:
        result, events = self._run_update_backup_harness()
        self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        self.assertEqual(
            events,
            ["container-inspect", "running-inspect", "stop", "archive", "start", "wait", "verify"],
        )

    def test_update_backup_restores_backend_after_archive_failure(self) -> None:
        result, events = self._run_update_backup_harness(archive_failure=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            events,
            ["container-inspect", "running-inspect", "stop", "archive", "start", "wait"],
        )
        self.assertIn("backend was restored and the update was not started", result.stderr)

    def test_update_backup_restores_backend_when_process_is_terminated(self) -> None:
        result, events = self._run_update_backup_harness(archive_signal="TERM")
        self.assertEqual(
            result.returncode,
            143,
            msg=f"events={events}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertEqual(
            events,
            ["container-inspect", "running-inspect", "stop", "archive", "start"],
        )
        self.assertIn("Backup interrupted by TERM", result.stderr)


if __name__ == "__main__":
    unittest.main()
