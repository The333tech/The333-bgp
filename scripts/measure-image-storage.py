#!/usr/bin/env python3
"""CI-only measurement of exact published platform images; never run on user VMs."""

import argparse
import json
import re
import subprocess
import tarfile
from pathlib import Path


def docker(*args: str):
    return json.loads(subprocess.check_output(["docker", *args], text=True, timeout=120))


def exported_layers(reference: str) -> list[tuple[int, int]]:
    sizes, manifest = {}, None
    with subprocess.Popen(["docker", "image", "save", reference], stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for item in archive:
                allocation = 0
                if item.name == "manifest.json":
                    if item.size > 2 * 1024**2:
                        raise ValueError("export manifest is too large")
                    manifest = json.load(archive.extractfile(item))
                elif item.isfile():
                    # Docker export also contains small JSON/config blobs. Only
                    # filesystem tar streams have members to allocate on disk.
                    try:
                        with tarfile.open(fileobj=archive.extractfile(item), mode="r|") as layer:
                            for member in layer:
                                allocation += ((member.size + 4095) // 4096 + 1) * 4096
                    except tarfile.ReadError:
                        pass
                sizes[item.name] = (item.size, max(item.size, allocation))
        if process.wait(timeout=120) or not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError("cannot inspect exported image layers")
    return [sizes[name] for name in manifest[0]["Layers"]]


def measure(reference: str) -> dict:
    if not re.fullmatch(r"ghcr\.io/the333tech/the333-bgp-(?:core|backend|portal)@sha256:[a-f0-9]{64}", reference):
        raise ValueError("only immutable project image references can be measured")
    index = docker("buildx", "imagetools", "inspect", "--raw", reference)
    result = {}
    for arch in ("amd64", "arm64"):
        matches = [item for item in index["manifests"] if item.get("platform", {}).get("os") == "linux"
                   and item.get("platform", {}).get("architecture") == arch]
        if len(matches) != 1:
            raise ValueError("image must have exactly one manifest per supported platform")
        platform_ref = reference.split("@")[0] + "@" + matches[0]["digest"]
        manifest = docker("buildx", "imagetools", "inspect", "--raw", platform_ref)
        # Use the platform digest: classic overlay2 cannot retain two platform
        # variants under the same index reference in its local image store.
        subprocess.run(["docker", "pull", "--platform", f"linux/{arch}", platform_ref], check=True, timeout=300)
        info = docker("image", "inspect", platform_ref)[0]
        if info["Architecture"] != arch or info["Os"] != "linux" or not info["Size"]:
            raise ValueError("Docker inspected a different platform")
        unpacked_sizes = exported_layers(platform_ref)
        diff_ids = info["RootFS"]["Layers"]
        if len(unpacked_sizes) != len(manifest["layers"]) or len(diff_ids) != len(unpacked_sizes):
            raise ValueError("exported layers do not match the platform manifest")
        layers = [{"digest": layer["digest"], "diff_id": diff_id, "compressed_bytes": layer["size"],
                   "unpacked_bytes": unpacked[0], "allocation_bytes": unpacked[1]} for layer, diff_id, unpacked in
                  zip(manifest["layers"], diff_ids, unpacked_sizes, strict=True)]
        result[f"linux/{arch}"] = {"reference": reference, "unpacked_bytes": info["Size"],
                                   "compressed_bytes": sum(layer["size"] for layer in manifest["layers"]),
                                   "layers": layers}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("reference")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(measure(args.reference), indent=2) + "\n")
