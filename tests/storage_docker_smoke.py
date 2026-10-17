"""Explicit local Docker test. Does not start containers or use the production names."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.storage_retention import Docker, Retention, REPOSITORIES, SERVICES
from test_storage_retention import StorageRetentionTests


class IsolatedDocker(Docker):
    def __init__(self):
        super().__init__()
        self.current = []

    def current_images(self, project):
        # Only container discovery is replaced. All image operations use the real daemon.
        return self.current


def main():
    if os.environ.get("THE333_ALLOW_LOCAL_DOCKER_TEST") != "YES":
        raise SystemExit("Set THE333_ALLOW_LOCAL_DOCKER_TEST=YES for this explicit, local-only test")
    run_id = "retention-qa-" + uuid.uuid4().hex[:12]
    docker = IsolatedDocker()
    references = []
    containers = []
    foreign = "the333-retention-qa:" + run_id
    try:
        with tempfile.TemporaryDirectory(prefix="the333-retention-qa-") as directory:
            project = Path(directory) / "project"
            project.mkdir()
            retention = Retention(project, docker)
            archive_fixture = StorageRetentionTests()
            archive_fixture.project = project
            (project / ".env").write_text("PRIVATE_FIXTURE_ONLY=yes\n")

            def install(version):
                docker.current = []
                for index, repository in enumerate(REPOSITORIES):
                    reference = f"{repository}:{run_id}-{version}"
                    if reference not in references:
                        # A tiny local rootfs; no pull/build cache or remote image is involved.
                        payload = io.BytesIO()
                        import tarfile
                        with tarfile.open(fileobj=payload, mode="w") as archive:
                            entry = tarfile.TarInfo("fixture")
                            value = (run_id + reference).encode()
                            entry.size = len(value)
                            archive.addfile(entry, io.BytesIO(value))
                        subprocess.run(["docker", "image", "import", "-", reference], input=payload.getvalue(),
                                       capture_output=True, check=True, timeout=90)
                        references.append(reference)
                    docker.current.append({"service": SERVICES[index], "reference": reference, "id": docker.image(reference)["id"]})
                (project / "VERSION").write_text(version)

            snapshots = []
            for index in range(3):
                old, new = f"1.{index}", f"1.{index + 1}"
                install(old)
                archive = archive_fixture.archive(old, index + 1)
                retention.capture(archive, new)
                install(new)
                retention.complete(archive)
                snapshots.append(archive)
            before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in snapshots}
            oldest = json.loads((retention.records / (snapshots[0].name + ".json")).read_text())
            docker.run("image", "tag", oldest["images"][0]["reference"], foreign)
            references.append(foreign)
            container = "the333-" + run_id
            docker.run("container", "create", "--name", container, "--network", "none", "--entrypoint", "/not-started",
                       oldest["images"][1]["reference"])
            containers.append(container)
            protected_plan = retention.plan()
            assert len(protected_plan["images"]) == 1, protected_plan
            assert all(path.exists() for path in snapshots)
            docker.run("container", "rm", container)
            containers.remove(container)
            docker.remove(foreign)
            references.remove(foreign)
            plan = retention.plan()
            assert len(plan["images"]) == 3 and len(plan["files"]) == 1, plan
            result = retention.apply(plan)
            assert len(result["removed_image_references"]) == 3, result
            assert not snapshots[0].exists()
            for record in retention.read_records():
                if record["status"] == "succeeded":
                    retention.verify_snapshot(record)
                    assert hashlib.sha256((retention.backups / record["archive"]).read_bytes()).hexdigest() == before[record["archive"]]
            print(json.dumps({"ok": True, "snapshots_retained": 2, "old_images_removed": 3,
                              "stopped_container_protected": True, "foreign_alias_protected": True,
                              "production_container_names_used": False}))
    finally:
        for name in containers:
            docker.run("container", "rm", name)
        for reference in reversed(references):
            if docker.image(reference) is not None:
                docker.remove(reference)


if __name__ == "__main__":
    main()
