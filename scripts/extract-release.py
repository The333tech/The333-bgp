#!/usr/bin/env python3
import argparse
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath


MAX_MEMBERS = 20_000
MAX_EXPANDED_BYTES = 512 * 1024 * 1024


def validated_members(archive: tarfile.TarFile) -> tuple[str, list[tuple[tarfile.TarInfo, tuple[str, ...]]]]:
    validated: list[tuple[tarfile.TarInfo, tuple[str, ...]]] = []
    roots: set[str] = set()
    expanded_bytes = 0

    for index, member in enumerate(archive.getmembers(), start=1):
        if index > MAX_MEMBERS:
            raise ValueError(f"release archive contains more than {MAX_MEMBERS} members")
        if "\\" in member.name:
            raise ValueError("release archive contains a backslash path")
        path = PurePosixPath(member.name)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"unsafe release path: {member.name!r}")
        roots.add(path.parts[0])
        if not (member.isdir() or member.isfile()):
            raise ValueError(f"unsupported release member type: {member.name!r}")
        expanded_bytes += max(0, member.size)
        if expanded_bytes > MAX_EXPANDED_BYTES:
            raise ValueError("release archive exceeds the expanded size limit")
        validated.append((member, path.parts))

    if len(roots) != 1:
        raise ValueError("release archive must contain exactly one top-level directory")
    return next(iter(roots)), validated


def extract_release(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("release extraction directory must be empty")
    destination_root = destination.resolve()

    with tarfile.open(archive_path, mode="r:gz") as archive:
        _root, members = validated_members(archive)
        for member, parts in members:
            relative_parts = parts[1:]
            if not relative_parts:
                continue
            target = destination.joinpath(*relative_parts)
            resolved_parent = target.parent.resolve()
            resolved_parent.relative_to(destination_root)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, 0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read release member: {member.name!r}")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("destination")
    args = parser.parse_args()
    archive_path = Path(args.archive).resolve()
    destination = Path(args.destination).resolve()
    if not archive_path.is_file() or archive_path.is_symlink():
        raise SystemExit("release archive is missing or unsafe")
    extract_release(archive_path, destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
