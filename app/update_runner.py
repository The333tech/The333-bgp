"""Run the release controller independently of the browser/backend lifetime."""

import fcntl
import os
import signal
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from app.maintenance import ACTIVE_STATUSES, read_state, write_state


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_identity(project: Path) -> tuple[int, int]:
    env_path = project / ".env"
    if env_path.is_symlink():
        raise ValueError("refusing a symlinked project .env")
    fallback = env_path.stat() if env_path.exists() else project.stat()
    values: dict[str, str] = {}
    if env_path.is_file() and not env_path.is_symlink():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"PUID", "PGID"}:
                values[key] = value.strip()
    uid = values.get("PUID", os.getenv("PUID", ""))
    gid = values.get("PGID", os.getenv("PGID", ""))
    return (int(uid) if uid.isdecimal() else fallback.st_uid,
            int(gid) if gid.isdecimal() else fallback.st_gid)


def prepare_runtime(project: Path, uid: int, gid: int) -> Path:
    root = project / "runtime"
    operations = root / "operations"
    sessions = root / "sessions"
    root.mkdir(exist_ok=True, mode=0o755)
    operations.mkdir(exist_ok=True, mode=0o770)
    sessions.mkdir(exist_ok=True, mode=0o700)
    if stat.S_IMODE(root.stat().st_mode) != 0o755:
        os.chmod(root, 0o755)
    if os.geteuid() == 0:
        os.chown(operations, 0, gid)
    if stat.S_IMODE(operations.stat().st_mode) != 0o770:
        os.chmod(operations, 0o770)
    owner = sessions.stat()
    if (owner.st_uid, owner.st_gid) != (uid, gid):
        os.chown(sessions, uid, gid)
    if stat.S_IMODE(sessions.stat().st_mode) != 0o700:
        os.chmod(sessions, 0o700)
    for name in ("runner.lock", "writes.lock"):
        lock = operations / name
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o660)
        os.close(fd)
        if os.geteuid() == 0:
            os.chown(lock, 0, gid)
        if stat.S_IMODE(lock.stat().st_mode) != 0o660:
            os.chmod(lock, 0o660)
    return operations


def run_command(project: Path, arguments: list[str], request_id: str, channel: str,
                version: str, environment: dict[str, str], timeout: int,
                capture: bool = True) -> dict:
    directory = prepare_runtime(project, *runtime_identity(project))
    with (directory / "runner.lock").open("r+") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": False, "busy": True, "status": "failed"}
        previous = read_state(directory)
        if previous.get("status") in ACTIVE_STATUSES:
            # Never silently unlock a transaction whose owner died mid-install.
            return {"ok": False, "busy": True, "status": "recovery_required"}
        started = time.monotonic()
        state = {"request_id": request_id, "status": "running", "stage": "preflight",
                 "version": version, "previous_version": (project / "VERSION").read_text().strip(),
                 "channel": channel, "started_at": timestamp(), "updated_at": timestamp(),
                 "history": [], "mutated": False}
        write_state(directory, state)
        with (directory / "writes.lock").open("r+") as gate:
            deadline = time.monotonic() + 30
            while True:
                try:
                    fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        state.update(status="failed", stage="writes_busy", updated_at=timestamp())
                        write_state(directory, state)
                        return {**state, "ok": False}
                    time.sleep(0.1)
            return _execute(project, arguments, environment, timeout, capture, directory, started)


def recover_operation(project: Path, environment: dict[str, str]) -> dict:
    """Clear an orphaned gate only after the installed runtime passes its own status check."""
    directory = prepare_runtime(project, *runtime_identity(project))
    with (directory / "runner.lock").open("r+") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("update runner is still active") from exc
        state = read_state(directory)
        if state.get("status") not in ACTIVE_STATUSES:
            raise RuntimeError("there is no unfinished update to recover")
        if not state.get("request_id") or not state.get("previous_version") or (
            not state.get("version") and state.get("mutated")
        ):
            raise RuntimeError("operation metadata is incomplete; inspect the host and backup manually")
        with (directory / "writes.lock").open("r+") as gate:
            try:
                fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("another write operation is still active") from exc
            current_version = (project / "VERSION").read_text(encoding="utf-8").strip()
            if current_version not in {state["previous_version"], state.get("version")}:
                raise RuntimeError("installed version differs from both recorded versions")
            check = subprocess.run(["/bin/bash", str(project / "scripts" / "the333bgp.sh"), "status"],
                                   cwd=project, env=environment, capture_output=True, timeout=90,
                                   check=False)
            if check.returncode != 0:
                raise RuntimeError("runtime status check failed; repair or restore the installation first")
            status = "succeeded" if current_version == state["version"] else (
                "rolled_back" if state.get("mutated") else "failed")
            state.update(status=status, stage="verified_recovery", ok=status == "succeeded",
                         updated_at=timestamp(), finished_at=timestamp())
            state["history"] = (state.get("history", []) + [
                {"stage": "verified_recovery", "time": state["updated_at"]}])[-30:]
            write_state(directory, state)
            return state


def _execute(project: Path, arguments: list[str], environment: dict[str, str], timeout: int,
             capture: bool, directory: Path, started: float) -> dict:
    env = {**environment, "THE333_UPDATE_OPERATION_FILE": str(directory / "operation.json"),
           "THE333_UPDATE_RUNNER_ACTIVE": "true"}
    timed_out = False
    returncode = 1
    with tempfile.TemporaryFile(mode="w+b") as output:
        try:
            # The executable is fixed; request-controlled values are arguments only.
            process = subprocess.Popen(["/bin/bash", str(project / "scripts" / "the333bgp.sh"),
                                        *arguments], cwd=project, env=env, start_new_session=True,
                                       stdout=output if capture else None,
                                       stderr=subprocess.STDOUT if capture else None)
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                # The leader may exit while a descendant still holds resources.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        except OSError:
            returncode = 1
        output.seek(0, os.SEEK_END)
        output.seek(max(0, output.tell() - 6000))
        tail = output.read().decode("utf-8", errors="replace")
    state = read_state(directory)
    status = "succeeded" if returncode == 0 else "failed"
    if state.get("stage") == "rolled_back":
        status = "rolled_back"
    elif timed_out or (returncode != 0 and state.get("mutated")):
        status = "recovery_required"
    elif returncode != 0 and state.get("stage") in {"backup", "download"}:
        try:
            health = subprocess.run(["/bin/bash", str(project / "scripts" / "the333bgp.sh"), "status"],
                                    cwd=project, env=environment, capture_output=True,
                                    timeout=90, check=False)
            if health.returncode != 0:
                status = "recovery_required"
        except (OSError, subprocess.TimeoutExpired):
            status = "recovery_required"
    state.update(status=status, ok=status == "succeeded", returncode=returncode,
                 timeout=timed_out, finished_at=timestamp(), updated_at=timestamp(),
                 duration_seconds=round(time.monotonic() - started, 3))
    write_state(directory, state)
    return {**state, "stdout_tail": tail, "stderr_tail": ""}
