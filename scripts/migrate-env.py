#!/usr/bin/env python3
import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path


OFFICIAL_RELEASES_URL = "https://api.github.com/repos/The333tech/The333-bgp/releases?per_page=20"
LEGACY_OFFICIAL_MANIFEST_MARKERS = (
    "raw.githubusercontent.com/The333tech/The333-bgp/",
    "api.github.com/repos/The333tech/The333-bgp/releases/latest",
)
IMAGE_REPOSITORIES = {
    "THE333_GOBGP_IMAGE": "ghcr.io/the333tech/the333-bgp-core",
    "THE333_BACKEND_IMAGE": "ghcr.io/the333tech/the333-bgp-backend",
    "THE333_PORTAL_IMAGE": "ghcr.io/the333tech/the333-bgp-portal",
}


def validate_prebuilt_image_ref(key: str, value: str) -> str:
    repository = IMAGE_REPOSITORIES[key]
    pattern = rf"{re.escape(repository)}@sha256:[0-9a-f]{{64}}"
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{key} must be an immutable {repository}@sha256:<digest> reference")
    return value


def release_image_refs(project_dir: Path, version: str) -> dict[str, str]:
    manifest_path = project_dir / "update-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("release update-manifest.json is missing or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("release update-manifest.json is invalid") from exc

    matches = [item for item in manifest.get("versions", []) if str(item.get("version")) == version]
    if len(matches) != 1:
        raise ValueError(f"release manifest must contain exactly one entry for version {version}")
    images = matches[0].get("images")
    if not isinstance(images, dict):
        raise ValueError(f"release {version} has no prebuilt image manifest")

    manifest_keys = {
        "THE333_GOBGP_IMAGE": "core",
        "THE333_BACKEND_IMAGE": "backend",
        "THE333_PORTAL_IMAGE": "portal",
    }
    return {
        env_key: validate_prebuilt_image_ref(env_key, str(images.get(manifest_key, "")).strip())
        for env_key, manifest_key in manifest_keys.items()
    }


