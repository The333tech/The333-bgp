#!/usr/bin/env python3
"""Destructive delivery checks, exclusively on a fresh GitHub-hosted Linux runner."""

import argparse
import functools
import hashlib
import http.cookiejar
import http.server
import json
import os
import platform
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/opt/the333-delivery-smoke")
CONTAINERS = ("the333-gobgp-core", "the333-bgp-backend", "the333-portal")
REPOSITORIES = {name: f"ghcr.io/the333tech/the333-bgp-{name}" for name in ("core", "backend", "portal")}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*args, cwd=ROOT, env=None, capture=False, check=True):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, check=False,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None, timeout=1800)
    if check and result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {args[:3]}")
    return result.stdout if capture else result.returncode


def environment_file(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if "=" in line and not line.lstrip().startswith("#"))


def set_environment(values):
    path = PROJECT / ".env"
    lines = path.read_text().splitlines()
    updated = set()
    for index, line in enumerate(lines):
        key = line.partition("=")[0]
        if key in values:
            lines[index] = f"{key}={values[key]}"
            updated.add(key)
    lines.extend(f"{key}={value}" for key, value in values.items() if key not in updated)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def validate_refs(images):
    require(set(images) == set(REPOSITORIES), "three image references are required")
    for name, repository in REPOSITORIES.items():
        require(bool(re.fullmatch(re.escape(repository) + r"@sha256:[a-f0-9]{64}", images[name])),
                f"invalid immutable reference: {name}")
    return images


def fetch(url):
    require(url.startswith("https://"), "HTTPS required")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "The333-BGP-delivery-test"}), timeout=60) as response:
        require(response.geturl().startswith("https://"), "non-HTTPS redirect")
        data = response.read(268435457)
    require(len(data) <= 268435456, "download too large")
    return data


def verified_previous(version, destination):
    require(bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)*b?", version)), "invalid previous version")
    release = json.loads(fetch(f"https://api.github.com/repos/The333tech/The333-bgp/releases/tags/v{version}"))
    assets = {item["name"]: item for item in release["assets"]}
    name = f"the333-bgp-v{version}.tar.gz"
    for filename in (name, "update-manifest.json"):
        asset = assets[filename]
        require(bool(re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("digest", ""))), "published asset digest missing")
        data = fetch(asset["browser_download_url"])
        require("sha256:" + hashlib.sha256(data).hexdigest() == asset["digest"], "published asset checksum mismatch")
        (destination / filename).write_bytes(data)
    manifest = json.loads((destination / "update-manifest.json").read_text())
    entry = next(item for item in manifest["versions"] if item["version"] == version)
    return destination / name, validate_refs(entry["images"])


def extract(archive, destination):
    run("python3", str(ROOT / "scripts/extract-release.py"), str(archive), str(destination))


def verify_release_assets(directory, images, version, destination):
    archive = directory / f"the333-bgp-v{version}.tar.gz"
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        require(name not in checksums and bool(re.fullmatch(r"[a-f0-9]{64}", digest)), "invalid release checksums")
        checksums[name] = digest
    require(set(checksums) == {archive.name, "update-manifest.json", "the333-bgp.spdx.json"}, "unexpected release assets")
    for name, digest in checksums.items():
        item = directory / name
        require(item.is_file() and not item.is_symlink(), "unsafe release asset")
        require(hashlib.sha256(item.read_bytes()).hexdigest() == digest, "release asset checksum mismatch")
    extract(archive, destination)
    require((destination / "VERSION").read_text().strip() == version, "release version mismatch")
    for manifest_path in (destination / "update-manifest.json", directory / "update-manifest.json"):
        manifest = json.loads(manifest_path.read_text())
        matches = [item for item in manifest["versions"] if item["version"] == version]
        require(len(matches) == 1 and matches[0]["images"] == images, "release images differ from tested references")
    return archive


