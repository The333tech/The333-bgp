import copy
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.update_storage import (MIB, RESERVE, LEGACY_IMAGE_BUDGET, REPOSITORIES, Plan, archive_footprint,
                                build_plan, configured_floor, containerd_root, filesystem_reserve,
                                footprint, image_budget, layer_chains)

ROOT = Path(__file__).resolve().parents[1]
REFS = [repo + "@sha256:" + str(index) * 64 for index, repo in enumerate(REPOSITORIES.values(), 1)]


def metadata():
    return {"schema": 1, "platforms": {"linux/amd64": {
        name: {"reference": ref, "unpacked_bytes": 40 * MIB, "compressed_bytes": 10 * MIB,
               "layers": [{"digest": "sha256:" + str(index) * 64, "diff_id": "sha256:" + str(index + 3) * 64,
                           "compressed_bytes": 10 * MIB, "unpacked_bytes": 40 * MIB}]}
        for index, (name, ref) in enumerate(zip(REPOSITORIES, REFS, strict=True), 1)
    }}}


class StorageBudgetTests(unittest.TestCase):
    def test_shipped_defaults_become_automatic_but_operator_floor_is_preserved(self):
        for raw in ("0", "1073741824", "2147483648"):
            self.assertEqual(configured_floor(raw), 0)
        self.assertEqual(configured_floor("1610612736"), 1536 * MIB)
        self.assertEqual(configured_floor("1073741824", policy="adaptive"), 1024 * MIB)
        self.assertEqual(configured_floor("0", "source"), 1024 * MIB)
        for invalid in ("-1", "", "bad", "1.5", "9" * 30):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                configured_floor(invalid)

    def test_shared_filesystem_sums_costs_but_keeps_one_reserve(self):
        with patch("app.update_storage.shutil.disk_usage", return_value=SimpleNamespace(total=8 * 1024 * MIB, free=500 * MIB)):
            plan = Plan()
            plan.add(ROOT, "backup", 20 * MIB)
            plan.add(ROOT / "app", "images", 200 * MIB)
        report = plan.report()
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["required_bytes"], 348 * MIB)
        self.assertTrue(report[0]["ready"])
        with patch("app.update_storage.shutil.disk_usage", return_value=SimpleNamespace(total=8 * 1024 * MIB, free=2000 * MIB)):
            plan = Plan(1536 * MIB)
            plan.add(ROOT, "images", 200 * MIB)
        self.assertEqual(plan.report()[0]["required_bytes"], 1536 * MIB)

    def test_separate_filesystems_cannot_borrow_space_from_each_other(self):
        probes = []
        for device in (11, 22):
            probe = Mock()
            probe.stat.return_value.st_dev = device
            probes.append(probe)
        with patch("app.update_storage.existing_path", side_effect=probes), patch(
            "app.update_storage.shutil.disk_usage", side_effect=[SimpleNamespace(total=8 * 1024 * MIB, free=129 * MIB),
                                                                SimpleNamespace(total=32 * 1024 * MIB, free=20 * 1024 * MIB)]):
            plan = Plan()
            plan.add(Path("/project"), "backup", 2 * MIB)
            plan.add(Path("/docker"), "images", 200 * MIB)
        self.assertEqual([item["ready"] for item in plan.report()], [False, True])

    def test_small_temporary_filesystem_is_not_forced_to_reserve_128_mib(self):
        self.assertEqual(filesystem_reserve(64 * MIB), 8 * MIB)
        self.assertEqual(filesystem_reserve(8 * 1024 * MIB), RESERVE)

    def test_cold_and_warm_images_have_different_requirements(self):
        cold = image_budget(metadata(), REFS, set(), "amd64")
        self.assertEqual(cold, 196 * MIB)
        self.assertEqual(image_budget(metadata(), REFS, set(REFS), "amd64"), 0)
        self.assertEqual(image_budget(metadata(), REFS, {REFS[0]}, "amd64"), 136 * MIB)
        self.assertEqual(image_budget(None, REFS, set(), "amd64"), LEGACY_IMAGE_BUDGET)
        self.assertEqual(image_budget(None, REFS, set(REFS), "amd64"), 0)

    def test_shared_layers_are_only_counted_once_and_parent_chain_matters(self):
        data = metadata()
        images = data["platforms"]["linux/amd64"]
        images["backend"]["layers"] = copy.deepcopy(images["core"]["layers"])
        self.assertEqual(image_budget(data, REFS, set(), "amd64"), 136 * MIB)
        cached = set(layer_chains([images["core"]["layers"][0]["diff_id"]]))
        self.assertEqual(image_budget(data, REFS, set(), "amd64", cached), 86 * MIB)
        first, second = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        self.assertNotEqual(layer_chains([first, second])[-1], layer_chains([second])[-1])

    def test_vfs_budgets_whole_parent_copies_and_unknown_drivers_fail_closed(self):
        data = metadata()
        layers = data["platforms"]["linux/amd64"]["core"]["layers"]
        layers.append(copy.deepcopy(data["platforms"]["linux/amd64"]["backend"]["layers"][0]))
        self.assertEqual(image_budget(data, REFS, set(), "amd64", driver="vfs"), 296 * MIB)
        with self.assertRaises(ValueError):
            image_budget(None, REFS, set(), "amd64", driver="vfs")
        with self.assertRaises(ValueError):
            image_budget(data, REFS, set(), "amd64", driver="unknown")
        self.assertEqual(image_budget(data, REFS, set(REFS), "amd64", driver="unknown"), 0)

    def test_sizes_are_bound_to_platform_and_immutable_digest(self):
        data = metadata()
        data["platforms"]["linux/amd64"]["backend"]["reference"] = REFS[0]
        with self.assertRaises(ValueError):
            image_budget(data, REFS, set(), "amd64")
        with self.assertRaises(ValueError):
            image_budget(metadata(), REFS, set(), "arm64")
        for size in (True, 0, -1, 2**63, "1"):
            data = metadata()
            data["platforms"]["linux/amd64"]["backend"]["unpacked_bytes"] = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                image_budget(data, REFS, set(), "amd64")

    def test_reused_core_is_not_forced_to_new_release_digest_when_already_present(self):
        refs = [REFS[0][:-64] + "f" * 64, *REFS[1:]]
        self.assertEqual(image_budget(metadata(), refs, {refs[0]}, "amd64"), 136 * MIB)

    def test_footprint_counts_small_files_and_sparse_logical_size_not_node_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "data" / "small").write_text("x")
            (root / "portal" / "node_modules").mkdir(parents=True)
            with (root / "portal" / "node_modules" / "excluded").open("wb") as handle:
                handle.truncate(1024 * MIB)
            with (root / "data" / "sparse").open("wb") as handle:
                handle.truncate(10 * MIB)
            self.assertGreater(footprint(root), 10 * MIB)
            self.assertLess(footprint(root), 11 * MIB)

    def test_snapshot_gzip_size_is_not_a_rollback_space_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "snapshot.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                entry = tarfile.TarInfo("data/compressible")
                entry.size = 4 * MIB
                handle.addfile(entry, io.BytesIO(bytes(entry.size)))
            self.assertLess(archive.stat().st_size, MIB)
            self.assertGreater(archive_footprint(archive), 4 * MIB)

    def test_phase_checks_keep_backup_and_do_not_recharge_loaded_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, release = root / "project", root / "release"
            project.mkdir()
            release.mkdir()
            (project / "data").mkdir()
            (project / "data" / "state").write_bytes(bytes(MIB))
            (release / "VERSION").write_text("0.94b")
            info = {"DockerRootDir": str(root), "Driver": "overlay2", "DriverStatus": []}
            with patch.dict(os.environ, {"UPDATE_MIN_FREE_BYTES": "0"}), patch(
                    "app.update_storage.command_json", return_value=info):
                initial = build_plan(project, "initial", ["docker"])[0]
                before = build_plan(project, "backup", ["docker"], release)[0]
                with self.assertRaises(ValueError):
                    build_plan(project, "activate", ["docker"], release)
                backup = root / "backup.tar.gz"
                with tarfile.open(backup, "w:gz") as handle:
                    handle.add(project / "data", arcname="data")
                after = build_plan(project, "activate", ["docker"], release, backup=backup)[0]
                installed = build_plan(project, "installed", ["docker"], release)[0]
            self.assertEqual(initial["required_bytes"], RESERVE)
            self.assertIn("mandatory backup", before["components"])
            self.assertNotIn("mandatory backup", after["components"])
            self.assertLess(after["required_bytes"], before["required_bytes"])
            self.assertEqual(installed["required_bytes"], RESERVE)
            self.assertEqual(installed["components"]["new release files"], 0)

    def test_package_setup_does_not_require_docker(self):
        with patch("app.update_storage.command_json", side_effect=AssertionError("Docker was called")):
            report = build_plan(ROOT, "setup", ["docker"])
        self.assertTrue(report)

    def test_mode_switch_counts_images_but_not_unchanged_data_or_release_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            project = Path(directory)
            (project / "docker").mkdir()
            (project / "docker/image-storage.json").write_text(json.dumps(metadata()))
            (project / "data").mkdir()
            with (project / "data/large").open("wb") as handle:
                handle.truncate(2 * 1024 * MIB)
            info = {"DockerRootDir": directory, "Driver": "overlay2", "DriverStatus": []}
            def inspect(_command, *args):
                return info if args[0] == "info" else []
            with patch("app.update_storage.command_json", side_effect=inspect), patch(
                    "app.update_storage.platform.machine", return_value="x86_64"), patch(
                    "app.update_storage.shutil.disk_usage", return_value=SimpleNamespace(total=8 * 1024 * MIB, free=324 * MIB)):
                result = build_plan(project, "mode", ["docker"], project, REFS)[0]
            self.assertEqual(result["required_bytes"], 324 * MIB)
            self.assertTrue(result["ready"])
            self.assertEqual(result["components"]["rollback extraction"], 0)
            self.assertEqual(result["components"]["new release files"], 0)
            self.assertNotIn("mandatory backup", result["components"])

    def test_repair_reads_operator_floor_from_env_file_without_sourcing_it(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            project = Path(directory)
            (project / ".env").write_text("UPDATE_STORAGE_POLICY=adaptive\nUPDATE_MIN_FREE_BYTES=2147483648\n")
            info = {"DockerRootDir": directory, "Driver": "overlay2", "DriverStatus": []}
            with patch("app.update_storage.command_json", return_value=info):
                report = build_plan(project, "initial", ["docker"])
                self.assertEqual(report[0]["required_bytes"], 2048 * MIB)
                with patch.dict(os.environ, {"UPDATE_MIN_FREE_BYTES": "0"}):
                    self.assertEqual(build_plan(project, "initial", ["docker"])[0]["required_bytes"], RESERVE)

    def test_containerd_root_comes_from_active_process_not_an_unrelated_default(self):
        info = {"DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"UPDATE_CONTAINERD_ROOT": ""}):
            proc = Path(directory)
            (proc / "12").mkdir()
            (proc / "12" / "cmdline").write_bytes(b"/usr/bin/containerd\0--root\0/custom/images\0")
            self.assertEqual(containerd_root(info, proc), Path("/custom/images"))
            (proc / "13").mkdir()
            (proc / "13" / "cmdline").write_bytes(b"/usr/bin/containerd\0--root=/other/images\0")
            with self.assertRaises(ValueError):
                containerd_root(info, proc)
            with patch.dict(os.environ, {"UPDATE_CONTAINERD_ROOT": directory}):
                self.assertEqual(containerd_root(info, proc), proc)
        self.assertIsNone(containerd_root({"DriverStatus": []}))

    def test_apt_budget_uses_selected_package_versions_and_dependencies(self):
        spec = importlib.util.spec_from_file_location("apt_storage_test", ROOT / "scripts/check-apt-storage.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        responses = ["Inst docker-ce (1.0 repo [amd64])\nInst libdep [0.9] (2.0 repo [amd64])\n",
                     "Version: 1.0\nSize: 1024\nInstalled-Size: 2048\n",
                     "Version: 2.0\nSize: 2048\nInstalled-Size: 1024\n"]
        with patch.object(module.subprocess, "check_output", side_effect=responses) as called:
            report = module.transaction_budget(["docker-ce"])
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["components"]["package downloads"], 3072)
        self.assertIn("libdep=2.0", called.call_args_list[-1].args[0])
        with self.assertRaises(ValueError):
            module.transaction_budget(["--bad"])


if __name__ == "__main__":
    unittest.main()