def parse_env(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def detect_peer_mode(peer_address: str) -> str:
    if not peer_address:
        return "direct"
    try:
        completed = subprocess.run(
            ["ip", "-4", "route", "get", peer_address],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return "multihop" if " via " in f" {completed.stdout.strip()} " else "direct"
    except (OSError, subprocess.SubprocessError):
        return "direct"


def write_atomic(path: Path, text: str) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to replace symlink: {path}")
    original_stat = path.stat() if path.exists() else None
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            if original_stat is not None and os.name != "nt":
                os.fchown(handle.fileno(), original_stat.st_uid, original_stat.st_gid)
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--channel", choices=("stable", "beta"), default="beta")
    parser.add_argument("--update-url", default=OFFICIAL_RELEASES_URL)
    args = parser.parse_args()

    env_path = Path(args.env).resolve()
    project_dir = Path(args.project_dir).resolve()
    if env_path.parent != project_dir or env_path.name != ".env":
        raise SystemExit("--env must point to PROJECT_DIR/.env")
    if not env_path.is_file() or env_path.is_symlink():
        raise SystemExit(".env is missing or unsafe")

    lines = env_path.read_text(encoding="utf-8").splitlines()
    values = parse_env(lines)

    image_mode = values.get("THE333_IMAGE_MODE", "source").strip().lower() or "source"
    if image_mode not in {"source", "prebuilt"}:
        raise SystemExit("THE333_IMAGE_MODE must be source or prebuilt")
    if image_mode == "prebuilt":
        try:
            image_refs = release_image_refs(project_dir, args.version)
        except ValueError as exc:
            current_version = values.get("PRODUCT_VERSION", "").strip()
            if current_version != args.version:
                raise SystemExit(str(exc)) from exc
            try:
                image_refs = {
                    key: validate_prebuilt_image_ref(key, values.get(key, "").strip())
                    for key in IMAGE_REPOSITORIES
                }
            except ValueError:
                raise SystemExit(str(exc)) from exc
    else:
        image_refs = {key: "" for key in IMAGE_REPOSITORIES}

    secret_file = project_dir / "data" / "secrets" / "bgp_tcp_md5"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_md5 = values.get("BGP_TCP_MD5_KEY", "")
    if legacy_md5:
        write_atomic(secret_file, legacy_md5 + "\n")
    elif not secret_file.exists():
        write_atomic(secret_file, "")
    os.chmod(secret_file, 0o600)

    peer_mode = values.get("BGP_PEER_MODE") or detect_peer_mode(values.get("PEER_ADDRESS", ""))
    updater_token = values.get("HOST_UPDATER_TOKEN") or secrets.token_hex(32)
    update_min_free_bytes = values.get("UPDATE_MIN_FREE_BYTES", "").strip()
    if not update_min_free_bytes or update_min_free_bytes == "2147483648":
        update_min_free_bytes = "1073741824"
    elif not update_min_free_bytes.isdigit() or int(update_min_free_bytes) <= 0:
        raise SystemExit("UPDATE_MIN_FREE_BYTES must be a positive integer")
    manifest_url = values.get("PRODUCT_UPDATE_MANIFEST_URL", "").strip()
    if not manifest_url or any(marker in manifest_url for marker in LEGACY_OFFICIAL_MANIFEST_MARKERS):
        requested_url = (args.update_url or "").strip()
        manifest_url = (
            requested_url
            if requested_url and not any(marker in requested_url for marker in LEGACY_OFFICIAL_MANIFEST_MARKERS)
            else OFFICIAL_RELEASES_URL
        )

    defaults = {
        "PRODUCT_VERSION": args.version,
        "PRODUCT_CHANNEL": args.channel,
        "THE333_BIND_IP": values.get("ROUTER_ID", "127.0.0.1"),
        "PRODUCT_UPDATE_MANIFEST_URL": manifest_url,
        "PRODUCT_UPDATE_ENABLED": "true",
        "PRODUCT_UPDATE_MODE": "host-updater",
        "PRODUCT_UPDATE_TIMEOUT_SECONDS": "1800",
        "HOST_UPDATER_SOCKET": "/run/the333-bgp/updater.sock",
        "HOST_UPDATER_RUN_DIR": "/run/the333-bgp",
        "HOST_UPDATER_RESULT_DIR": str(project_dir / "data" / "host-updater-results"),
        "HOST_UPDATER_TOKEN": updater_token,
        "UPDATE_MIN_FREE_BYTES": update_min_free_bytes,
        "AUTH_ALLOW_BASIC": "false",
        "SESSION_COOKIE_NAME": "the333_session",
        "SESSION_TTL_SECONDS": "43200",
        "SESSION_COOKIE_SECURE": "false",
        "SESSION_MAX_ACTIVE": "8",
        "PORTAL_TLS_ENABLED": "false",
        "PORTAL_TLS_CERT_PATH": "/etc/the333-bgp/tls/portal.crt",
        "PORTAL_TLS_KEY_PATH": "/etc/the333-bgp/tls/portal.key",
        "REMOTE_FETCH_MAX_BYTES": "16777216",
        "REMOTE_FETCH_MAX_REDIRECTS": "5",
        "REMOTE_FETCH_CACHE_GRACE_SECONDS": "86400",
        "GOBGP_CORE_IMAGE_VERSION": "4.7.0-r5",
        "THE333_IMAGE_MODE": image_mode,
        **image_refs,
        "BGP_PEER_MODE": peer_mode,
        "BGP_DOCKER_BRIDGE_HOPS": "1",
        "BGP_TTL_SECURITY_ENABLED": "false",
        "BGP_TTL_SECURITY_MIN": "255",
        "BGP_TCP_MD5_CONFIGURED": "true" if secret_file.stat().st_size > 0 else "false",
        "BGP_REJECT_INBOUND_ROUTES": "true",
        "GOBGP_RECOVERY_POLL_SECONDS": "5",
        "SYSTEM_BACKUP_RETENTION": "20",
        "SYSTEM_BACKUP_MAX_BYTES": "134217728",
    }
    always_managed = {
        "PRODUCT_VERSION",
        "PRODUCT_CHANNEL",
        "PRODUCT_UPDATE_MANIFEST_URL",
        "BGP_TCP_MD5_CONFIGURED",
        "GOBGP_CORE_IMAGE_VERSION",
        "THE333_IMAGE_MODE",
        "THE333_GOBGP_IMAGE",
        "THE333_BACKEND_IMAGE",
        "THE333_PORTAL_IMAGE",
        "HOST_UPDATER_RESULT_DIR",
        "UPDATE_MIN_FREE_BYTES",
    }

    updated: list[str] = []
    seen: set[str] = set()
    removed_legacy_key = False
    last_assignment_index: dict[str, int] = {}
    for index, line in enumerate(lines):
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, _value = line.split("=", 1)
            last_assignment_index[key] = index

    for index, line in enumerate(lines):
        if not line or line.lstrip().startswith("#") or "=" not in line:
            updated.append(line)
            continue
        key, _value = line.split("=", 1)
        if key == "BGP_TCP_MD5_KEY":
            removed_legacy_key = True
            continue
        if last_assignment_index.get(key) != index:
            continue
        if key in always_managed:
            updated.append(f"{key}={defaults[key]}")
            seen.add(key)
        else:
            updated.append(line)
            seen.add(key)

    missing = [key for key in defaults if key not in seen]
    if missing:
        updated.extend(["", "# Product/runtime defaults managed by The333-BGP migration."])
        updated.extend(f"{key}={defaults[key]}" for key in missing)

    write_atomic(env_path, "\n".join(updated) + "\n")
    print(json.dumps({
        "ok": True,
        "version": args.version,
        "added": missing,
        "legacy_md5_migrated": bool(legacy_md5),
        "legacy_md5_key_removed": removed_legacy_key,
        "peer_mode": peer_mode,
        "image_mode": image_mode,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