def candidate_archive(directory, images, version, broken=False, base_archive=None):
    """Use the git archive distribution, not the developer's untracked work tree."""
    directory.mkdir()
    source = directory / "source.tar.gz"
    if base_archive is None:
        run("git", "archive", "--format=tar.gz", "--prefix=The333/", f"--output={source}", "HEAD")
    else:
        shutil.copyfile(base_archive, source)
    tree = directory / "tree"
    extract(source, tree)
    (tree / "VERSION").write_text(version + "\n")
    manifest = {"product": "The333-BGP", "latest": {"beta": version, "stable": None},
                "versions": [{"version": version, "channel": "beta", "images": images}]}
    (tree / "update-manifest.json").write_text(json.dumps(manifest))
    measurements = ROOT / "dist" / "image-digests"
    if all((measurements / f"{name}.storage.json").is_file() for name in REPOSITORIES):
        storage = {"schema": 1, "platforms": {f"linux/{arch}": {} for arch in ("amd64", "arm64")}}
        for name in REPOSITORIES:
            measured = json.loads((measurements / f"{name}.storage.json").read_text())
            for target, entries in storage["platforms"].items():
                entries[name] = measured[target]
        (tree / "docker" / "image-storage.json").write_text(json.dumps(storage))
    if broken:
        # Valid Compose, but OCI cannot start this executable; exercise a real partial rollout.
        (tree / "docker-compose.portal.yml").write_text(json.dumps({"services": {"the333-portal": {
            "image": "${THE333_PORTAL_IMAGE}", "container_name": "the333-portal",
            "networks": ["frontend"], "entrypoint": ["/__the333_delivery_missing_entrypoint"],
        }}}))
    archive = directory / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(tree, arcname=f"The333-bgp-v{version}")
    return archive


