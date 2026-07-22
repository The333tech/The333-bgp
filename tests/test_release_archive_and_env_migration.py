import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EnvMigrationTests(unittest.TestCase):
    def test_migration_preserves_user_settings_and_moves_md5_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "the333-bgp"
            project.mkdir()
            env_path = project / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "PRODUCT_VERSION=0.1",
                        "PRODUCT_VERSION=0.78",
                        "PRODUCT_CHANNEL=stable",
                        "PRODUCT_UPDATE_MANIFEST_URL=https://raw.githubusercontent.com/The333tech/The333-bgp/main/update-manifest.json",
                        "PORTAL_TLS_ENABLED=true",
                        "SESSION_COOKIE_SECURE=true",
                        "ROUTER_ID=192.168.1.10",
                        "THE333_BIND_IP=192.168.1.20",
                        "PEER_ADDRESS=192.168.1.1",
                        "GOBGP_CORE_IMAGE_VERSION=4.7.0-r2",
                        "BGP_DOCKER_BRIDGE_HOPS=2",
                        "BGP_TTL_SECURITY_ENABLED=true",
                        "BGP_TCP_MD5_KEY=private-test-key",
                        "HOST_UPDATER_TOKEN=existing-token",
                        "HOST_UPDATER_RESULT_DIR=/data/host-updater-results",
                        "CUSTOM_SETTING=old-value",
                        "CUSTOM_SETTING=keep-me",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                str(ROOT / "scripts" / "migrate-env.py"),
                "--env",
                str(env_path),
                "--project-dir",
                str(project),
                "--version",
                "0.82b",
                "--channel",
                "beta",
                "--update-url",
                "https://raw.githubusercontent.com/The333tech/The333-bgp/main/update-manifest.json",
            ]
            first = subprocess.run(command, text=True, capture_output=True, check=False)
            second = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertEqual(second.returncode, 0, msg=second.stderr)

            migrated = env_path.read_text(encoding="utf-8")
            self.assertIn("PRODUCT_VERSION=0.82b", migrated)
            self.assertIn("PRODUCT_CHANNEL=beta", migrated)
            self.assertIn(
                "PRODUCT_UPDATE_MANIFEST_URL=https://api.github.com/repos/The333tech/The333-bgp/releases?per_page=20",
                migrated,
            )
            self.assertIn("PORTAL_TLS_ENABLED=true", migrated)
            self.assertIn("SESSION_COOKIE_SECURE=true", migrated)
            self.assertIn("THE333_BIND_IP=192.168.1.20", migrated)
            self.assertIn("CUSTOM_SETTING=keep-me", migrated)
            self.assertNotIn("CUSTOM_SETTING=old-value", migrated)
            self.assertEqual(migrated.count("CUSTOM_SETTING="), 1)
            self.assertIn("HOST_UPDATER_TOKEN=existing-token", migrated)
            self.assertIn(
                f"HOST_UPDATER_RESULT_DIR={project / 'data' / 'host-updater-results'}",
                migrated,
            )
            self.assertIn("BGP_TCP_MD5_CONFIGURED=true", migrated)
            self.assertIn("GOBGP_CORE_IMAGE_VERSION=4.7.0-r4", migrated)
            self.assertIn("BGP_DOCKER_BRIDGE_HOPS=2", migrated)
            self.assertIn("BGP_TTL_SECURITY_ENABLED=true", migrated)
            self.assertNotIn("BGP_TCP_MD5_KEY=", migrated)
            self.assertEqual(migrated.count("PRODUCT_VERSION="), 1)

            secret = project / "data" / "secrets" / "bgp_tcp_md5"
            self.assertEqual(secret.read_text(encoding="utf-8"), "private-test-key\n")
            if os.name == "posix":
                self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_migration_defaults_gtsm_to_compatible_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "the333-bgp"
            project.mkdir()
            env_path = project / ".env"
            env_path.write_text(
                "ROUTER_ID=192.168.1.10\nPEER_ADDRESS=192.168.1.1\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "migrate-env.py"),
                    "--env",
                    str(env_path),
                    "--project-dir",
                    str(project),
                    "--version",
                    "0.82b",
                    "--channel",
                    "beta",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            migrated = env_path.read_text(encoding="utf-8")
            self.assertIn("THE333_BIND_IP=192.168.1.10", migrated)
            self.assertIn("BGP_TTL_SECURITY_ENABLED=false", migrated)
            self.assertIn("BGP_DOCKER_BRIDGE_HOPS=1", migrated)
            self.assertIn("GOBGP_CORE_IMAGE_VERSION=4.7.0-r4", migrated)

    @unittest.skipUnless(os.name == "posix", "ownership semantics require POSIX")
    def test_migration_preserves_env_owner_and_group_with_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "the333-bgp"
            project.mkdir()
            env_path = project / ".env"
            env_path.write_text(
                "ROUTER_ID=192.168.1.10\nPEER_ADDRESS=192.168.1.1\n",
                encoding="utf-8",
            )
            os.chmod(env_path, 0o600)
            if os.geteuid() == 0:
                os.chown(env_path, 65534, 65534)
            before = env_path.stat()

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "migrate-env.py"),
                    "--env",
                    str(env_path),
                    "--project-dir",
                    str(project),
                    "--version",
                    "0.82b",
                    "--channel",
                    "beta",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            after = env_path.stat()
            self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
            self.assertEqual(after.st_mode & 0o777, 0o600)


class ReleaseArchiveExtractionTests(unittest.TestCase):
    def run_extractor(self, archive: Path, destination: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "extract-release.py"), str(archive), str(destination)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_safe_archive_is_extracted_without_top_level_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "release.tar.gz"
            destination = root / "out"
            with tarfile.open(archive, "w:gz") as handle:
                data = b"0.82b\n"
                member = tarfile.TarInfo("The333-bgp-v0.82b/VERSION")
                member.size = len(data)
                member.mode = 0o644
                handle.addfile(member, io.BytesIO(data))
            result = self.run_extractor(archive, destination)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual((destination / "VERSION").read_text(encoding="utf-8"), "0.82b\n")

    def test_path_traversal_and_links_are_rejected(self) -> None:
        for member_name, link_target in (
            ("The333-bgp-v0.82b/../../escape", None),
            ("The333-bgp-v0.82b/link", "/etc/passwd"),
        ):
            with self.subTest(member=member_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                archive = root / "release.tar.gz"
                destination = root / "out"
                with tarfile.open(archive, "w:gz") as handle:
                    member = tarfile.TarInfo(member_name)
                    if link_target is None:
                        data = b"bad"
                        member.size = len(data)
                        handle.addfile(member, io.BytesIO(data))
                    else:
                        member.type = tarfile.SYMTYPE
                        member.linkname = link_target
                        handle.addfile(member)
                result = self.run_extractor(archive, destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
