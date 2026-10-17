"""Host-only retention of verified update snapshots and their exact runtime images."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.maintenance import ACTIVE_STATUSES, read_state

SERVICES = ("the333-gobgp-core", "the333-bgp-backend", "the333-portal")
REPOSITORIES = ("the333-bgp-core", "the333-bgp-backend", "the333-bgp-portal")
ARCHIVE_RE = re.compile(r"the333-bgp-before-update-\d{8}-\d{6}(?:-[A-Za-z0-9]{8})?\.tar\.gz")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+(?:[a-z][0-9]*)?")
LAZY_SNAPSHOTTERS = {"stargz", "nydus", "overlaybd", "soci"}


class StorageError(RuntimeError):
    pass


def valid_reference(reference: str, index: int) -> bool:
    repository = REPOSITORIES[index]
    return bool(re.fullmatch(re.escape(repository) + r":[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", reference)
                or re.fullmatch("ghcr.io/the333tech/" + re.escape(repository) + r"@sha256:[0-9a-f]{64}", reference))


def file_digest(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StorageError("snapshot must be a regular file without hard links")
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def archive_version(path: Path) -> str:
    required = {".env", "VERSION", "docker-compose.yml", "docker-compose.portal.yml", "app", "scripts", "config", "data"}
    found = set()
    version = ""
    with tarfile.open(path, "r:gz") as archive:
        for entry in archive:
            name = entry.name.removeprefix("./").rstrip("/")
            if name not in required:
                continue
            if entry.issym() or entry.islnk():
                raise StorageError("snapshot contains linked required entries")
            is_directory = name in {"app", "scripts", "config", "data"}
            if (is_directory and not entry.isdir()) or (not is_directory and not entry.isfile()):
                raise StorageError("snapshot has invalid required entries")
            if name in found:
                raise StorageError("snapshot has duplicate required entries")
            found.add(name)
            if name == "VERSION":
                if entry.size > 128:
                    raise StorageError("snapshot version is invalid")
                with archive.extractfile(entry) as source:
                    version = source.read().decode("ascii").strip()
    if found != required or not VERSION_RE.fullmatch(version):
        raise StorageError("snapshot is incomplete or has no valid version")
    return version


class Docker:
    def __init__(self, command: list[str] | None = None):
        self.command = command or ["docker"]

    def run(self, *arguments: str, missing_ok: bool = False) -> str | None:
        try:
            result = subprocess.run([*self.command, *arguments], capture_output=True, text=True,
                                    timeout=90, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise StorageError("Docker command is unavailable or timed out") from exc
        if result.returncode:
            if missing_ok and re.search(r"no such (image|object)", result.stderr, re.IGNORECASE):
                return None
            # Do not copy Docker/Compose output: it can contain rendered configuration.
            raise StorageError(f"Docker {arguments[0]} failed; no cleanup was authorized by this check")
        return result.stdout

    def info(self) -> dict:
        return json.loads(self.run("info", "--format",
            '{"id":{{json .ID}},"root":{{json .DockerRootDir}},"driver":{{json .Driver}},"driver_status":{{json .DriverStatus}}}'))

    def image(self, reference: str) -> dict | None:
        output = self.run("image", "inspect", "--format",
                          '{"id":{{json .Id}},"tags":{{json .RepoTags}},"digests":{{json .RepoDigests}},"size":{{json .Size}}}',
                          reference, missing_ok=True)
        return json.loads(output) if output is not None else None

    def used_images(self) -> set[str]:
        identifiers = self.run("container", "ls", "-aq", "--no-trunc").split()
        used = set()
        for identifier in identifiers:
            used.add(self.run("container", "inspect", "--format", "{{.Image}}", identifier).strip())
        return used

    def current_images(self, project: Path) -> list[dict]:
        config = json.loads(self.run("compose", "--project-directory", str(project),
                                     "-f", str(project / "docker-compose.yml"),
                                     "-f", str(project / "docker-compose.portal.yml"), "config", "--format", "json"))
        images = []
        for index, service in enumerate(SERVICES):
            reference = config["services"][service]["image"]
            if not valid_reference(reference, index):
                raise StorageError("unrecognized runtime image reference; keep this installation unmanaged")
            container = json.loads(self.run("container", "inspect", "--format",
                '{"image":{{json .Image}},"reference":{{json .Config.Image}},"labels":{{json .Config.Labels}}}', service))
            labels = container.get("labels") or {}
            if (labels.get("com.docker.compose.service") != service
                    or labels.get("com.docker.compose.project.working_dir") != str(project)
                    or container["reference"] != reference):
                raise StorageError("runtime containers do not match this project and its current configuration")
            image = self.image(reference)
            if image is None or image["id"] != container["image"] or not IMAGE_ID_RE.fullmatch(image["id"]):
                raise StorageError("runtime image is missing or its reference has changed")
            images.append({"service": service, "reference": reference, "id": image["id"]})
        return images

    def remove(self, reference: str) -> None:
        self.run("image", "rm", "--no-prune", reference)


class Retention:
    def __init__(self, project: Path, docker: Docker, keep: int = 2):
        if not 2 <= keep <= 20:
            raise StorageError("UPDATE_BACKUP_RETENTION must be between 2 and 20")
        if not project.is_absolute() or project.resolve() != project or project == Path("/") or not project.is_dir():
            raise StorageError("project path must be an absolute non-symlink directory")
        self.project, self.docker, self.keep = project, docker, keep
        self.backups = project / "backups"
        self.records = self.backups / ".retention"
        self.check_directories()

    def check_directories(self) -> None:
        for path in (self.backups, self.records):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise StorageError("backup storage must be local directories, not links")

    def archive_path(self, name: str) -> Path:
        if not isinstance(name, str) or not ARCHIVE_RE.fullmatch(name):
            raise StorageError("unrecognized update archive name")
        return self.backups / name

    def write_record(self, record: dict) -> None:
        self.check_directories()
        self.records.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.records, 0o700)
        target = self.records / (record["archive"] + ".json")
        if target.is_symlink():
            raise StorageError("linked retention record")
        fd, temporary = tempfile.mkstemp(prefix=".record-", dir=self.records)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True)
                handle.flush()
                os.fchmod(handle.fileno(), 0o600)
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory = os.open(self.records, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def read_records(self) -> list[dict]:
        self.check_directories()
        records = []
        if not self.records.exists():
            return records
        for path in sorted(self.records.iterdir()):
            if path.name.startswith(".record-"):
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
                raise StorageError("unsafe retention record; cleanup disabled")
            record = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(record, dict) or record.get("schema") != 1
                    or record.get("project") != str(self.project)
                    or record.get("status") not in {"pending", "succeeded", "retired"}
                    or not isinstance(record.get("archive"), str)
                    or not ARCHIVE_RE.fullmatch(record["archive"])
                    or path.name != record["archive"] + ".json"
                    or not isinstance(record.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                    or not isinstance(record.get("version"), str)
                    or not VERSION_RE.fullmatch(record["version"])
                    or not isinstance(record.get("target_version"), str)
                    or not VERSION_RE.fullmatch(record["target_version"])
                    or not isinstance(record.get("daemon"), str) or not record["daemon"]
                    or type(record.get("generation")) is not int or record["generation"] < 1
                    or not isinstance(record.get("images"), list) or len(record["images"]) != 3):
                raise StorageError("invalid retention record; cleanup disabled")
            for index, image in enumerate(record["images"]):
                if (not isinstance(image, dict) or image.get("service") != SERVICES[index]
                        or not isinstance(image.get("id"), str) or not IMAGE_ID_RE.fullmatch(image["id"])
                        or not isinstance(image.get("reference"), str) or not valid_reference(image["reference"], index)):
                    raise StorageError("invalid snapshot image; cleanup disabled")
            records.append(record)
        if len({r["generation"] for r in records}) != len(records):
            raise StorageError("duplicate snapshot generation; cleanup disabled")
        return records

    def capture(self, archive: Path, target_version: str) -> None:
        if archive != self.archive_path(archive.name) or not VERSION_RE.fullmatch(target_version):
            raise StorageError("snapshot path or target version is invalid")
        if (self.records / (archive.name + ".json")).exists():
            raise StorageError("snapshot is already registered")
        digest = file_digest(archive)
        version = archive_version(archive)
        if version != (self.project / "VERSION").read_text().strip():
            raise StorageError("snapshot does not match the installed version")
        info = self.docker.info()
        if not info.get("id"):
            raise StorageError("Docker daemon identity is unknown")
        generation = max((r["generation"] for r in self.read_records()), default=0) + 1
        self.write_record({"schema": 1, "project": str(self.project), "archive": archive.name, "generation": generation,
                           "sha256": digest, "version": version, "target_version": target_version,
                           "created_at": datetime.now(timezone.utc).isoformat(), "status": "pending",
                           "daemon": info["id"], "images": self.docker.current_images(self.project)})

    def complete(self, archive: Path) -> None:
        if archive != self.archive_path(archive.name):
            raise StorageError("snapshot path is outside this installation")
        record = next((r for r in self.read_records() if r["archive"] == archive.name), None)
        if record is None or record["status"] != "pending":
            raise StorageError("pending snapshot is missing")
        if (self.project / "VERSION").read_text().strip() != record["target_version"]:
            raise StorageError("installed version differs from the update target")
        self.verify_snapshot(record)
        self.docker.current_images(self.project)
        record["status"] = "succeeded"
        self.write_record(record)

    def verify_snapshot(self, record: dict) -> None:
        archive = self.archive_path(record["archive"])
        if file_digest(archive) != record["sha256"]:
            raise StorageError("retained snapshot checksum mismatch; cleanup disabled")
        if archive_version(archive) != record["version"]:
            raise StorageError("retained snapshot is incomplete; cleanup disabled")
        if self.docker.info()["id"] != record["daemon"]:
            raise StorageError("snapshot belongs to another Docker daemon; cleanup disabled")
        for image in record["images"]:
            current = self.docker.image(image["reference"])
            if current is None or current["id"] != image["id"]:
                raise StorageError("retained rollback images are unavailable; cleanup disabled")

    def plan(self) -> dict:
        records = self.read_records()
        info = self.docker.info()
        current = self.docker.current_images(self.project)
        used = self.docker.used_images()
        succeeded = sorted((r for r in records if r["status"] == "succeeded"),
                           key=lambda r: r["generation"], reverse=True)
        protected = succeeded[:self.keep] + [r for r in records if r["status"] == "pending"]
        blockers = []
        if info.get("driver") in LAZY_SNAPSHOTTERS:
            blockers.append("Очистка отключена: полное локальное наличие слоёв lazy-snapshotter не подтверждено.")
        if len(succeeded) < self.keep:
            blockers.append(f"Нужно {self.keep} подтверждённых комплектов; сейчас {len(succeeded)}. Удаление пока отключено.")
        for record in protected:
            try:
                self.verify_snapshot(record)
            except (OSError, ValueError, tarfile.TarError, StorageError) as exc:
                blockers.append(str(exc))
        protected_names = {r["archive"] for r in protected}
        protected_ids = used | {image["id"] for image in current}
        protected_ids.update(image["id"] for r in protected for image in r["images"])
        files, candidates, skipped = [], {}, []
        for record in records:
            if record["archive"] in protected_names:
                continue
            if record["daemon"] != info["id"]:
                skipped.append(f"{record['archive']}: другой Docker daemon")
                continue
            archive = self.archive_path(record["archive"])
            if archive.exists() or archive.is_symlink():
                if file_digest(archive) != record["sha256"]:
                    blockers.append(f"{archive.name}: контрольная сумма изменилась")
                    continue
                files.append({"name": archive.name, "bytes": archive.stat().st_size, "sha256": record["sha256"]})
            for image in record["images"]:
                if image["id"] in protected_ids:
                    continue
                present = self.docker.image(image["reference"])
                if present is None:
                    continue
                if present["id"] != image["id"]:
                    skipped.append(f"{image['reference']}: ссылка теперь указывает на другой образ")
                    continue
                candidate = candidates.setdefault(image["id"], {"id": image["id"], "references": set(),
                    "aliases": set(present.get("tags") or []) | set(present.get("digests") or []), "logical_bytes": present["size"]})
                candidate["references"].add(image["reference"])
        images = []
        for candidate in candidates.values():
            if candidate["aliases"] - candidate["references"]:
                skipped.append(f"{candidate['id']}: есть неучтённые ссылки, образ оставлен")
            else:
                images.append({k: sorted(v) if isinstance(v, set) else v
                               for k, v in candidate.items() if k != "aliases"})
        managed_names = {r["archive"] for r in records}
        unmanaged = sorted(p.name for p in self.backups.iterdir()
                           if p.name != ".retention" and p.name not in managed_names) if self.backups.exists() else []
        return {"keep": self.keep, "protected": sorted(protected_names), "files": [] if blockers else files,
                "images": [] if blockers else sorted(images, key=lambda i: i["id"]), "blockers": blockers,
                "skipped": skipped, "unmanaged": unmanaged,
                "archive_bytes": 0 if blockers else sum(f["bytes"] for f in files),
                "image_space_note": "Размеры образов логические: общие слои и build cache не позволяют обещать такой же объём освобождения."}

    def apply(self, plan: dict) -> dict:
        # Rebuild under the operation lock: a stale preview must never authorize new targets.
        if self.plan() != plan:
            raise StorageError("cleanup plan changed; review a new preview")
        if plan["blockers"]:
            raise StorageError("cleanup is disabled until rollback snapshots are available")
        before = filesystem_space(self.project)
        docker_root = Path(self.docker.info()["root"])
        docker_before = filesystem_space(docker_root)
        removed_files, removed_images = [], []
        for image in plan["images"]:
            if image["id"] in self.docker.used_images():
                raise StorageError("an image became used by a container; cleanup stopped")
            for reference in image["references"]:
                present = self.docker.image(reference)
                if present is None or present["id"] != image["id"]:
                    raise StorageError("image reference changed; cleanup stopped")
                self.docker.remove(reference)
                removed_images.append(reference)
        directory = os.open(self.backups, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for item in plan["files"]:
                path = self.archive_path(item["name"])
                if file_digest(path) != item["sha256"]:
                    raise StorageError("snapshot changed; cleanup stopped")
                actual = path.stat(follow_symlinks=False)
                anchored = os.stat(item["name"], dir_fd=directory, follow_symlinks=False)
                if (actual.st_dev, actual.st_ino) != (anchored.st_dev, anchored.st_ino):
                    raise StorageError("backup directory changed; cleanup stopped")
                os.unlink(item["name"], dir_fd=directory)
                removed_files.append(item["name"])
            os.fsync(directory)
        finally:
            os.close(directory)
        for record in self.read_records():
            if record["archive"] in removed_files:
                record["status"] = "retired"
                self.write_record(record)
        return {"removed_archives": removed_files, "removed_image_references": removed_images,
                "project_filesystem_before": before, "project_filesystem_after": filesystem_space(self.project),
                "docker_filesystem_before": docker_before, "docker_filesystem_after": filesystem_space(docker_root)}


def filesystem_space(path: Path) -> dict:
    try:
        usage = shutil.disk_usage(path)
        return {"device": path.stat().st_dev, "total_bytes": usage.total, "free_bytes": usage.free}
    except OSError:
        return {"error": "файловая система недоступна для измерения"}


def directory_bytes(path: Path, excluded: set[str] | None = None) -> dict:
    if path.is_symlink():
        return {"error": "символическая ссылка не измеряется"}
    if not path.exists():
        return {"bytes": 0, "exists": False}
    if not path.is_dir():
        return {"error": "ожидался каталог"}
    total = 0

    def failed(error):
        raise error

    try:
        for root, directories, files in os.walk(path, followlinks=False, onerror=failed):
            directories[:] = [d for d in directories if not (Path(root) / d).is_symlink()
                              and (Path(root) / d).relative_to(path).as_posix() not in (excluded or set())]
            for name in files:
                info = (Path(root) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
        return {"bytes": total, "exists": True}
    except OSError:
        return {"bytes_partial": total, "error": "не все файлы доступны для измерения"}


def storage_report(retention: Retention) -> dict:
    info = retention.docker.info()
    notes = []
    if ["driver-type", "io.containerd.snapshotter.v1"] in (info.get("driver_status") or []):
        notes.append("containerd может хранить образы отдельно от Docker Root Dir. "
                     "Путь отдельного хранилища не определён этим отчётом; если он на другом разделе, проверь место там отдельно.")
    if info.get("driver") in LAZY_SNAPSHOTTERS:
        notes.append("Обнаружен snapshotter с возможной lazy-загрузкой: наличие image ID не гарантирует все слои для офлайн-отката. "
                     "До обновления нужна полная локальная копия образов и проверка восстановления.")
    current = retention.docker.current_images(retention.project)
    references = {i["reference"] for i in current}
    references.update(i["reference"] for r in retention.read_records() for i in r["images"])
    images = {}
    for reference in sorted(references):
        image = retention.docker.image(reference)
        if image:
            images.setdefault(image["id"], {"id": image["id"], "logical_bytes": image["size"], "references": []})["references"].append(reference)
    return {"project_files": directory_bytes(retention.project, {"backups", "data/system_backups"}),
            "update_backups": directory_bytes(retention.backups),
            "portal_backups": directory_bytes(retention.project / "data" / "system_backups"),
            "project_filesystem": filesystem_space(retention.project), "docker_root": info["root"],
            "storage_notes": notes,
            "docker_filesystem": filesystem_space(Path(info["root"])), "tracked_images": list(images.values()),
            "cleanup": retention.plan()}


@contextmanager
def operation_lock(project: Path, internal: bool = False):
    import fcntl
    from app.update_runner import prepare_runtime, runtime_identity

    for path in (project / "runtime", project / "runtime" / "operations",
                 *(project / "runtime" / "operations" / name for name in ("runner.lock", "writes.lock", "storage.lock"))):
        if path.is_symlink():
            raise StorageError("linked operation directory or lock")
    directory = prepare_runtime(project, *runtime_identity(project))
    with (directory / "runner.lock").open("r+") as runner, (directory / "writes.lock").open("r+") as writes:
        for handle in (runner, writes):
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                if not internal:
                    raise StorageError("обновление или другая операция записи уже выполняется") from exc
            else:
                if internal:
                    raise StorageError("internal retention requires the active update transaction")
        state = read_state(directory)
        if internal:
            if state.get("status") != "running" or os.getenv("THE333_UPDATE_RUNNER_ACTIVE") != "true":
                raise StorageError("active update transaction is missing")
        elif state.get("status") in ACTIVE_STATUSES:
            raise StorageError("незавершённое обновление требует проверки; очистка запрещена")
        fd = os.open(directory / "storage.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as storage:
            try:
                fcntl.flock(storage, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StorageError("другая очистка уже выполняется") from exc
            yield
