#!/usr/bin/env python3
"""Read APT's selected transaction and budget it before package installation."""

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.update_storage import Plan


def transaction_budget(packages: list[str]) -> list[dict]:
    if not packages or any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", name) for name in packages):
        raise ValueError("invalid package names")
    environment = dict(os.environ, LC_ALL="C")
    simulated = subprocess.check_output(
        ["apt-get", "--simulate", "--no-install-recommends", "install", *packages],
        env=environment, text=True, timeout=120,
    )
    installed = compressed = 0
    for line in simulated.splitlines():
        if not line.startswith("Inst "):
            continue
        match = re.match(r"Inst ([^ ]+) (?:\[[^]]+\] )?\(([^ ]+) ", line)
        if not match:
            raise ValueError("cannot parse selected APT transaction")
        name, version = match.groups()
        metadata = subprocess.check_output(["apt-cache", "show", "--no-all-versions", f"{name}={version}"],
                                           env=environment, text=True, timeout=30)
        fields = dict(line.split(": ", 1) for line in metadata.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Version") != version:
            raise ValueError("APT metadata does not match selected package version")
        if any(not fields.get(key, "").isdigit() for key in ("Installed-Size", "Size")):
            raise ValueError("APT package sizes are missing or invalid")
        installed += int(fields["Installed-Size"]) * 1024
        compressed += int(fields["Size"])
    plan = Plan()
    # Do not assume old package files or downloaded archives can be removed.
    plan.add(Path("/"), "package installation", installed + installed // 5)
    plan.add(Path("/var/cache/apt/archives"), "package downloads", compressed)
    return plan.report()


if __name__ == "__main__":
    import json
    try:
        report = transaction_budget(sys.argv[1:])
        for item in report:
            print("[install] APT disk budget: " + json.dumps(item), file=sys.stderr)
        raise SystemExit(0 if all(item["ready"] for item in report) else 1)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"[install] ERROR: cannot verify package disk budget: {exc}", file=sys.stderr)
        raise SystemExit(1)
