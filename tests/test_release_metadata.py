import json
import re
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

    def test_product_version_is_synchronized_across_release_files(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        manifest = json.loads((ROOT / "update-manifest.json").read_text(encoding="utf-8"))
        package = json.loads((ROOT / "portal" / "package.json").read_text(encoding="utf-8"))
        package_lock = json.loads((ROOT / "portal" / "package-lock.json").read_text(encoding="utf-8"))
        matching = next(item for item in manifest["versions"] if str(item.get("version")) == version)
        channel = str(matching["channel"])
        beta_match = re.fullmatch(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?b", version)
        if beta_match:
            major, minor, patch = beta_match.groups()
            package_version = f"{major}.{minor}.{patch or '0'}-beta"
        else:
            package_version = f"{version}.0" if version.count(".") == 1 else version

        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        install_doc = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        app = (ROOT / "portal" / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertIn(f"PRODUCT_VERSION={version}", env_example)
        self.assertIn(f"PRODUCT_CHANNEL={channel}", env_example)
        self.assertIn("product_version=\"$(tr -d '[:space:]' < \"${PROJECT_DIR}/VERSION\"", installer)
        self.assertIn("PRODUCT_CHANNEL=${INSTALL_CHANNEL}", installer)
        self.assertIn("ensure_env_defaults", installer)
        self.assertIn("fallback_backup_existing_install", installer)
        self.assertIn("HOST_UPDATER_TOKEN", installer)
        self.assertIn("HOST_UPDATER_RESULT_DIR=${PROJECT_DIR}/data/host-updater-results", installer)
        self.assertIn("--action update|repair|backup|status|quit", installer)
        self.assertIn("THE333_INSTALL_ACTION=update", installer)
        self.assertIn("docker deploy extras requirements.in", installer)
        self.assertNotIn('"${control}" update', installer)
        self.assertIn(f"Version-v{version}", readme)
        self.assertIn("github.com/The333tech/The333-bgp/actions/workflows/ci.yml/badge.svg", readme)
        self.assertIn("github.com/The333tech/The333-bgp/actions/workflows/release.yml/badge.svg", readme)
        self.assertIn("github.com/The333tech/The333-bgp/actions/workflows/codeql.yml/badge.svg", readme)
        self.assertNotIn("img.shields.io/github/actions/workflow/status", readme)
        self.assertIn(f"v{version} (beta)", readme)
        self.assertIn(f"Версия: **v{version} (beta)**", install_doc)
        self.assertIn(f"**v{version} (beta)**", security)
        self.assertIn(f'const PRODUCT_VERSION = "{version}";', app)
        self.assertEqual(package["version"], package_version)
        self.assertEqual(package_lock["version"], package_version)
        self.assertEqual(package_lock["packages"][""]["version"], package_version)

    def test_release_urls_use_https(self) -> None:
        manifest = json.loads((ROOT / "update-manifest.json").read_text(encoding="utf-8"))
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        controller = (ROOT / "scripts" / "the333bgp.sh").read_text(encoding="utf-8")

        for item in manifest.get("versions", []):
            with self.subTest(version=item.get("version")):
                archive_url = str(item.get("archive_url", ""))
                self.assertEqual(urlparse(archive_url).scheme, "https")
        self.assertIn('matches[0]["sha256"] = sha256', release)
        self.assertIn("selected version has no valid sha256; refusing update", controller)
        for download_path in (installer, controller):
            self.assertIn("--proto '=https'", download_path)
            self.assertIn("--proto-redir '=https'", download_path)
            self.assertIn("--max-filesize 2097152", download_path)
            self.assertIn("--max-filesize 268435456", download_path)

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

    def test_docker_socket_is_not_exposed_to_containers(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        portal_compose = (ROOT / "docker-compose.portal.yml").read_text(encoding="utf-8")

        self.assertNotIn("/var/run/docker.sock", compose)
        self.assertNotIn("the333-host-updater:", compose)
        self.assertIn("HOST_UPDATER_SOCKET: /run/the333-bgp/updater.sock", compose)
        self.assertIn(":/run/the333-bgp:ro", compose)
        self.assertIn("internal: true", compose)
        self.assertNotIn('BGP_TCP_MD5_KEY: "${BGP_TCP_MD5_KEY', compose)
        self.assertIn("./data/secrets/bgp_tcp_md5:/run/secrets/bgp_tcp_md5:ro", compose)
        self.assertIn('BGP_TTL_SECURITY_ENABLED: "${BGP_TTL_SECURITY_ENABLED:-false}"', compose)
        self.assertIn('BGP_DOCKER_BRIDGE_HOPS: "${BGP_DOCKER_BRIDGE_HOPS:-1}"', compose)
        self.assertIn("condition: service_healthy", portal_compose)

        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        entrypoint = (ROOT / "docker" / "gobgp-entrypoint.sh").read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("BGP_TTL_SECURITY_ENABLED=false", env_example)
        self.assertIn("BGP_DOCKER_BRIDGE_HOPS=1", env_example)
        self.assertIn('elif [ "${BGP_TTL_SECURITY_ENABLED}" = "true" ]; then', entrypoint)
        self.assertIn('direct_transport_ttl=$((BGP_DOCKER_BRIDGE_HOPS + 1))', entrypoint)
        self.assertIn("GTSM requires matching TTL security on MikroTik", installer)

    def test_auth_proxy_config_values_are_not_logged_raw(self) -> None:
        app = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

        self.assertIn("invalid AUTH_TRUSTED_PROXY_CIDRS entry ignored at position", app)
        self.assertNotIn("invalid AUTH_TRUSTED_PROXY_CIDRS entry ignored: %s", app)

    def test_runtime_images_are_split_by_role(self) -> None:
        backend = (ROOT / "docker" / "backend.Dockerfile").read_text(encoding="utf-8")
        gobgp = (ROOT / "docker" / "gobgp.Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        core_service = compose.split("  the333-bgp-backend:", 1)[0]

        self.assertFalse((ROOT / "Dockerfile").exists())
        self.assertNotIn("docker-cli", backend)
        self.assertNotIn("gobgpd /usr/local/bin/gobgpd", backend)
        self.assertNotIn("python", gobgp.lower())
        self.assertNotIn("VCS_REF", core_service)
        self.assertIn('org.opencontainers.image.revision="${GOBGP_REF}"', gobgp)
        self.assertIn("GOBGP_CORE_IMAGE_VERSION=4.7.0-r5", gobgp)
        portal_dockerfile = (ROOT / "portal" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("apk upgrade --no-cache", portal_dockerfile)
        self.assertIn("user[[:space:]]", portal_dockerfile)

    def test_python_dependencies_are_hash_locked(self) -> None:
        direct = [
            line.strip()
            for line in (ROOT / "requirements.in").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        lock = (ROOT / "requirements.txt").read_text(encoding="utf-8")

        self.assertGreaterEqual(lock.count("--hash=sha256:"), 10)
        self.assertNotIn("--index-url", lock)
        self.assertNotIn("--trusted-host", lock)
        for requirement in direct:
            with self.subTest(requirement=requirement):
                self.assertIn(f"{requirement} \\\n", lock)

    def test_docker_base_images_are_digest_pinned(self) -> None:
        dockerfiles = [
            ROOT / "docker" / "backend.Dockerfile",
            ROOT / "docker" / "gobgp.Dockerfile",
            ROOT / "portal" / "Dockerfile",
        ]

        for dockerfile in dockerfiles:
            for line in dockerfile.read_text(encoding="utf-8").splitlines():
                if line.startswith("FROM "):
                    with self.subTest(dockerfile=dockerfile.name, line=line):
                        self.assertRegex(line, r"@sha256:[0-9a-f]{64}(?:\s|$)")

    def test_dependabot_covers_every_dockerfile_directory(self) -> None:
        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

        for directory in ("/docker", "/portal", "/extras/docker-awg"):
            with self.subTest(directory=directory):
                self.assertIn(f"directory: {directory}", dependabot)
        self.assertNotRegex(
            dependabot,
            r"(?m)^\s*- package-ecosystem: docker\s+directory: /\s*$",
        )

    def test_runtime_images_have_oci_identity_labels(self) -> None:
        dockerfiles = [
            ROOT / "docker" / "backend.Dockerfile",
            ROOT / "docker" / "gobgp.Dockerfile",
            ROOT / "portal" / "Dockerfile",
        ]
        required_labels = [
            "org.opencontainers.image.title",
            "org.opencontainers.image.source",
            "org.opencontainers.image.licenses",
            "org.opencontainers.image.version",
            "org.opencontainers.image.revision",
        ]

        for dockerfile in dockerfiles:
            content = dockerfile.read_text(encoding="utf-8")
            for label in required_labels:
                with self.subTest(dockerfile=dockerfile.name, label=label):
                    self.assertIn(label, content)

    def test_gobgp_build_is_commit_pinned_and_uses_patched_modules(self) -> None:
        build_script = (ROOT / "docker" / "build-gobgp.sh").read_text(encoding="utf-8")
        dockerfiles = [
            (ROOT / "docker" / "backend.Dockerfile").read_text(encoding="utf-8"),
            (ROOT / "docker" / "gobgp.Dockerfile").read_text(encoding="utf-8"),
        ]
        expected_values = [
            "GOBGP_TAG_REF=982fa664245fcd0dac3c8c408205bb2198b2cad3",
            "GOBGP_REF=8b5edc2c55cbec9e7df33123a07811a119d44542",
            "GOBGP_X_NET_VERSION=v0.56.0",
            "GOBGP_X_SYS_VERSION=v0.46.0",
            "GOBGP_X_TEXT_VERSION=v0.39.0",
            "GOBGP_GRPC_VERSION=v1.82.1",
        ]

        for dockerfile in dockerfiles:
            for value in expected_values:
                with self.subTest(value=value):
                    self.assertIn(value, dockerfile)
            self.assertIn("COPY docker/build-gobgp.sh", dockerfile)
            self.assertNotIn("go install github.com/osrg/gobgp", dockerfile)

        self.assertIn('rev-parse "refs/tags/${GOBGP_VERSION}"', build_script)
        self.assertIn('test "$(git -C /src/gobgp rev-parse HEAD)" = "${GOBGP_REF}"', build_script)
        self.assertIn('"golang.org/x/net@${GOBGP_X_NET_VERSION}"', build_script)
        self.assertIn('"google.golang.org/grpc@${GOBGP_GRPC_VERSION}"', build_script)
        self.assertIn("go mod verify", build_script)
        self.assertIn("-mod=readonly", build_script)

    def test_grype_exception_is_narrow_and_version_scoped(self) -> None:
        config = (ROOT / ".grype.yaml").read_text(encoding="utf-8")

        self.assertEqual(config.count("- vulnerability:"), 1)
        self.assertIn("vulnerability: CVE-2026-15308", config)
        self.assertIn("name: python", config)
        self.assertIn("version: 3.14.6", config)
        self.assertIn("type: binary", config)
        self.assertIn("neither imports nor", config)

    def test_github_actions_are_pinned_and_release_is_attested(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        for workflow in workflow_dir.glob("*.yml"):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                match = re.search(r"\buses:\s*[^@\s]+@([^\s#]+)", line)
                if match:
                    with self.subTest(workflow=workflow.name, action=line.strip()):
                        self.assertRegex(match.group(1), r"^[0-9a-f]{40}$")

        release = (workflow_dir / "release.yml").read_text(encoding="utf-8")
        release_check = (ROOT / "scripts" / "release-check.sh").read_text(encoding="utf-8")
        self.assertIn("Generate SPDX SBOM", release)
        self.assertIn("Attest release provenance", release)
        self.assertIn("Attest release SBOM", release)
        self.assertIn("Scan release SBOM", release)
        self.assertIn("the333-bgp.spdx.json", release)
        self.assertIn("Scan release SBOM for fixable High/Critical vulnerabilities", release)
        self.assertIn("GRYPE_VERSION: v0.112.0", release)
        self.assertIn("severity-cutoff: high", release)
        self.assertIn("grype-version: ${{ env.GRYPE_VERSION }}", release)
        self.assertIn("output-format: table", release)
        self.assertIn('if gh release view "${tag}"', release)
        self.assertIn("Published release assets are immutable", release)
        self.assertNotIn("--clobber", release)
        self.assertNotIn('gh release edit "${tag}"', release)
        self.assertIn('gh release create "${tag}" "${assets[@]}"', release)
        self.assertIn('release_state=(--prerelease)', release)
        self.assertIn('release_state=(--latest)', release)
        self.assertNotIn('title="${title} beta"', release)
        self.assertIn("THE333_REQUIRE_TRACKED_RELEASE_FILES=true ./scripts/release-check.sh", release)
        self.assertIn("THE333_REQUIRE_TRACKED_RELEASE_FILES", release_check)
        self.assertIn("source_required_files=(", release_check)
        self.assertIn("THE333_STRICT_SOURCE_TREE", release_check)
        self.assertIn(".github/workflows/release.yml", release_check)
        self.assertIn("tests/test_installer_upgrade_flow.py", release_check)
        self.assertIn("required release file is not tracked by Git", release_check)
        self.assertIn("git check-attr export-ignore", release_check)
        self.assertIn("excluded from git archive by export-ignore", release_check)
        self.assertIn("detect_python()", release_check)
        self.assertIn('PYTHON_BIN="$(detect_python)"', release_check)

        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("/.github export-ignore", attributes)
        self.assertIn("/tests export-ignore", attributes)
        self.assertNotIn("/.gitattributes export-ignore", attributes)
        self.assertNotIn("/.gitignore export-ignore", attributes)

        ci = (workflow_dir / "ci.yml").read_text(encoding="utf-8")
        self.assertEqual(ci.count("anchore/scan-action@"), 3)
        self.assertEqual(ci.count("only-fixed: true"), 3)
        self.assertEqual(ci.count("severity-cutoff: high"), 3)
        self.assertEqual(ci.count("grype-version: ${{ env.GRYPE_VERSION }}"), 3)
        self.assertEqual(ci.count("output-format: table"), 3)
        self.assertIn("GRYPE_VERSION: v0.112.0", ci)
        self.assertNotIn("severity-cutoff: critical", ci)

        codeql = (workflow_dir / "codeql.yml").read_text(encoding="utf-8")
        self.assertIn("build-mode: none", codeql)
        self.assertNotIn("github.event.repository.private == false", codeql)
        self.assertNotIn("github/codeql-action/autobuild@", codeql)

    def test_host_updater_uses_hardened_unix_socket_service(self) -> None:
        unit = (ROOT / "deploy" / "systemd" / "the333-bgp-updater.service.in").read_text(encoding="utf-8")
        controller = (ROOT / "scripts" / "the333bgp.sh").read_text(encoding="utf-8")

        self.assertIn("HOST_UPDATER_SOCKET=/run/the333-bgp/updater.sock", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("RestrictNamespaces=true", unit)
        self.assertIn("SuccessExitStatus=75", unit)
        self.assertIn("scripts/the333bgp.sh install-updater-service", controller)
        self.assertIn("стабильный systemd unit будет переиспользован", controller)
        self.assertIn('$1 == "PGID"', controller)
        self.assertIn('install-updater-service)', controller)
        self.assertIn("Legacy updater container detected", controller)
        self.assertIn("the333-host-updater", controller)

        install_doc = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
        self.assertIn("Переход с ранних установок", install_doc)
        self.assertIn("./scripts/the333bgp.sh install-updater-service", install_doc)

    def test_install_and_update_have_realistic_disk_preflight(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        controller = (ROOT / "scripts" / "the333bgp.sh").read_text(encoding="utf-8")

        self.assertIn("MIN_FREE_DISK_KB=8388608", installer)
        self.assertIn("RECOMMENDED_FREE_DISK_GB=12", installer)
        self.assertIn("DEFAULT_MIN_UPDATE_FREE_BYTES=2147483648", controller)
        self.assertIn("check_update_disk_space", controller)
        self.assertIn("build_update_images", controller)
        self.assertIn("compose build the333-bgp-backend the333-portal", controller)
        self.assertIn('"${PROJECT_DIR}/scripts/the333bgp.sh"', controller)
        self.assertIn("chmod +x", controller)
        self.assertLess(
            controller.index("check_update_disk_space", controller.index("update_project()")),
            controller.index("make_backup", controller.index("update_project()")),
        )

    def test_optional_tls_overlay_keeps_private_key_outside_project(self) -> None:
        overlay = (ROOT / "docker-compose.tls.yml").read_text(encoding="utf-8")
        nginx = (ROOT / "portal" / "nginx-tls.conf").read_text(encoding="utf-8")

        self.assertIn('SESSION_COOKIE_SECURE: "true"', overlay)
        self.assertIn("/etc/the333-bgp/tls/portal.key", overlay)
        self.assertIn("https://127.0.0.1:8443/", overlay)
        self.assertIn("ssl_protocols TLSv1.2 TLSv1.3", nginx)
        self.assertIn("proxy_set_header Authorization $http_authorization", nginx)
        self.assertIn("ssl_session_tickets off", nginx)
        self.assertFalse((ROOT / "config" / "tls" / "portal.key").exists())

    def test_docker_awg_candidate_is_pinned_and_contains_no_configuration(self) -> None:
        dockerfile = (ROOT / "extras" / "docker-awg" / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (ROOT / "extras" / "docker-awg" / "entrypoint.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "docker-awg.yml").read_text(encoding="utf-8")
        controller = (ROOT / "scripts" / "the333bgp.sh").read_text(encoding="utf-8")
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("AWG_GO_REF=1cc94272ca8e9e223a5fe76382f5880f09d3c12d", dockerfile)
        self.assertIn("AWG_TOOLS_REF=61e741780e8465a67a7d7fb6cffe14a8a15d624a", dockerfile)
        self.assertIn("AWG_X_CRYPTO_VERSION=v0.53.0", dockerfile)
        self.assertIn("AWG_X_NET_VERSION=v0.56.0", dockerfile)
        self.assertIn("AWG_X_SYS_VERSION=v0.46.0", dockerfile)
        self.assertIn("go mod verify", dockerfile)
        self.assertEqual(dockerfile.count("@sha256:"), 2)
        self.assertNotIn("COPY awg0.conf", dockerfile)
        self.assertNotIn("cat \"${config}\"", entrypoint)
        self.assertIn("linux/arm/v7", workflow)
        self.assertIn("--self-test", workflow)
        self.assertIn("Generate image SPDX SBOM", workflow)
        self.assertIn("Scan candidate for fixable High/Critical vulnerabilities", workflow)
        self.assertIn("GRYPE_VERSION: v0.112.0", workflow)
        self.assertIn("severity-cutoff: high", workflow)
        self.assertIn("grype-version: ${{ env.GRYPE_VERSION }}", workflow)
        self.assertIn("output-format: table", workflow)
        self.assertNotIn("severity-cutoff: critical", workflow)
        self.assertIn("docker deploy extras requirements.in", installer)
        self.assertIn("docker deploy extras requirements.in", controller)

    def test_backup_validation_uses_allowlisted_name_before_path_access(self) -> None:
        backend = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

        self.assertIn("def validate_system_backup_zip_name", backend)
        self.assertIn("backup_path = safe_system_backup_path(name)", backend)
        self.assertIn("def validate_system_backup_zip_path", backend)
        self.assertNotIn("validate_system_backup_zip(backup_path)", backend)


if __name__ == "__main__":
    unittest.main()
