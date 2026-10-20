"""Phase-specific update budgets. No deletion, pulling or mutation happens here."""

from __future__ import annotations

import json
import hashlib
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MIB = 1024**2
RESERVE = 128 * MIB
LEGACY_IMAGE_BUDGET = 1024 * MIB
REPOSITORIES = {name: f"ghcr.io/the333tech/the333-bgp-{name}" for name in ("core", "backend", "portal")}
BACKUP_ITEMS = (
    ".env", "VERSION", "CHANGELOG.md", "README.md", "docker-compose.yml",
    "docker-compose.portal.yml", "docker-compose.tls.yml", "requirements.in", "requirements.txt",
    "app", "portal", "config", "data", "docs", "docker", "deploy", "extras", "scripts",
    "install.sh", "LICENSE", "SECURITY.md", "update-manifest.json", "update-manifest.example.json",
    ".env.example", ".dockerignore", ".gitattributes", ".gitignore",
)
EXCLUDED = {("portal", "node_modules"), ("portal", "dist")}


def filesystem_reserve(total: int) -> int:
    return min(RESERVE, max(8 * MIB, total // 20))


def configured_floor(value: str | int, mode: str = "prebuilt", policy: str = "") -> int:
    raw = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,16}", raw):
        raise ValueError("UPDATE_MIN_FREE_BYTES must be a non-negative integer")
    number = int(raw)
    # These were shipped defaults, not a measured requirement or an operator override.
    if mode == "prebuilt" and (number == 0 or (policy != "adaptive" and number in {1024 * MIB, 2048 * MIB})):
        return 0
    return number or 1024 * MIB


def footprint(root: Path, items: tuple[str, ...] | None = None) -> int:
    """Allow for tar headers, small-file allocation and non-sparse backup extraction."""
    def visit(path: Path, parts: tuple[str, ...]) -> int:
        if parts[:2] in EXCLUDED:
            return 0
        info = path.lstat()
        if path.is_symlink():
            return 8192
        if path.is_dir():
            return 8192 + sum(visit(child, (*parts, child.name)) for child in path.iterdir())
        if not path.is_file():
            raise ValueError("unsupported file in update snapshot")
        return ((info.st_size + 4095) // 4096 + 2) * 4096

    if items is None:
        return visit(root, ())
    return sum(visit(root / item, (item,)) for item in items if (root / item).exists() or (root / item).is_symlink())


def existing_path(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def archive_footprint(path: Path) -> int:
    total = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            total += ((member.size + 4095) // 4096 + 2) * 4096
    return total


@dataclass
class Budget:
    path: Path
    free_bytes: int
    required_bytes: int = RESERVE
    components: dict[str, int] = field(default_factory=dict)
    minimum_bytes: int = 0


class Plan:
    def __init__(self, floor: int = 0):
        self.floor = floor
        self.devices: dict[int, Budget] = {}

    def add(self, path: Path, label: str, amount: int = 0, minimum: int = 0) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("storage budget must be non-negative")
        probe = existing_path(path)
        device = probe.stat().st_dev
        if device not in self.devices:
            usage = shutil.disk_usage(probe)
            self.devices[device] = Budget(probe, usage.free, filesystem_reserve(usage.total))
        budget = self.devices[device]
        if type(minimum) is not int or minimum < 0:
            raise ValueError("storage minimum must be non-negative")
        budget.minimum_bytes = max(budget.minimum_bytes, minimum)
        budget.required_bytes += amount
        budget.components[label] = budget.components.get(label, 0) + amount

    def report(self) -> list[dict]:
        return [{"path": str(item.path), "free_bytes": item.free_bytes,
                 "required_bytes": max(self.floor, item.minimum_bytes, item.required_bytes), "components": item.components,
                 "ready": item.free_bytes >= max(self.floor, item.minimum_bytes, item.required_bytes)} for item in self.devices.values()]


def command_json(command: list[str], *args: str):
    result = subprocess.run([*command, *args], check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def layer_chains(diff_ids: list[str]) -> list[str]:
    chains = []
    for diff_id in diff_ids:
        if not isinstance(diff_id, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", diff_id):
            raise ValueError("invalid image layer identity")
        chains.append("sha256:" + hashlib.sha256(f"{chains[-1]} {diff_id}".encode()).hexdigest() if chains else diff_id)
    return chains


def image_budget(metadata: dict | None, references: list[str], present: set[str], architecture: str,
                 cached_chains: set[str] | None = None, driver: str = "overlay2") -> int:
    if len(references) != 3 or any(not re.fullmatch(re.escape(repo) + r"@sha256:[0-9a-f]{64}", ref)
                                   for repo, ref in zip(REPOSITORIES.values(), references, strict=True)):
        raise ValueError("invalid immutable release image references")
    missing = [ref for ref in references if ref not in present]
    if not missing:
        return 0
    if driver not in {"overlay2", "overlayfs", "fuse-overlayfs", "vfs"}:
        raise ValueError(f"cannot estimate image allocation for Docker storage driver {driver!r}")
    if metadata is None:
        if driver == "vfs":
            raise ValueError("vfs requires measured image layers in the release archive")
        return LEGACY_IMAGE_BUDGET
    if metadata.get("schema") != 1:
        raise ValueError("unsupported image storage metadata")
    images = metadata.get("platforms", {}).get(f"linux/{architecture}")
    if not isinstance(images, dict) or set(images) != set(REPOSITORIES):
        raise ValueError("image storage metadata has no matching platform")
    total, compressed_seen, chain_seen = 16 * MIB, set(), set(cached_chains or ())
    for name, ref in zip(REPOSITORIES, references, strict=True):
        if ref in present:
            continue
        entry = images[name]
        if not isinstance(entry, dict) or entry.get("reference") != ref:
            raise ValueError("image storage metadata does not match release digests")
        unpacked, compressed = entry.get("unpacked_bytes"), entry.get("compressed_bytes")
        if any(type(n) is not int or not 0 < n <= 8 * 1024**3 for n in (unpacked, compressed)):
            raise ValueError("invalid image storage sizes")
        layers = entry.get("layers")
        if not isinstance(layers, list) or not 0 < len(layers) <= 128:
            if driver == "vfs":
                raise ValueError("vfs requires measured image layers in the release archive")
            # Older measured releases can still use the whole-image upper bound.
            total += compressed + (unpacked * 5 + 3) // 4
            continue
        if any(not isinstance(layer, dict) for layer in layers):
            raise ValueError("invalid image layer sizes")
        chains = layer_chains([layer.get("diff_id", "") for layer in layers])
        parent_allocation = 0
        for layer, chain in zip(layers, chains, strict=True):
            digest, packed, raw = layer.get("digest"), layer.get("compressed_bytes"), layer.get("unpacked_bytes")
            if (not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
                    or any(type(n) is not int or not 0 < n <= 8 * 1024**3 for n in (packed, raw))):
                raise ValueError("invalid image layer sizes")
            # Blob identity and unpacked layer-chain identity are not interchangeable.
            if digest not in compressed_seen:
                total += packed
                compressed_seen.add(digest)
            allocation = layer.get("allocation_bytes", (raw * 5 + 3) // 4)
            if type(allocation) is not int or not raw <= allocation <= 64 * 1024**3:
                raise ValueError("invalid layer allocation budget")
            parent_allocation += allocation
            if chain not in chain_seen:
                # vfs copies the complete parent tree for each new child layer.
                total += parent_allocation if driver == "vfs" else allocation
                chain_seen.add(chain)
    return total


def containerd_root(info: dict, proc: Path = Path("/proc"), override: str | None = None) -> Path | None:
    statuses = dict(info.get("DriverStatus") or [])
    if "containerd" not in statuses.get("driver-type", ""):
        return None
    override = (os.environ.get("UPDATE_CONTAINERD_ROOT", "") if override is None else override).strip()
    if override:
        path = Path(override)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("UPDATE_CONTAINERD_ROOT must be an existing absolute directory")
        return path
    roots = set()
    for cmdline in proc.glob("[0-9]*/cmdline"):
        try:
            arguments = cmdline.read_bytes().decode().rstrip("\0").split("\0")
            if Path(arguments[0]).name != "containerd":
                continue
            options = {}
            for index, argument in enumerate(arguments[1:], 1):
                key, separator, value = argument.partition("=")
                if key in {"--config", "-c", "--root"}:
                    options[key] = value if separator else arguments[index + 1]
            if "--root" in options:
                root = options["--root"]
            else:
                config = Path(options.get("--config", options.get("-c", "/etc/containerd/config.toml")))
                root = "/var/lib/containerd"
                if config.is_file():
                    try:
                        import tomllib
                    except ImportError:
                        import tomli as tomllib
                    root = tomllib.loads(config.read_text()).get("root", root)
            path = Path(root)
            if not path.is_absolute():
                raise ValueError("containerd root is not absolute")
            roots.add(path)
        except (PermissionError, ProcessLookupError, FileNotFoundError):
            continue
        except (IndexError, UnicodeError, ImportError, TypeError):
            raise ValueError("cannot read active containerd configuration; set UPDATE_CONTAINERD_ROOT") from None
    if len(roots) == 1:
        return roots.pop()
    raise ValueError("cannot identify one active containerd storage; set UPDATE_CONTAINERD_ROOT")


def build_plan(project: Path, phase: str, command: list[str], release: Path | None = None,
               references: list[str] | None = None, backup: Path | None = None, build_reserve: int = 0) -> list[dict]:
    values = {}
    env_path = project / ".env"
    if env_path.is_file():
        values = dict(line.split("=", 1) for line in env_path.read_text().splitlines()
                      if "=" in line and not line.lstrip().startswith("#"))

    def option(key: str, default: str = "") -> str:
        return os.environ.get(key, values.get(key, default))

    floor = configured_floor(option("UPDATE_MIN_FREE_BYTES", "0"), policy=option("UPDATE_STORAGE_POLICY"))
    raw_project_floor = option("UPDATE_MIN_PROJECT_FREE_BYTES", "0")
    if not re.fullmatch(r"[0-9]{1,16}", raw_project_floor):
        raise ValueError("UPDATE_MIN_PROJECT_FREE_BYTES must be a non-negative integer")
    project_floor = int(raw_project_floor)
    plan = Plan()
    tmp = Path(tempfile.gettempdir())
    plan.add(project, "project", minimum=project_floor)
    plan.add(tmp, "temporary files")
    if phase == "setup":
        plan.add(Path("/"), "package setup")
        return plan.report()
    info = command_json(command, "info", "--format", "{{json .}}")
    docker_root = Path(info["DockerRootDir"])
    if not docker_root.is_absolute() or not docker_root.is_dir():
        raise ValueError("Docker storage must be available on this host")
    plan.add(docker_root, "Docker", minimum=floor)
    plan.add(docker_root, "source build cache", build_reserve)
    image_root = containerd_root(info, override=option("UPDATE_CONTAINERD_ROOT")) or docker_root
    plan.add(image_root, "image storage", minimum=floor)
    if build_reserve and existing_path(image_root).stat().st_dev != docker_root.stat().st_dev:
        plan.add(image_root, "source runtime images", min(build_reserve, LEGACY_IMAGE_BUDGET))
    if phase == "initial":
        return plan.report()
    if release is None or not release.is_dir():
        raise ValueError("verified release directory is required")
    installing = phase in {"install", "copied", "installed"}
    snapshot = (0 if installing else
                archive_footprint(backup) if phase == "activate" and backup is not None and backup.is_file()
                else footprint(project, BACKUP_ITEMS))
    activation = 0 if phase in {"copied", "installed"} else footprint(release)
    plan.add(project, "new release files", activation)
    plan.add(tmp, "rollback extraction", snapshot)
    if phase not in {"activate", "install", "copied", "installed"}:
        # gzip can be larger than its input; footprint includes generous per-file overhead.
        plan.add(project / "backups", "mandatory backup", snapshot + MIB)
    elif phase == "activate" and (backup is None or not backup.is_file() or backup.is_symlink() or backup.stat().st_size == 0):
        raise ValueError("verified backup is required before activation")
    if phase in {"images", "install", "copied"}:
        references = references or []
        architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), "")
        present, cached_chains = set(), set()
        for ref in references:
            try:
                inspected = command_json(command, "image", "inspect", ref)
            except subprocess.CalledProcessError:
                continue
            if inspected and inspected[0].get("Architecture") == architecture:
                present.add(ref)
                cached_chains.update(layer_chains(inspected[0]["RootFS"]["Layers"]))
        for key in ("THE333_GOBGP_IMAGE", "THE333_BACKEND_IMAGE", "THE333_PORTAL_IMAGE"):
            ref = option(key).strip()
            if not ref or ref in present:
                continue
            try:
                inspected = command_json(command, "image", "inspect", ref)
                if inspected and inspected[0].get("Architecture") == architecture:
                    cached_chains.update(layer_chains(inspected[0]["RootFS"]["Layers"]))
            except subprocess.CalledProcessError:
                pass
        metadata_path = release / "docker" / "image-storage.json"
        metadata = json.loads(metadata_path.read_text()) if metadata_path.is_file() else None
        required = image_budget(metadata, references, present, architecture, cached_chains, info.get("Driver", ""))
        plan.add(image_root, "missing images and unpacking", required)
    return plan.report()
