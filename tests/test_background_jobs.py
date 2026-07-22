import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class BackgroundJobRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.jobs_file = self.root / "jobs.json"
        self.results = self.root / "host-updater-results"
        self.results.mkdir()
        self.patches = [
            patch.object(main, "JOBS_FILE", self.jobs_file),
            patch.object(main, "HOST_UPDATER_RESULT_DIR", self.results),
        ]
        for current in self.patches:
            current.start()
        main.RUNNING_JOB_IDS.clear()

    def tearDown(self) -> None:
        main.RUNNING_JOB_IDS.clear()
        for current in reversed(self.patches):
            current.stop()
        self.temp_dir.cleanup()

    def old_timestamp(self) -> str:
        return (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    def test_product_update_is_reconciled_from_durable_result(self) -> None:
        job_id = "a" * 32
        state = {
            "version": 1,
            "jobs": [
                {
                    "id": job_id,
                    "kind": "product_update",
                    "key": "product_update",
                    "status": "running",
                    "stage": "Выполняется",
                    "created_at": self.old_timestamp(),
                    "started_at": self.old_timestamp(),
                }
            ],
        }
        (self.results / f"{job_id}.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "status": "succeeded",
                    "version": "0.82b",
                    "channel": "beta",
                    "returncode": 0,
                    "duration_seconds": 42,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        self.assertTrue(main.reconcile_jobs_state(state))
        job = state["jobs"][0]
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["result_summary"]["version"], "0.82b")
        self.assertEqual(job["progress_percent"], 100)

    def test_interrupted_non_update_job_fails_closed_after_restart(self) -> None:
        state = {
            "version": 1,
            "jobs": [
                {
                    "id": "b" * 32,
                    "kind": "route_update",
                    "status": "running",
                    "created_at": self.old_timestamp(),
                    "started_at": self.old_timestamp(),
                }
            ],
        }
        self.assertTrue(main.reconcile_jobs_state(state))
        self.assertEqual(state["jobs"][0]["status"], "failed")
        self.assertIn("перезапуском", state["jobs"][0]["stage"])

    def test_current_process_job_is_not_reconciled(self) -> None:
        job_id = "c" * 32
        state = {
            "version": 1,
            "jobs": [
                {
                    "id": job_id,
                    "kind": "route_update",
                    "status": "running",
                    "created_at": self.old_timestamp(),
                    "started_at": self.old_timestamp(),
                }
            ],
        }
        main.RUNNING_JOB_IDS.add(job_id)
        self.assertFalse(main.reconcile_jobs_state(state))
        self.assertEqual(state["jobs"][0]["status"], "running")

    def test_only_not_started_job_is_cancellable(self) -> None:
        queued = main.public_job_record({"id": "d" * 32, "status": "queued"})
        running = main.public_job_record({"id": "e" * 32, "status": "running"})
        self.assertTrue(queued["cancellable"])
        self.assertFalse(running["cancellable"])

    def test_cancelled_job_cannot_be_claimed_after_spawn_race(self) -> None:
        job_id = "f" * 32
        self.jobs_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "jobs": [
                        {
                            "id": job_id,
                            "kind": "route_update",
                            "status": "cancelled",
                            "cancel_requested": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.assertFalse(main.claim_queued_job(job_id))
        self.assertNotIn(job_id, main.RUNNING_JOB_IDS)

    def test_claim_transitions_queued_job_exactly_once(self) -> None:
        job_id = "1" * 32
        self.jobs_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "jobs": [
                        {
                            "id": job_id,
                            "kind": "route_update",
                            "status": "queued",
                            "cancel_requested": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.assertTrue(main.claim_queued_job(job_id))
        self.assertFalse(main.claim_queued_job(job_id))
        claimed = json.loads(self.jobs_file.read_text(encoding="utf-8"))["jobs"][0]
        self.assertEqual(claimed["status"], "running")
        self.assertIn(job_id, main.RUNNING_JOB_IDS)

    def test_restore_is_exclusive_with_other_background_jobs(self) -> None:
        active_id = "2" * 32
        self.jobs_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "jobs": [
                        {
                            "id": active_id,
                            "kind": "system_backup",
                            "key": "system_backup",
                            "status": "running",
                            "started_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        main.RUNNING_JOB_IDS.add(active_id)
        with self.assertRaises(main.HTTPException) as caught:
            main.create_job("system_restore", "system_restore", "Restore")
        self.assertEqual(caught.exception.status_code, 409)

    def test_active_restore_blocks_new_route_job(self) -> None:
        active_id = "3" * 32
        self.jobs_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "jobs": [
                        {
                            "id": active_id,
                            "kind": "system_restore",
                            "key": "system_restore",
                            "status": "running",
                            "started_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        main.RUNNING_JOB_IDS.add(active_id)
        with self.assertRaises(main.HTTPException) as caught:
            main.create_job("route_update", "route_update", "Routes")
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
