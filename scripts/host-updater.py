#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import re
import secrets
import socketserver
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(os.getenv("THE333_PROJECT_DIR", "/opt/the333-bgp")).resolve()
SOCKET_PATH = Path(os.getenv("HOST_UPDATER_SOCKET", "/run/the333-bgp/updater.sock"))
LOCK_PATH = Path(os.getenv("HOST_UPDATER_LOCK", "/run/the333-bgp/update.lock"))
UPDATER_TOKEN = os.getenv("HOST_UPDATER_TOKEN", "").strip()
UPDATE_TIMEOUT_SECONDS = int(os.getenv("PRODUCT_UPDATE_TIMEOUT_SECONDS", "1800"))
RESULT_DIR = Path(
    os.getenv(
        "HOST_UPDATER_RESULT_DIR",
        str(PROJECT_DIR / "data" / "host-updater-results"),
    )
).resolve()
MAX_REQUEST_BYTES = 64 * 1024
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
CHILD_ENV_PASSTHROUGH = {
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "TERM",
    "TMPDIR",
    "TZ",
}
RUNTIME_CONTAINERS = (
    ("portal", "Портал", "the333-portal"),
    ("backend", "Backend", "the333-bgp-backend"),
    ("gobgp", "GoBGP", "the333-gobgp-core"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in CHILD_ENV_PASSTHROUGH or key.startswith("LC_")
    }
    environment.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    environment.setdefault("HOME", str(PROJECT_DIR))
    environment["THE333_PROJECT_DIR"] = str(PROJECT_DIR)
    environment["THE333_HOST_UPDATER_ACTIVE"] = "true"
    return environment


def redact_output(value: str) -> str:
    secrets_to_hide = {
        item
        for key, item in os.environ.items()
        if item and any(marker in key.upper() for marker in ("PASSWORD", "TOKEN", "SECRET"))
    }
    redacted = value
    for secret_value in sorted(secrets_to_hide, key=len, reverse=True):
        redacted = redacted.replace(secret_value, "[REDACTED]")
    return redacted[-6000:]


def prepare_result_dir() -> None:
    expected_parent = (PROJECT_DIR / "data").resolve()
    try:
        RESULT_DIR.relative_to(expected_parent)
    except ValueError as exc:
        raise RuntimeError("HOST_UPDATER_RESULT_DIR must be inside project data/") from exc

    gid = int(os.getenv("PGID", "0"))
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(RESULT_DIR, 0, gid)
    os.chmod(RESULT_DIR, 0o770)


def prune_result_files(max_files: int = 100) -> None:
    candidates = sorted(
        (
            item
            for item in RESULT_DIR.glob("*.json")
            if item.is_file()
            and not item.is_symlink()
            and REQUEST_ID_RE.fullmatch(item.stem)
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in candidates[max_files:]:
        stale.unlink(missing_ok=True)


def result_path(request_id: str) -> Path:
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("invalid request_id")

    result_root = os.path.realpath(RESULT_DIR)
    candidate = os.path.normpath(os.path.join(result_root, f"{request_id}.json"))
    result_prefix = result_root.rstrip(os.sep) + os.sep
    if not candidate.startswith(result_prefix) or os.path.dirname(candidate) != result_root:
        raise ValueError("invalid result path")
    return Path(candidate)


def write_result(request_id: str, payload: dict[str, Any]) -> None:
    target = result_path(request_id)
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=".result-",
        suffix=".tmp",
        dir=str(RESULT_DIR),
    )
    temporary = Path(temporary_name)
    uid = int(os.getenv("PUID", "0"))
    gid = int(os.getenv("PGID", "0"))

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
        directory_fd = os.open(RESULT_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        prune_result_files()
    finally:
        temporary.unlink(missing_ok=True)


def update_command(channel: str, version: str) -> list[str]:
    script = (PROJECT_DIR / "scripts" / "the333bgp.sh").resolve()
    if script.parent != (PROJECT_DIR / "scripts").resolve() or not script.is_file():
        raise RuntimeError("update script is unavailable")

    command = [str(script), "update", "--non-interactive", "--channel", channel]
    if version:
        command.extend(["--version", version])
    return command


def container_runtime_status() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    containers: list[dict[str, Any]] = []
    for key, label, name in RUNTIME_CONTAINERS:
        try:
            completed = subprocess.run(
                ["docker", "inspect", name],
                cwd=str(PROJECT_DIR),
                env=public_environment(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            payload = json.loads(completed.stdout) if completed.returncode == 0 else []
            item = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else {}
            state = item.get("State") if isinstance(item.get("State"), dict) else {}
            started_at = str(state.get("StartedAt") or "") or None
            started = None
            if started_at:
                try:
                    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                except ValueError:
                    started = None
            health_payload = state.get("Health") if isinstance(state.get("Health"), dict) else {}
            running = bool(state.get("Running", False))
            containers.append(
                {
                    "key": key,
                    "label": label,
                    "name": name,
                    "exists": bool(item),
                    "status": str(state.get("Status") or ("missing" if not item else "unknown")),
                    "health": str(health_payload.get("Status") or ("running" if running else "unknown")),
                    "started_at": started_at,
                    "uptime_seconds": max(0, int((now - started).total_seconds())) if running and started else None,
                }
            )
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            containers.append(
                {
                    "key": key,
                    "label": label,
                    "name": name,
                    "exists": False,
                    "status": "unavailable",
                    "health": "unknown",
                    "started_at": None,
                    "uptime_seconds": None,
                }
            )
    return {"ok": all(item["exists"] for item in containers), "containers": containers, "time": now_iso()}


def run_update(channel: str, version: str, request_id: str) -> dict[str, Any]:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    write_result(
        request_id,
        {
            "ok": None,
            "request_id": request_id,
            "status": "running",
            "stage": "Запущено обновление на хосте",
            "channel": channel,
            "version": version or None,
            "started_at": now_iso(),
            "time": now_iso(),
        },
    )

    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result = {
                "ok": False,
                "busy": True,
                "request_id": request_id,
                "status": "failed",
                "error": "Другая операция обновления уже выполняется.",
                "time": now_iso(),
            }
            write_result(request_id, result)
            return result

        try:
            completed = subprocess.run(
                update_command(channel, version),
                cwd=str(PROJECT_DIR),
                env=public_environment(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=UPDATE_TIMEOUT_SECONDS,
                check=False,
            )
            result = {
                "ok": completed.returncode == 0,
                "request_id": request_id,
                "status": "succeeded" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
                "stdout_tail": redact_output(completed.stdout),
                "stderr_tail": redact_output(completed.stderr),
                "duration_seconds": round(time.time() - started, 3),
                "channel": channel,
                "version": version or None,
                "finished_at": now_iso(),
                "time": now_iso(),
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "ok": False,
                "request_id": request_id,
                "status": "failed",
                "timeout": True,
                "error": "Превышено время ожидания обновления.",
                "stdout_tail": redact_output(str(exc.stdout or "")),
                "stderr_tail": redact_output(str(exc.stderr or "")),
                "duration_seconds": round(time.time() - started, 3),
                "channel": channel,
                "version": version or None,
                "finished_at": now_iso(),
                "time": now_iso(),
            }
        except Exception as exc:
            print(f"[host-updater] update process failed: {type(exc).__name__}", flush=True)
            result = {
                "ok": False,
                "request_id": request_id,
                "status": "failed",
                "error": "Обновление не запущено из-за внутренней ошибки host-updater.",
                "duration_seconds": round(time.time() - started, 3),
                "channel": channel,
                "version": version or None,
                "finished_at": now_iso(),
                "time": now_iso(),
            }

    write_result(request_id, result)
    return result


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: str, handler: type[BaseHTTPRequestHandler]) -> None:
        self.restart_requested = False
        super().__init__(socket_path, handler)


class UpdaterHandler(BaseHTTPRequestHandler):
    server: UnixHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[host-updater] local {format_string % args}", flush=True)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # The backend is expected to disconnect while its own update restarts it.
            pass

    def authenticated(self) -> bool:
        provided = self.headers.get("x-the333-updater-token", "")
        return bool(UPDATER_TOKEN) and "CHANGE_ME" not in UPDATER_TOKEN and secrets.compare_digest(
            provided,
            UPDATER_TOKEN,
        )

    def read_json(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("content-length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        payload = json.loads(self.rfile.read(content_length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        return payload

    def do_GET(self) -> None:
        if self.path == "/api/runtime":
            if not self.authenticated():
                self.send_json(403, {"ok": False, "error": "access denied"})
                return
            self.send_json(200, container_runtime_status())
            return
        if self.path != "/health":
            self.send_json(404, {"ok": False, "error": "not found"})
            return
        self.send_json(
            200,
            {
                "ok": True,
                "role": "host-updater",
                "project_exists": PROJECT_DIR.is_dir(),
                "time": now_iso(),
            },
        )

    def do_POST(self) -> None:
        if self.path != "/api/update":
            self.send_json(404, {"ok": False, "error": "not found"})
            return
        if not self.authenticated():
            self.send_json(403, {"ok": False, "error": "access denied"})
            return

        try:
            payload = self.read_json()
            if set(payload) - {"channel", "version", "request_id"}:
                raise ValueError("unsupported request fields")
            channel = str(payload.get("channel", "stable") or "stable").strip()
            version = str(payload.get("version", "") or "").strip()
            request_id = str(payload.get("request_id", "") or "").strip().lower()
            if channel not in {"stable", "beta"}:
                raise ValueError("channel must be stable or beta")
            if version and not VERSION_RE.fullmatch(version):
                raise ValueError("invalid version")
            if not REQUEST_ID_RE.fullmatch(request_id):
                raise ValueError("invalid request_id")

            result = run_update(channel, version, request_id)
            status = 200 if result.get("ok") else (409 if result.get("busy") else 500)
            self.send_json(status, result)
            if result.get("ok"):
                self.server.restart_requested = True
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        except (json.JSONDecodeError, ValueError) as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            print(f"[host-updater] update failed: {type(exc).__name__}", flush=True)
            self.send_json(500, {"ok": False, "error": "Обновление не выполнено. Проверь журнал сервиса."})


def prepare_socket() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_socket():
        SOCKET_PATH.unlink()


def secure_socket_permissions() -> None:
    uid = int(os.getenv("PUID", "0"))
    gid = int(os.getenv("PGID", "0"))
    os.chown(SOCKET_PATH.parent, 0, gid)
    os.chmod(SOCKET_PATH.parent, 0o770)
    os.chown(SOCKET_PATH, uid, gid)
    os.chmod(SOCKET_PATH, 0o660)


def main() -> int:
    global SOCKET_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=str(SOCKET_PATH))
    args = parser.parse_args()

    SOCKET_PATH = Path(args.socket)
    if not UPDATER_TOKEN or "CHANGE_ME" in UPDATER_TOKEN:
        raise SystemExit("HOST_UPDATER_TOKEN is not configured")

    prepare_result_dir()
    prepare_socket()
    try:
        with UnixHTTPServer(str(SOCKET_PATH), UpdaterHandler) as server:
            secure_socket_permissions()
            print(f"[host-updater] listening on {SOCKET_PATH}", flush=True)
            server.serve_forever(poll_interval=0.5)
            return 75 if server.restart_requested else 0
    finally:
        SOCKET_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
