#!/usr/bin/env python3
"""Validate published image references before passing them to CI scan jobs."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
REPOSITORIES = {
    "core": "ghcr.io/the333tech/the333-bgp-core",
    "backend": "ghcr.io/the333tech/the333-bgp-backend",
    "portal": "ghcr.io/the333tech/the333-bgp-portal",
}


def image_matrix(manifest: Any, version: str) -> dict[str, list[dict[str, str]]]:
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?b?", version):
        raise ValueError("invalid release version")
    if not isinstance(manifest, dict) or manifest.get("product") != "The333-BGP":
        raise ValueError("unexpected manifest product")
    versions = manifest.get("versions")
    if not isinstance(versions, list):
        raise ValueError("manifest versions must be a list")
    matches = [item for item in versions if isinstance(item, dict) and item.get("version") == version]
    if len(matches) != 1:
        raise ValueError("manifest must contain exactly one matching release")
    images = matches[0].get("images")
    if not isinstance(images, dict) or set(images) != set(REPOSITORIES):
        raise ValueError("manifest must contain core, backend and portal images")

    rows = []
    for service, repository in REPOSITORIES.items():
        reference = images[service]
        if not isinstance(reference, str) or not re.fullmatch(
            rf"{re.escape(repository)}@sha256:[0-9a-f]{{64}}", reference
        ):
            raise ValueError(f"invalid immutable image reference: {service}")
        for architecture in ("amd64", "arm64"):
            rows.append({
                "service": service,
                "image": reference,
                "architecture": architecture,
                "platform": f"linux/{architecture}",
            })
    return {"include": rows}


def latest_release_tag(releases: Any) -> str:
    if not isinstance(releases, list):
        raise ValueError("release list must be an array")
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get("isDraft"):
            continue
        tag = str(release.get("tagName") or "")
        match = re.fullmatch(r"v(\d+)\.(\d+)(?:\.(\d+))?(b)?", tag)
        if match:
            candidates.append(((int(match[1]), int(match[2]), int(match[3] or 0), -1 if match[4] else 0), tag))
    if not candidates:
        raise ValueError("no supported published release tag found")
    return max(candidates)[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("version", nargs="?")
    parser.add_argument("--latest-tag", action="store_true")
    args = parser.parse_args()
    if not args.latest_tag and (args.manifest is None or args.version is None):
        parser.error("manifest and version are required")
    try:
        if args.latest_tag:
            raw = sys.stdin.buffer.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                raise ValueError("release list exceeds size limit")
            print(latest_release_tag(json.loads(raw)))
            return 0
        with args.manifest.open("rb") as source:
            raw = source.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest exceeds size limit")
        result = image_matrix(json.loads(raw), args.version)
    except (OSError, ValueError) as exc:
        print(f"[release-image-matrix] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
