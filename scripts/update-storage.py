#!/usr/bin/env python3
"""Read-only host-side disk checks used before a prebuilt update changes files."""

import argparse
import json
import subprocess
import tarfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.update_storage import MIB, Plan, archive_footprint, build_plan
from app.update_failures import record_failure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("setup", "initial", "extract", "images", "backup", "activate", "install", "copied", "installed"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--build-reserve", type=int, default=0)
    parser.add_argument("--images", nargs=3)
    parser.add_argument("--operation-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--docker-command", nargs=argparse.REMAINDER, default=["docker"])
    args = parser.parse_args()
    try:
        if args.phase == "extract":
            if args.archive is None:
                raise ValueError("release archive is required")
            plan = Plan()
            plan.add(args.archive.parent, "release extraction", archive_footprint(args.archive))
            report = plan.report()
        else:
            report = build_plan(args.project, args.phase, args.docker_command, args.release, args.images, args.backup,
                                args.build_reserve)
        for device in report:
            details = ", ".join(f"{name}: {(amount + MIB - 1) // MIB} MiB"
                                for name, amount in device["components"].items() if amount)
            print(f"[the333bgp] Disk budget ({device['path']}): {device['free_bytes'] // MIB} MiB free; "
                  f"{(device['required_bytes'] + MIB - 1) // MIB} MiB required. {details}", file=sys.stderr)
        if args.json:
            print(json.dumps(report))
        failed = next((item for item in report if not item["ready"]), None)
        if failed:
            if args.operation_dir:
                record_failure(args.operation_dir, "disk_space", failed["free_bytes"], failed["required_bytes"])
            return 1
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as exc:
        print(f"[the333bgp] ERROR: cannot calculate update disk budget: {exc}", file=sys.stderr)
        if args.operation_dir:
            record_failure(args.operation_dir, "disk_check")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
