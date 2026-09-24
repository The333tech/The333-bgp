"""Host-owned update state and a cross-process gate for configuration writes."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Development without a configured host gate, e.g. Windows.
    fcntl = None

ACTIVE_STATUSES = {"running", "recovery_required"}


class MaintenanceBusy(RuntimeError):
    pass


def read_state(directory: Path | None) -> dict:
    if directory is None:
        return {}
    try:
        value = json.loads((directory / "operation.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("status") not in {
            "running", "succeeded", "failed", "rolled_back", "recovery_required"
        }:
            raise ValueError("invalid update state")
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"status": "recovery_required", "stage": "state_unavailable"}


def write_state(directory: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".operation-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(name, directory / "operation.json")
        if hasattr(os, "O_DIRECTORY"):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def mutation_lease(directory: Path | None):
    if directory is None:
        yield
        return
    if fcntl is None:
        raise MaintenanceBusy("host write gate is unavailable")
    try:
        handle = (directory / "writes.lock").open("rb")
    except OSError as exc:
        raise MaintenanceBusy("host write gate is unavailable") from exc
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MaintenanceBusy("update is active") from exc
        try:
            if read_state(directory).get("status") in ACTIVE_STATUSES:
                raise MaintenanceBusy("update is active or needs recovery")
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
