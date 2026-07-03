import os
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


APP_NAME = os.getenv("APP_NAME", "The333-BGP")
PROJECT_DIR = Path(os.getenv("THE333_PROJECT_DIR", "/opt/the333-bgp")).resolve()
UPDATER_TOKEN = os.getenv("HOST_UPDATER_TOKEN", "").strip()
UPDATE_TIMEOUT_SECONDS = int(os.getenv("PRODUCT_UPDATE_TIMEOUT_SECONDS", "1800"))

app = FastAPI(title=f"{APP_NAME} Host Updater")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_token(request: Request) -> None:
    if not UPDATER_TOKEN or "CHANGE_ME" in UPDATER_TOKEN:
        raise HTTPException(status_code=503, detail="HOST_UPDATER_TOKEN is not configured")

    provided = request.headers.get("x-the333-updater-token", "")
    if not secrets.compare_digest(provided, UPDATER_TOKEN):
        raise HTTPException(status_code=403, detail="invalid updater token")


def base_command() -> list[str]:
    script = PROJECT_DIR / "scripts" / "the333bgp.sh"
    if not script.exists():
        raise RuntimeError(f"update script not found: {script}")
    return [str(script)]


def run_project_command(args: list[str]) -> dict[str, Any]:
    started = time.time()
    env = os.environ.copy()
    env["THE333_PROJECT_DIR"] = str(PROJECT_DIR)
    env["THE333_SKIP_SELF_RECREATE"] = "true"

    result = subprocess.run(
        args,
        cwd=str(PROJECT_DIR),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=UPDATE_TIMEOUT_SECONDS,
        check=False,
    )

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-6000:],
        "stderr_tail": result.stderr[-6000:],
        "duration_seconds": round(time.time() - started, 3),
        "time": now_iso(),
    }


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "app": APP_NAME,
            "role": "host-updater",
            "project_dir": str(PROJECT_DIR),
            "project_exists": PROJECT_DIR.exists(),
            "time": now_iso(),
        }
    )


@app.post("/api/update")
async def api_update(request: Request) -> JSONResponse:
    require_token(request)
    body = await request.json()

    channel = str(body.get("channel", "stable") or "stable").strip()
    version = str(body.get("version", "") or "").strip()
    if channel not in {"stable", "beta"}:
        raise HTTPException(status_code=400, detail="channel must be stable or beta")

    args = [*base_command(), "update", "--non-interactive", "--channel", channel]
    if version:
        args.extend(["--version", version])

    payload = run_project_command(args)
    payload.update({"channel": channel, "version": version or None})
    return JSONResponse(payload, status_code=200 if payload["ok"] else 500)


@app.post("/api/backup")
async def api_backup(request: Request) -> JSONResponse:
    require_token(request)
    payload = run_project_command([*base_command(), "backup"])
    return JSONResponse(payload, status_code=200 if payload["ok"] else 500)


@app.get("/api/status")
async def api_status(request: Request) -> JSONResponse:
    require_token(request)
    payload = run_project_command([*base_command(), "status"])
    return JSONResponse(payload, status_code=200 if payload["ok"] else 500)
