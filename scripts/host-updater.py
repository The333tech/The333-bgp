#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import re
import secrets
import socketserver
import subprocess
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
MAX_REQUEST_BYTES = 64 * 1024
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_environment() -> dict[str, str]:
    environment = os.environ.copy()
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


def update_command(channel: str, version: str) -> list[str]:
    script = (PROJECT_DIR / "scripts" / "the333bgp.sh").resolve()
    if script.parent != (PROJECT_DIR / "scripts").resolve() or not script.is_file():
        raise RuntimeError("update script is unavailable")

    command = [str(script), "update", "--non-interactive", "--channel", channel]
    if version:
        command.extend(["--version", version])
    return command


def run_update(channel: str, version: str) -> dict[str, Any]:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()

    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "ok": False,
                "busy": True,
                "error": "Другая операция обновления уже выполняется.",
                "time": now_iso(),
            }

        result = subprocess.run(
            update_command(channel, version),
            cwd=str(PROJECT_DIR),
            env=public_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=UPDATE_TIMEOUT_SECONDS,
            check=False,
        )

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": redact_output(result.stdout),
        "stderr_tail": redact_output(result.stderr),
        "duration_seconds": round(time.time() - started, 3),
        "channel": channel,
        "version": version or None,
        "time": now_iso(),
    }


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
        self.wfile.write(body)
        self.wfile.flush()

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
            if set(payload) - {"channel", "version"}:
                raise ValueError("unsupported request fields")
            channel = str(payload.get("channel", "stable") or "stable").strip()
            version = str(payload.get("version", "") or "").strip()
            if channel not in {"stable", "beta"}:
                raise ValueError("channel must be stable or beta")
            if version and not VERSION_RE.fullmatch(version):
                raise ValueError("invalid version")

            result = run_update(channel, version)
            status = 200 if result.get("ok") else (409 if result.get("busy") else 500)
            self.send_json(status, result)
            if result.get("ok"):
                self.server.restart_requested = True
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        except (json.JSONDecodeError, ValueError) as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except subprocess.TimeoutExpired:
            self.send_json(504, {"ok": False, "error": "Превышено время ожидания обновления."})
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