class QuietFiles(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class Api:
    def __init__(self, password):
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""
        self.csrf = self.call("/auth/login", {"password": password})["csrf_token"]

    def call(self, path, data=None, method=None, expected=200):
        request = urllib.request.Request("http://127.0.0.1:8090/backend" + path,
            data=json.dumps(data).encode() if data is not None else None, method=method,
            headers={"Content-Type": "application/json", "X-CSRF-Token": self.csrf})
        try:
            response = self.client.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            require(response.code == expected, f"API {path}: expected {expected}, got {response.code}")
            return json.load(response)

    def job(self, path, data):
        job_id = self.call(path, data)["job"]["id"]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = self.call(f"/api/jobs/{job_id}")["job"]
            if job["status"] not in {"running", "queued"}:
                require(job["status"] == "succeeded", f"job failed: {path}")
                return job["result_summary"]
            time.sleep(1)
        raise RuntimeError(f"job timeout: {path}")


class DeliverySmoke:
    def __init__(self, args):
        require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
                "only a disposable GitHub-hosted runner is allowed")
        require(platform.system() == "Linux" and platform.machine() in {"x86_64", "aarch64"}, "unsupported native host")
        self.args = args
        self.temp = Path(os.environ["RUNNER_TEMP"]).resolve() / "the333-delivery-fixture"
        require(not self.temp.exists() and not PROJECT.exists() and not PROJECT.is_symlink(), "test paths already exist")
        require(run("docker", "ps", "-aq", capture=True).strip() == "", "runner has existing containers")
        require(not Path("/etc/systemd/system/the333-bgp-updater.service").exists()
                and not Path("/run/the333-bgp").exists(), "existing updater must not be touched")
        self.temp.mkdir(mode=0o700)
        self.public = self.temp / "public"
        self.public.mkdir()
        self.owns_runtime = False
        self.ca = Path("/usr/local/share/ca-certificates/the333-delivery-test.crt")
        require(not self.ca.exists(), "test CA already exists")
        self.server = None
        self.completed = False
        self.password = "ci-delivery-" + secrets.token_hex(12)
        self.results = []

    def mark(self, name):
        self.results.append(name)
        print(f"[delivery] OK: {name}", flush=True)

    def tls_fixture(self):
        cert, key = self.temp / "ca.crt", self.temp / "ca.key"
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=The333 disposable delivery fixture", "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(key), "-out", str(cert), capture=True)
        run("sudo", "install", "-m", "0644", str(cert), str(self.ca))
        run("sudo", "update-ca-certificates", capture=True)
        handler = functools.partial(QuietFiles, directory=str(self.public))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"https://127.0.0.1:{self.server.server_port}"
        (self.public / "health.txt").write_text("delivery fixture\n")
        run("curl", "--fail", "--silent", self.url + "/health.txt", capture=True)

    def remove_runtime(self):
        if not self.owns_runtime:
            return
        for name in CONTAINERS:
            result = run("docker", "inspect", name, capture=True, check=False)
            if result:
                payload = json.loads(result)[0]
                require(payload["Config"]["Labels"].get("com.docker.compose.project.working_dir") == str(PROJECT), "foreign container")
        if (PROJECT / ".env").is_file():
            run("docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.portal.yml", "down", "--remove-orphans", cwd=PROJECT)
        unit = Path("/etc/systemd/system/the333-bgp-updater.service")
        if unit.exists():
            require(str(PROJECT) in unit.read_text(), "foreign updater unit")
            run("sudo", "systemctl", "disable", "--now", unit.name)
            run("sudo", "rm", "-f", str(unit))
            run("sudo", "systemctl", "daemon-reload")
        require(PROJECT == Path("/opt/the333-delivery-smoke") and not PROJECT.is_symlink(), "unsafe cleanup target")
        run("sudo", "rm", "-rf", str(PROJECT), "/run/the333-bgp")
        self.owns_runtime = False

    def install(self, archive):
        target = self.temp / ("install-" + secrets.token_hex(4))
        extract(archive, target)
        version = (target / "VERSION").read_text().strip()
        manifest = json.loads((target / "update-manifest.json").read_text())
        expected_images = validate_refs(next(item for item in manifest["versions"] if item["version"] == version)["images"])
        environment = {**os.environ, "THE333_PROJECT_DIR": str(PROJECT), "THE333_IMAGE_MODE": "prebuilt",
            "THE333_BIND_IP": "127.0.0.1", "LOCAL_AS": "64512", "ROUTER_ID": "127.0.0.2",
            "BGP_NEXTHOP": "127.0.0.2", "PEER_ADDRESS": "127.0.0.1", "PEER_AS": "64513",
            "BGP_COMMUNITY": "64512:500", "BGP_TTL_SECURITY_ENABLED": "false", "WEB_PASSWORD": self.password}
        self.owns_runtime = True
        run("bash", str(target / "install.sh"), "--non-interactive", env=environment)
        require(environment_file(PROJECT / ".env")["THE333_IMAGE_MODE"] == "prebuilt", "installer changed delivery mode")
        for name, role in zip(CONTAINERS, REPOSITORIES):
            item = json.loads(run("docker", "inspect", name, capture=True))[0]
            require(item["Config"]["Image"] == expected_images[role], "installer used a different image reference")
            image = json.loads(run("docker", "image", "inspect", item["Image"], capture=True))[0]
            require(image["Architecture"] == ("arm64" if platform.machine() == "aarch64" else "amd64"), "wrong image architecture")
            require(item["State"]["Health"]["Status"] == "healthy", f"unhealthy: {name}")
        if (target / "portal/src/components/useProductUpdates.ts").is_file():
            for path in ("/", "/index.html"):
                with urllib.request.urlopen("http://127.0.0.1:8090" + path, timeout=10) as response:
                    require("no-cache" in response.headers.get("Cache-Control", ""), "portal HTML can become stale")
                    require(response.headers.get("X-Frame-Options") == "DENY", "HTML cache policy lost security headers")
                    require(bool(response.headers.get("Content-Security-Policy")), "HTML cache policy lost CSP")
        return Api(self.password)

    def index(self, archive, version, corrupt=False):
        shutil.copyfile(archive, self.public / "selected.tar.gz")
        digest = "0" * 64 if corrupt else hashlib.sha256(archive.read_bytes()).hexdigest()
        (self.public / "index.json").write_text(json.dumps({"latest": {"beta": version}, "versions": [{
            "version": version, "channel": "beta", "archive_url": self.url + "/selected.tar.gz", "sha256": digest}]}))
        set_environment({"PRODUCT_UPDATE_MANIFEST_URL": self.url + "/index.json"})

    def update(self, version, expected):
        request_id = secrets.token_hex(16)
        token = environment_file(PROJECT / ".env")["HOST_UPDATER_TOKEN"]
        body = json.dumps({"channel": "beta", "version": version, "request_id": request_id}).encode()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(30)
        connection.connect("/run/the333-bgp/updater.sock")
        connection.sendall((f"POST /api/update HTTP/1.1\r\nHost: localhost\r\nx-the333-updater-token: {token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
        # Deliberately stop reading: the durable host process must own the operation.
        connection.close()
        deadline = time.monotonic() + 900
        result_path = PROJECT / "data/host-updater-results" / f"{request_id}.json"
        while time.monotonic() < deadline:
            if result_path.exists():
                result = json.loads(result_path.read_text())
                if result.get("status") not in {"running", "queued"}:
                    if result["status"] != expected:
                        tail = str(result.get("stdout_tail", ""))[-2500:].replace(self.password, "[redacted]")
                        raise RuntimeError(f"update expected {expected}, got {result.get('status')}: {tail}")
                    operation = json.loads((PROJECT / "runtime/operations/operation.json").read_text())
                    require(operation["request_id"] == request_id and operation["status"] == expected, "durable status mismatch")
                    time.sleep(4)  # successful host-updater restarts its systemd process
                    return operation
            time.sleep(1)
        raise RuntimeError("host update did not terminate")

    def check_state(self, api, version, paused=True):
        require((PROJECT / "VERSION").read_text().strip() == version, "wrong installed version")
        require(api.call("/api/product/version")["version"] == version, "backend version mismatch")
        require(api.call("/auth/session")["csrf_token"] == api.csrf, "login session was lost")
        for name, role in zip(CONTAINERS, REPOSITORIES):
            image = run("docker", "inspect", "-f", "{{.Config.Image}}", name, capture=True).strip()
            require(image == self.images[role], f"wrong updated image: {role}")
        require(json.loads((PROJECT / "config/delivery-note.json").read_text()) == {"keep": "operator data"}, "custom config changed")
        require("1.1.1.0/24" in (PROJECT / "data/last_good_prefixes.txt").read_text(), "routes were lost")
        require(api.call("/api/runtime-settings")["route_auto_update"]["enabled"] is False, "schedule changed")
        publication = api.call("/api/routes/publication")
        require(publication["mode"] == ("paused" if paused else "publishing"), "publication state changed")
        if paused:
            require(publication["peer_admin_state"] == "down", "pause not applied to GoBGP")

    def execute(self):
        images = validate_refs({name: (self.args.images_dir / f"{name}.ref").read_text().strip() for name in REPOSITORIES})
        self.images = images
        current = (ROOT / "VERSION").read_text().strip()
        if self.args.release_assets:
            clean = verify_release_assets(self.args.release_assets, images, current, self.temp / "verify-release")
        else:
            clean = candidate_archive(self.temp / "clean", images, current)
        self.install(clean)
        self.mark("clean archive installation on native " + platform.machine())
        self.remove_runtime()
        previous_dir = self.temp / "previous"
        previous_dir.mkdir()
        previous_archive, _old_images = verified_previous(self.args.previous_release, previous_dir)
        api = self.install(previous_archive)
        api.call("/api/runtime-settings", {"route_auto_update": {"enabled": False}, "automatic_backup": {"enabled": False}}, method="PUT")
        api.call("/api/sources", [{"name": "delivery-static", "type": "static", "enabled": True, "prefixes": ["1.1.1.0/24"]}], method="PUT")
        api.call("/update", {})
        (PROJECT / "config/delivery-note.json").write_text(json.dumps({"keep": "operator data"}))
        api.call("/api/routes/publication/pause", {})
        self.tls_fixture()
        # The release gate tests the exact version/archive, candidates need a newer fixture number.
        first = current if self.args.release_assets else "9000.1b"
        second = "9000.2b"
        archive = clean if self.args.release_assets else candidate_archive(self.temp / "upgrade", images, first, base_archive=clean)
        self.index(archive, first)
        self.update(first, "succeeded")
        self.check_state(api, first)
        self.mark("published previous release -> candidate; disconnected client; session/data/pause preserved")
        api.call("/api/routes/publication/resume", {})
        lookup = api.call("/api/routes/lookup", {"query": "1.1.1.42"})
        require(lookup["addresses"][0]["matches"][0]["in_gobgp_rib"] is True, "routes were not replayed")
        backup = api.job("/api/system/backups/job", {})["backup_name"]
        (PROJECT / "config/delivery-note.json").write_text('{"changed": true}')
        api.job("/api/system/restore/job", {"backup": backup, "confirmation": "ВОССТАНОВИТЬ", "apply_routes": True})
        self.check_state(api, first, paused=False)
        self.mark("ZIP backup restored and routes replayed")
        api.call("/api/routes/publication/pause", {})
        archive = candidate_archive(self.temp / "next", images, second, base_archive=clean)
        self.index(archive, second)
        old_values = environment_file(PROJECT / ".env")
        old_limits = {"UPDATE_MIN_FREE_BYTES": old_values.get("UPDATE_MIN_FREE_BYTES", "1073741824"),
                      "UPDATE_RECOMMENDED_FREE_BYTES": old_values.get("UPDATE_RECOMMENDED_FREE_BYTES", "2147483648")}
        set_environment({key: str(2**60) for key in old_limits})
        identities = {name: run("docker", "inspect", "-f", "{{.Id}} {{.State.StartedAt}}", name, capture=True) for name in CONTAINERS}
        self.update(second, "failed")
        require(all(run("docker", "inspect", "-f", "{{.Id}} {{.State.StartedAt}}", name, capture=True) == value for name, value in identities.items()), "disk failure changed runtime")
        set_environment(old_limits)
        self.check_state(api, first)
        self.mark("insufficient disk: no containers restarted")
        self.index(archive, second, corrupt=True)
        self.update(second, "failed")
        self.check_state(api, first)
        self.mark("corrupt checksum: candidate not activated")
        unavailable = {**images, "backend": REPOSITORIES["backend"] + "@sha256:" + "0" * 64}
        missing = candidate_archive(self.temp / "missing", unavailable, second, base_archive=clean)
        self.index(missing, second)
        self.update(second, "failed")
        self.check_state(api, first)
        self.mark("unavailable GHCR digest: previous runtime retained")
        broken = candidate_archive(self.temp / "broken", images, second, broken=True, base_archive=clean)
        self.index(broken, second)
        operation = self.update(second, "rolled_back")
        require(operation.get("mutated") is True, "partial rollout was not exercised")
        self.check_state(api, first)
        self.mark("OCI startup failure: real runtime/data rollback")
        self.index(archive, second)
        self.update(second, "succeeded")
        self.check_state(api, second)
        statuses = json.loads(run("sudo", "python3", "-c",
            'import json; from pathlib import Path; print(json.dumps([json.loads(p.read_text())["status"] for p in Path("/opt/the333-delivery-smoke/backups/.retention").glob("*.json")]))', capture=True))
        require("succeeded" in statuses, "rollback snapshot was not confirmed")
        self.mark("retry succeeds and rollback snapshot is confirmed")
        self.completed = True

    def close(self):
        try:
            self.remove_runtime()
        finally:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
            if self.ca.exists():
                run("sudo", "rm", "-f", str(self.ca))
                run("sudo", "update-ca-certificates", capture=True)
            (self.temp / "result.json").write_text(json.dumps({"architecture": platform.machine(), "complete": self.completed,
                                                             "passed": self.results}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--previous-release", default="0.92b")
    parser.add_argument("--release-assets", type=Path)
    args = parser.parse_args()
    smoke = DeliverySmoke(args)
    try:
        smoke.execute()
    finally:
        smoke.close()


if __name__ == "__main__":
    main()
