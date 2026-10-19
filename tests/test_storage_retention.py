import hashlib
import io
import importlib.util
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage_retention import (Docker, Retention, SERVICES, REPOSITORIES, StorageError,
                                   archive_version, directory_bytes, operation_lock, storage_report)


class FakeDocker:
    def __init__(self):
        self.images = {}
        self.current = []
        self.used = set()
        self.removed = []
        self.daemon = "test-daemon"

    def info(self):
        return {"id": self.daemon, "root": "/missing-docker-root-for-test"}

    def image(self, reference):
        return self.images.get(reference)

    def current_images(self, project):
        return self.current

    def used_images(self):
        return {i["id"] for i in self.current} | self.used

    def remove(self, reference):
        self.removed.append(reference)
        del self.images[reference]

    def install(self, version):
        self.current = []
        for index, repository in enumerate(REPOSITORIES):
            reference = f"{repository}:{version}"
            identifier = "sha256:" + hashlib.sha256(reference.encode()).hexdigest()
            self.images[reference] = {"id": identifier, "tags": [reference], "digests": [], "size": 100}
            self.current.append({"service": SERVICES[index], "reference": reference, "id": identifier})


@unittest.skipUnless(os.name == "posix", "retention runs on the Linux host")
class StorageRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        (self.project / ".env").write_text(f"PUID={os.getuid()}\nPGID={os.getgid()}\n")
        self.docker = FakeDocker()
        self.retention = Retention(self.project, self.docker)

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, version, number):
        backups = self.project / "backups"
        backups.mkdir(exist_ok=True)
        path = backups / f"the333-bgp-before-update-20261018-1200{number:02d}-abcdefgh.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name in ("app", "scripts", "config", "data"):
                entry = tarfile.TarInfo(name)
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            for name, content in (("VERSION", version), (".env", "PRIVATE_FIXTURE_ONLY=yes\n"),
                                  ("docker-compose.yml", "services: {}\n"), ("docker-compose.portal.yml", "services: {}\n")):
                entry = tarfile.TarInfo(name)
                entry.size = len(content.encode())
                archive.addfile(entry, io.BytesIO(content.encode()))
        return path

    def snapshot(self, old, new, number, complete=True):
        (self.project / "VERSION").write_text(old)
        self.docker.install(old)
        archive = self.archive(old, number)
        self.retention.capture(archive, new)
        if complete:
            (self.project / "VERSION").write_text(new)
            self.docker.install(new)
            self.retention.complete(archive)
        return archive

    def three_updates(self):
        return [self.snapshot("1.0", "1.1", 1), self.snapshot("1.1", "1.2", 2),
                self.snapshot("1.2", "1.3", 3)]

    def test_preview_is_read_only_and_keeps_two_previous_versions(self):
        archives = self.three_updates()
        before = {p: p.read_bytes() for p in self.retention.records.iterdir()}
        plan = self.retention.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual([f["name"] for f in plan["files"]], [archives[0].name])
        self.assertEqual(set(plan["protected"]), {a.name for a in archives[1:]})
        self.assertEqual(len(plan["images"]), 3)
        self.assertEqual({p: p.read_bytes() for p in self.retention.records.iterdir()}, before)
        self.assertEqual(self.docker.removed, [])
        self.assertTrue(all(a.exists() for a in archives))

    def test_apply_only_retires_recorded_old_snapshot_and_images(self):
        archives = self.three_updates()
        manual = self.retention.backups / "my-private-backup.tar.gz"
        legacy = self.archive("0.9", 0)
        manual.write_bytes(b"manual backup")
        portal = self.project / "data" / "system_backups"
        portal.mkdir(parents=True)
        (portal / "backup.zip").write_bytes(b"portal")
        old_refs = {f"{repository}:1.0" for repository in REPOSITORIES}
        result = self.retention.apply(self.retention.plan())
        self.assertEqual(set(result["removed_image_references"]), old_refs)
        self.assertFalse(archives[0].exists())
        self.assertTrue(all(a.exists() for a in [*archives[1:], manual, legacy, portal / "backup.zip"]))
        self.assertEqual(self.retention.read_records()[0]["status"], "retired")
        for record in self.retention.read_records()[1:]:
            self.retention.verify_snapshot(record)
        self.assertEqual(self.retention.apply(self.retention.plan())["removed_archives"], [])

    def test_missing_previous_snapshot_disables_all_deletion(self):
        archives = self.three_updates()
        archives[-1].unlink()
        plan = self.retention.plan()
        self.assertTrue(plan["blockers"])
        self.assertEqual(plan["files"], [])
        self.assertEqual(plan["images"], [])
        with self.assertRaises(StorageError):
            self.retention.apply(plan)
        self.assertEqual(self.docker.removed, [])

    def test_corrupt_previous_archive_disables_cleanup(self):
        archives = self.three_updates()
        archives[-1].write_bytes(b"damaged")
        self.assertTrue(self.retention.plan()["blockers"])
        self.assertTrue(archives[0].exists())

    def test_missing_or_retagged_previous_image_disables_cleanup(self):
        self.three_updates()
        reference = f"{REPOSITORIES[1]}:1.2"
        image = self.docker.images.pop(reference)
        self.assertTrue(self.retention.plan()["blockers"])
        self.docker.images[reference] = {**image, "id": "sha256:" + "f" * 64}
        self.assertTrue(self.retention.plan()["blockers"])

    def test_first_update_does_not_remove_the_only_snapshot(self):
        archive = self.snapshot("1.0", "1.1", 1)
        plan = self.retention.plan()
        self.assertTrue(plan["blockers"])
        self.assertEqual(plan["files"], [])
        self.assertIn(archive.name, plan["protected"])

    def test_clock_rollback_does_not_make_latest_snapshot_a_cleanup_target(self):
        archives = self.three_updates()
        newest = self.snapshot("1.3", "1.4", 0)
        plan = self.retention.plan()
        self.assertIn(newest.name, plan["protected"])
        self.assertIn(archives[0].name, [f["name"] for f in plan["files"]])
        self.assertNotIn(newest.name, [f["name"] for f in plan["files"]])

    def test_new_backup_directory_link_cannot_redirect_existing_manager(self):
        self.three_updates()
        self.retention.records.rename(self.retention.backups / ".retention-moved")
        self.retention.records.symlink_to(self.project.parent, target_is_directory=True)
        with self.assertRaises(StorageError):
            self.retention.plan()

    def test_interrupted_update_snapshot_is_protected(self):
        self.three_updates()
        pending = self.snapshot("1.3", "1.4", 4, complete=False)
        plan = self.retention.plan()
        self.assertIn(pending.name, plan["protected"])
        self.assertNotIn(pending.name, [item["name"] for item in plan["files"]])

    def test_container_including_stopped_protects_old_image(self):
        self.three_updates()
        used = self.docker.images[f"{REPOSITORIES[0]}:1.0"]["id"]
        self.docker.used.add(used)
        self.assertNotIn(used, {i["id"] for i in self.retention.plan()["images"]})

    def test_prebuilt_digest_references_are_retired_without_touching_retained_images(self):
        install_source = self.docker.install
        def install_prebuilt(version):
            install_source(version)
            for index, image in enumerate(self.docker.current):
                source = self.docker.images.pop(image["reference"])
                reference = f"ghcr.io/the333tech/{REPOSITORIES[index]}@{image['id']}"
                image["reference"] = reference
                self.docker.images[reference] = {**source, "tags": [], "digests": [reference]}
        with patch.object(self.docker, "install", side_effect=install_prebuilt):
            archives = self.three_updates()
        result = self.retention.apply(self.retention.plan())
        self.assertEqual(len(result["removed_image_references"]), 3)
        self.assertTrue(all("@sha256:" in r for r in result["removed_image_references"]))
        self.assertTrue(all(a.exists() for a in archives[1:]))
        for record in self.retention.read_records()[1:]:
            self.retention.verify_snapshot(record)

    def test_runtime_discovery_rejects_foreign_project_or_changed_reference(self):
        docker = Docker()
        reference = f"{REPOSITORIES[0]}:1.0"
        config = {"services": {SERVICES[0]: {"image": reference}}}
        for labels, actual in (({"com.docker.compose.service": SERVICES[0],
                                  "com.docker.compose.project.working_dir": "/another/project"}, reference),
                               ({"com.docker.compose.service": SERVICES[0],
                                  "com.docker.compose.project.working_dir": str(self.project)}, reference + "-changed")):
            container = {"reference": actual, "image": "sha256:" + "1" * 64, "labels": labels}
            with patch.object(docker, "run", side_effect=[json.dumps(config), json.dumps(container)]):
                with self.assertRaisesRegex(StorageError, "do not match"):
                    docker.current_images(self.project)

    def test_runtime_discovery_checks_all_three_exact_images(self):
        self.docker.install("1.0")
        expected = self.docker.current
        def command(*arguments):
            if arguments[0] == "compose":
                return json.dumps({"services": {i["service"]: {"image": i["reference"]} for i in expected}})
            image = next(i for i in expected if i["service"] == arguments[-1])
            return json.dumps({"image": image["id"], "reference": image["reference"], "labels": {
                "com.docker.compose.service": image["service"], "com.docker.compose.project.working_dir": str(self.project)}})
        docker = Docker()
        with patch.object(docker, "run", side_effect=command), patch.object(docker, "image", side_effect=self.docker.image):
            self.assertEqual(docker.current_images(self.project), expected)

    def test_foreign_alias_protects_old_image(self):
        self.three_updates()
        image = self.docker.images[f"{REPOSITORIES[0]}:1.0"]
        image["tags"].append("other-project:keep")
        plan = self.retention.plan()
        self.assertNotIn(image["id"], {i["id"] for i in plan["images"]})
        self.assertTrue(any("неучтённые ссылки" in s for s in plan["skipped"]))

    def test_shared_current_image_is_not_removed(self):
        self.three_updates()
        old = self.docker.images[f"{REPOSITORIES[0]}:1.0"]
        self.docker.current[0]["id"] = old["id"]
        self.assertNotIn(old["id"], {i["id"] for i in self.retention.plan()["images"]})

    def test_changed_preview_cannot_authorize_deletion(self):
        archives = self.three_updates()
        plan = self.retention.plan()
        self.docker.used.add(plan["images"][0]["id"])
        with self.assertRaisesRegex(StorageError, "plan changed"):
            self.retention.apply(plan)
        self.assertTrue(all(a.exists() for a in archives))
        self.assertEqual(self.docker.removed, [])

    def test_docker_failure_never_deletes_archive(self):
        archives = self.three_updates()
        plan = self.retention.plan()
        with patch.object(self.docker, "remove", side_effect=StorageError("daemon unavailable")):
            with self.assertRaises(StorageError):
                self.retention.apply(plan)
        self.assertTrue(all(a.exists() for a in archives))

    def test_other_daemon_cannot_authorize_cleanup(self):
        self.three_updates()
        self.docker.daemon = "another-daemon"
        self.assertTrue(self.retention.plan()["blockers"])

    def test_archive_and_record_links_are_rejected(self):
        archives = self.three_updates()
        original = archives[-1].read_bytes()
        outside = self.project.parent / "outside.tar.gz"
        outside.write_bytes(original)
        archives[-1].unlink()
        archives[-1].symlink_to(outside)
        self.assertTrue(self.retention.plan()["blockers"])
        record = self.retention.records / (archives[0].name + ".json")
        record.unlink()
        record.symlink_to(outside)
        with self.assertRaises(StorageError):
            self.retention.plan()
        self.assertEqual(outside.read_bytes(), original)

    def test_backup_directory_link_is_rejected(self):
        (self.project / "backups").symlink_to(self.project.parent, target_is_directory=True)
        with self.assertRaises(StorageError):
            Retention(self.project, self.docker)

    def test_hard_linked_archive_is_never_removed(self):
        archives = self.three_updates()
        outside = self.project.parent / "keep-linked-snapshot"
        os.link(archives[-1], outside)
        self.assertTrue(self.retention.plan()["blockers"])
        self.assertTrue(all(a.exists() for a in archives))
        self.assertTrue(outside.exists())

    def test_traversal_or_foreign_record_is_rejected(self):
        archive = self.snapshot("1.0", "1.1", 1)
        path = self.retention.records / (archive.name + ".json")
        record = json.loads(path.read_text())
        record["archive"] = "../foreign.tar.gz"
        path.write_text(json.dumps(record))
        with self.assertRaises(StorageError):
            self.retention.plan()

    def test_incomplete_snapshot_is_never_registered(self):
        (self.project / "VERSION").write_text("1.0")
        self.docker.install("1.0")
        archive = self.archive("1.0", 1)
        with tarfile.open(archive, "w:gz") as handle:
            entry = tarfile.TarInfo("VERSION")
            entry.size = 3
            handle.addfile(entry, io.BytesIO(b"1.0"))
        with self.assertRaises(StorageError):
            self.retention.capture(archive, "1.1")
        self.assertFalse(self.retention.records.exists())

    def test_invalid_retention_cannot_remove_safety_floor(self):
        for keep in (0, 1, 21):
            with self.assertRaises(StorageError):
                Retention(self.project, self.docker, keep)

    def test_records_are_private_and_contain_no_env_contents(self):
        archive = self.snapshot("1.0", "1.1", 1)
        record = self.retention.records / (archive.name + ".json")
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.retention.records.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("PRIVATE_FIXTURE", record.read_text())

    def test_report_separates_backup_families_and_missing_filesystems(self):
        self.snapshot("1.0", "1.1", 1)
        (self.project / "data" / "system_backups").mkdir(parents=True)
        (self.project / "data" / "system_backups" / "backup.zip").write_bytes(b"12345")
        report = storage_report(self.retention)
        self.assertEqual(report["portal_backups"]["bytes"], 5)
        self.assertNotIn("free_bytes", report["docker_filesystem"])
        self.assertIn("error", report["docker_filesystem"])
        self.assertTrue(report["cleanup"]["blockers"])

    def test_report_does_not_claim_containerd_or_lazy_layers_are_fully_measured(self):
        self.snapshot("1.0", "1.1", 1)
        info = {**self.docker.info(), "driver": "stargz", "driver_status": [["driver-type", "io.containerd.snapshotter.v1"]]}
        with patch.object(self.docker, "info", return_value=info):
            report = storage_report(self.retention)
        self.assertEqual(len(report["storage_notes"]), 2)
        self.assertIn("Путь отдельного хранилища не определён", report["storage_notes"][0])
        self.assertIn("не гарантирует все слои", report["storage_notes"][1])
        self.assertTrue(any("lazy-snapshotter" in reason for reason in report["cleanup"]["blockers"]))
        self.assertEqual(report["cleanup"]["files"], [])
        self.assertEqual(report["cleanup"]["images"], [])

    def test_measurement_does_not_follow_external_symlink(self):
        outside = self.project.parent / "large-file"
        outside.write_bytes(b"123456789")
        (self.project / "external").symlink_to(outside)
        self.assertEqual(directory_bytes(self.project)["bytes"], len((self.project / ".env").read_bytes()))

    def test_cli_formats_space_without_turning_unknown_into_zero(self):
        spec = importlib.util.spec_from_file_location("storage_cli_test", Path(__file__).resolve().parents[1] / "scripts" / "storage-maintenance.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        self.assertEqual(cli.size_text(1073741824), "1.0 ГиБ")
        self.assertEqual(cli.measurement_text({"error": "нет доступа"}), "нет доступа")
        self.assertIn("каталог отсутствует", cli.measurement_text({"bytes": 0, "exists": False}))

    def test_cleanup_refuses_update_lock_and_orphaned_operation(self):
        from app.update_runner import prepare_runtime, runtime_identity
        import fcntl
        directory = prepare_runtime(self.project, *runtime_identity(self.project))
        with (directory / "runner.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(StorageError):
                with operation_lock(self.project):
                    self.fail("entered busy operation")
        (directory / "operation.json").write_text('{"status":"recovery_required"}')
        with self.assertRaises(StorageError):
            with operation_lock(self.project):
                self.fail("entered orphaned operation")

    def test_cleanup_locks_out_another_cleanup_and_backend_write(self):
        import fcntl
        with operation_lock(self.project):
            with self.assertRaises(StorageError):
                with operation_lock(self.project):
                    self.fail("second cleanup acquired the same lock")
            with (self.project / "runtime" / "operations" / "writes.lock").open("r+") as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_internal_operation_cannot_skip_lock_without_runner(self):
        with self.assertRaises(StorageError):
            with operation_lock(self.project, internal=True):
                self.fail("internal bypass accepted")

    def test_internal_operation_uses_held_transaction_locks(self):
        import fcntl
        from app.update_runner import prepare_runtime, runtime_identity
        directory = prepare_runtime(self.project, *runtime_identity(self.project))
        (directory / "operation.json").write_text('{"status":"running"}')
        with (directory / "runner.lock").open("r+") as runner, (directory / "writes.lock").open("r+") as writes:
            for handle in (runner, writes):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.dict(os.environ, {"THE333_UPDATE_RUNNER_ACTIVE": "true"}):
                with operation_lock(self.project, internal=True):
                    pass

    def test_docker_missing_is_not_daemon_or_permission_failure(self):
        docker = Docker()
        with patch("app.storage_retention.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "No such image: fixture")):
            self.assertIsNone(docker.image("fixture"))
        for error in ("permission denied", "Cannot connect to daemon"):
            with patch("app.storage_retention.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", error)):
                with self.assertRaises(StorageError):
                    docker.image("fixture")

    def test_docker_remove_never_force_prunes_parents(self):
        with patch("app.storage_retention.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            Docker().remove("the333-bgp-backend:1.0")
        self.assertEqual(run.call_args.args[0], ["docker", "image", "rm", "--no-prune", "the333-bgp-backend:1.0"])


if __name__ == "__main__":
    unittest.main()
