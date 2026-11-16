"""Bound system resolver calls without leaving timed-out threads behind."""

import ipaddress
import json
import math
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


_WORKER_PATH = Path(__file__).resolve()
_WORKERS = threading.BoundedSemaphore(4)
_COLLECTION_DEADLINE: ContextVar[float | None] = ContextVar("dns_collection_deadline", default=None)


class DnsBudgetExceeded(TimeoutError):
    pass


def positive_seconds(value: str | float, name: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise ValueError(f"{name} must be finite and greater than 0, at most 3600 seconds")
    return seconds


@contextmanager
def collection_budget(seconds: float) -> Iterator[None]:
    if _COLLECTION_DEADLINE.get() is not None:
        yield
        return
    token = _COLLECTION_DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _COLLECTION_DEADLINE.reset(token)


def provider_deadline(seconds: float) -> float:
    deadline = time.monotonic() + seconds
    collection_deadline = _COLLECTION_DEADLINE.get()
    return min(deadline, collection_deadline) if collection_deadline is not None else deadline


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise DnsBudgetExceeded("DNS collection budget exceeded")
    return seconds


def bounded_sleep(seconds: float, deadline: float) -> None:
    time.sleep(min(max(0, seconds), remaining(deadline)))
    remaining(deadline)


def resolve_ipv4(domain: str, timeout: float) -> list[str]:
    deadline = time.monotonic() + positive_seconds(timeout, "DNS timeout")
    if not _WORKERS.acquire(timeout=remaining(deadline)):
        raise TimeoutError("DNS worker unavailable within deadline")
    try:
        # Only this disposable process enters getaddrinfo. run() kills and reaps
        # it on timeout; no application state or inherited pipe is passed in.
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(_WORKER_PATH), domain],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                timeout=remaining(deadline),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError("DNS lookup timed out") from None
        remaining(deadline)
    finally:
        _WORKERS.release()
    if completed.returncode != 0 or len(completed.stdout) > 128 * 1024:
        raise OSError("DNS worker failed")
    try:
        payload = json.loads(completed.stdout)
        if "gaierror" in payload:
            raise socket.gaierror(int(payload["gaierror"]), "DNS lookup failed")
        addresses = payload["addresses"]
        if not isinstance(addresses, list) or len(addresses) > 4096:
            raise ValueError("invalid DNS response")
        return sorted({str(ipaddress.IPv4Address(address)) for address in addresses})
    except (KeyError, TypeError, ValueError):
        raise OSError("Invalid DNS worker response") from None


def _main() -> None:
    try:
        results = socket.getaddrinfo(sys.argv[1], None, socket.AF_INET, socket.SOCK_STREAM)
        addresses = sorted({item[4][0] for item in results})
        payload = {"addresses": addresses} if len(addresses) <= 4096 else {"error": "too_many_addresses"}
    except socket.gaierror as exc:
        payload = {"gaierror": exc.errno}
    except (OSError, UnicodeError, ValueError, IndexError):
        payload = {"error": "lookup_failed"}
    print(json.dumps(payload))


if __name__ == "__main__":
    _main()
