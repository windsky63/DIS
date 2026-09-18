from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import time
import unittest
from unittest.mock import patch

from backend.job_store import ActiveJobExists, JobStore
from backend.api.services.queue import review_summary, snapshot


class JobStoreTests(unittest.TestCase):
    def _create(self, store: JobStore, root: Path, job_id: str, signature: str) -> None:
        folder = root / job_id
        folder.mkdir()
        store.create_job(
            job_id=job_id,
            analysis_signature=signature,
            job_folder=folder,
            original_target_name=f"{job_id}.pdf",
            algorithm_version="test",
        )

    def test_claim_is_atomic_and_active_signature_is_unique(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            store = JobStore(root / "jobs.db")
            store.initialize()
            self._create(store, root, "a" * 32, "same")
            with self.assertRaises(ActiveJobExists):
                self._create(store, root, "b" * 32, "same")
            claimed = store.claim_next("worker-1", 60)
            self.assertEqual(claimed["job_id"], "a" * 32)
            self.assertIsNone(store.claim_next("worker-2", 60))

    def test_workspace_pages_only_hydrate_the_requested_page(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            store = JobStore(root / "jobs.db")
            store.initialize()
            job_id = "c" * 32
            self._create(store, root, job_id, "workspace-pages")
            store.replace_job_pages(job_id, [
                {
                    "page": 1,
                    "candidates": [{"id": "p1", "included": True, "referenceMatched": False}],
                    "layoutObstacles": {"textRects": [[1, 2, 3, 4]]},
                },
                {
                    "page": 2,
                    "candidates": [{"id": "p2", "included": True, "referenceMatched": True, "referenceLabel": "W2"}],
                    "layoutObstacles": {"textRects": [[5, 6, 7, 8]]},
                },
            ])

            pages = store.get_job_workspace_pages(job_id, 2)

        self.assertEqual(pages[0]["candidates"], [])
        self.assertNotIn("layoutObstacles", pages[0])
        self.assertFalse(pages[0]["detailsLoaded"])
        self.assertEqual(pages[0]["candidateCount"], 1)
        self.assertEqual(pages[0]["matchSummary"]["unmatched"], 1)
        self.assertEqual(pages[1]["candidates"][0]["id"], "p2")
        self.assertTrue(pages[1]["detailsLoaded"])

    def test_queue_snapshot_uses_database_summary_without_reading_task_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "f" * 32
            folder = root / job_id
            folder.mkdir()
            store = JobStore(root / "jobs.db")
            store.initialize()
            store.create_job(
                job_id=job_id,
                analysis_signature="summary-only",
                job_folder=folder,
                original_target_name="drawing.pdf",
                algorithm_version="test",
                queue_summary={
                    "totalPages": 4,
                    "completedPages": 4,
                    "progressStage": "complete",
                    "progressPercent": 100,
                    "referenceFileCount": 2,
                    "project": {"name": "摘要项目"},
                },
            )
            claimed = store.claim_next("worker-test", 60)
            store.replace_job_pages(job_id, [{"page": 1, "candidates": []}])
            self.assertTrue(store.finish(job_id, "complete", worker_id=str(claimed["lease_owner"])))

            with patch.object(Path, "read_text", side_effect=AssertionError("queue read a task file")):
                result = snapshot(store, root, 2)

        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["fileName"], "drawing.pdf")
        self.assertEqual(result["jobs"][0]["pageCount"], 4)
        self.assertEqual(result["jobs"][0]["drawingCount"], 3)
        self.assertEqual(result["jobs"][0]["project"], {"name": "摘要项目"})
        self.assertTrue(result["jobs"][0]["canRestore"])

    def test_queue_snapshot_pages_current_and_archived_jobs_before_loading_review_details(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            store = JobStore(root / "jobs.db")
            store.initialize()
            job_ids = [f"{index:032x}" for index in range(1, 6)]
            for index, job_id in enumerate(job_ids, start=1):
                self._create(store, root, job_id, f"signature-{index}")
                claimed = store.claim_next(f"worker-{index}", 60)
                store.replace_job_pages(job_id, [{"page": 1, "candidates": []}])
                self.assertTrue(store.finish(job_id, "complete", worker_id=str(claimed["lease_owner"])))
            with store._connection() as connection:
                for index, job_id in enumerate(job_ids, start=1):
                    connection.execute(
                        "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
                        (f"2026-09-17T10:00:0{index}", job_id),
                    )
                connection.execute(
                    "UPDATE jobs SET archived_at = ? WHERE job_id = ?",
                    ("2026-09-17T11:00:00", job_ids[-1]),
                )

            with (
                patch.object(store, "get_job_page_summaries", side_effect=AssertionError("used per-job page query")),
                patch.object(store, "page_review_history", side_effect=AssertionError("used per-job review query")),
            ):
                current = snapshot(store, root, 2, scope="current", page=2, page_size=2)
                archived = snapshot(store, root, 2, scope="archived", page=1, page_size=2)

        self.assertEqual([item["jobId"] for item in current["jobs"]], [job_ids[1], job_ids[0]])
        self.assertEqual(current["pagination"], {
            "scope": "current", "page": 2, "pageSize": 2, "totalItems": 4, "totalPages": 2,
        })
        self.assertEqual([item["jobId"] for item in archived["jobs"]], [job_ids[-1]])
        self.assertEqual(archived["pagination"], {
            "scope": "archived", "page": 1, "pageSize": 2, "totalItems": 1, "totalPages": 1,
        })

    def test_expired_lease_is_requeued_then_failed_after_retry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            store = JobStore(root / "jobs.db")
            store.initialize()
            self._create(store, root, "c" * 32, "retry")
            store.claim_next("worker-1", 1)
            with store._connection() as connection:
                connection.execute(
                    "UPDATE jobs SET lease_expires_at = ? WHERE job_id = ?",
                    (time.time() - 1, "c" * 32),
                )
            self.assertEqual(store.requeue_expired()["requeued"], ["c" * 32])
            store.claim_next("worker-2", 1)
            with store._connection() as connection:
                connection.execute(
                    "UPDATE jobs SET lease_expires_at = ? WHERE job_id = ?",
                    (time.time() - 1, "c" * 32),
                )
            self.assertEqual(store.requeue_expired()["failed"], ["c" * 32])

    def test_queued_cancellation_is_terminal_without_worker(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            store = JobStore(root / "jobs.db")
            store.initialize()
            self._create(store, root, "d" * 32, "cancel")
            self.assertEqual(store.request_cancel("d" * 32), "cancelled")
            self.assertIsNone(store.claim_next("worker", 60))

    def test_job_records_creator_and_migrates_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            path = root / "jobs.db"
            store = JobStore(path)
            store.initialize()
            with store._connection() as connection:
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
                if "creator_user_id" in columns:
                    connection.execute("ALTER TABLE jobs DROP COLUMN creator_user_id")
                if "creator_username" in columns:
                    connection.execute("ALTER TABLE jobs DROP COLUMN creator_username")

            store.initialize()
            folder = root / ("e" * 32)
            folder.mkdir()
            created = store.create_job(
                job_id="e" * 32,
                analysis_signature="creator",
                job_folder=folder,
                original_target_name="drawing.pdf",
                algorithm_version="test",
                creator_user_id="user-1",
                creator_username="Reviewer01",
            )

            self.assertEqual(created["creator_user_id"], "user-1")
            self.assertEqual(created["creator_username"], "Reviewer01")
            self.assertEqual(created["createdBy"], {"userId": "user-1", "username": "Reviewer01"})

    def test_latest_completed_batch_is_selected_from_structured_job_columns(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            store = JobStore(root / "jobs.db")
            store.initialize()
            jobs = [
                ("1" * 32, "older-batch", 0, "2026-09-17T10:00:00"),
                ("2" * 32, "latest-batch", 1, "2026-09-18T10:00:02"),
                ("3" * 32, "latest-batch", 0, "2026-09-18T10:00:01"),
            ]
            for index, (job_id, batch_id, batch_index, completed_at) in enumerate(jobs):
                folder = root / job_id
                folder.mkdir()
                store.create_job(
                    job_id=job_id,
                    analysis_signature=f"batch-{index}",
                    job_folder=folder,
                    original_target_name=f"{index}.pdf",
                    algorithm_version="algorithm-v1",
                    batch_id=batch_id,
                    batch_index=batch_index,
                )
                claimed = store.claim_next(f"worker-{index}", 60)
                store.replace_job_pages(job_id, [{"page": 1, "candidates": []}])
                self.assertTrue(store.finish(job_id, "complete", worker_id=str(claimed["lease_owner"])))
                with store._connection() as connection:
                    connection.execute(
                        "UPDATE jobs SET completed_at = ? WHERE job_id = ?",
                        (completed_at, job_id),
                    )

            selected = store.latest_completed_batch("algorithm-v1")

            self.assertEqual([item["job_id"] for item in selected], ["3" * 32, "2" * 32])
            self.assertEqual([item["batch_index"] for item in selected], [0, 1])
            self.assertEqual({item["batch_id"] for item in selected}, {"latest-batch"})
            self.assertTrue(all(item["completed_at"] for item in selected))

    def test_initialize_backfills_batch_columns_for_existing_completed_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "4" * 32
            folder = root / job_id
            folder.mkdir()
            (folder / "job.json").write_text(
                '{"batchId":"legacy-batch","batchIndex":3}', encoding="utf-8",
            )
            (folder / "result.json").write_text('{"status":"complete"}', encoding="utf-8")
            store = JobStore(root / "jobs.db")
            store.initialize()
            store.create_job(
                job_id=job_id, analysis_signature="legacy", job_folder=folder,
                original_target_name="legacy.pdf", algorithm_version="algorithm-v1",
            )
            claimed = store.claim_next("legacy-worker", 60)
            store.replace_job_pages(job_id, [{"page": 1, "candidates": []}])
            self.assertTrue(store.finish(job_id, "complete", worker_id=str(claimed["lease_owner"])))
            with store._connection() as connection:
                connection.execute("DROP INDEX jobs_recent_batch")
                connection.execute("ALTER TABLE jobs DROP COLUMN batch_id")
                connection.execute("ALTER TABLE jobs DROP COLUMN batch_index")
                connection.execute("ALTER TABLE jobs DROP COLUMN completed_at")

            store.initialize()
            restored = store.get(job_id)

            self.assertEqual(restored["batch_id"], "legacy-batch")
            self.assertEqual(restored["batch_index"], 3)
            self.assertTrue(restored["completed_at"])

    def test_page_review_events_preserve_each_successful_page_save(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            store = JobStore(root / "jobs.db")
            store.initialize()
            job_id = "f" * 32
            self._create(store, root, job_id, "reviews")
            store.record_page_review(job_id, 3, 1, {"userId": "u1", "username": "张三"}, "2026-09-15T10:00:00")
            store.record_page_review(job_id, 3, 2, {"userId": "u2", "username": "李四"}, "2026-09-15T10:10:00")
            store.record_page_review(job_id, 3, 2, {"userId": "u2", "username": "李四"}, "2026-09-15T10:10:00")

            history = store.page_review_history(job_id)
            self.assertEqual(len(history), 2)
            self.assertEqual([item["revision"] for item in history], [1, 2])
            self.assertEqual([item["username"] for item in history], ["张三", "李四"])

    def test_pages_are_stored_and_saved_as_independent_database_rows(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            store = JobStore(root / "jobs.db")
            store.initialize()
            job_id = "9" * 32
            self._create(store, root, job_id, "page-rows")
            store.replace_job_pages(job_id, [
                {"page": 1, "candidates": [{"id": "a", "page": 1, "number": ""}]},
                {"page": 2, "candidates": [{"id": "b", "page": 2, "number": ""}]},
            ])

            def save(page_number: int, number: str) -> dict:
                return store.save_job_page(
                    job_id, page_number,
                    {"page": page_number, "candidates": [{
                        "id": "a" if page_number == 1 else "b",
                        "page": page_number, "number": number,
                    }]},
                    0, {"userId": f"u{page_number}", "username": f"用户{page_number}"},
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                saved = list(executor.map(lambda args: save(*args), [(1, "A1"), (2, "B1")]))

            self.assertEqual([page["reviewRevision"] for page in saved], [1, 1])
            reopened = JobStore(root / "jobs.db")
            reopened.initialize()
            pages = reopened.get_job_pages(job_id)
            self.assertEqual([page["candidates"][0]["number"] for page in pages], ["A1", "B1"])
            self.assertEqual([page["reviewRevision"] for page in pages], [1, 1])

    def test_queue_review_summary_groups_users_by_page_and_keeps_unreviewed_pages(self) -> None:
        summary = review_summary([
            {"page_number": 1, "revision": 1, "user_id": "u1", "username": "张三", "saved_at": "2026-09-15T10:00:00"},
            {"page_number": 1, "revision": 2, "user_id": "u1", "username": "张三", "saved_at": "2026-09-15T10:05:00"},
            {"page_number": 1, "revision": 3, "user_id": "u2", "username": "李四", "saved_at": "2026-09-15T10:10:00"},
        ], [{"page": 1}, {"page": 2}])

        self.assertEqual(summary["reviewedPageCount"], 1)
        self.assertEqual(summary["pageReviews"][0]["saveCount"], 3)
        self.assertEqual(summary["pageReviews"][0]["reviewers"][0]["username"], "张三")
        self.assertEqual(summary["pageReviews"][0]["reviewers"][0]["saveCount"], 2)
        self.assertEqual(summary["pageReviews"][1]["saveCount"], 0)

    def test_queue_review_summary_keeps_only_five_most_recent_events_across_reviewers(self) -> None:
        events = [
            {
                "page_number": index,
                "revision": 1,
                "user_id": f"u{index % 3}",
                "username": f"审核人{index % 3}",
                "saved_at": f"2026-09-15T10:0{index}:00",
            }
            for index in range(1, 7)
        ]

        summary = review_summary(events, [{"page": index} for index in range(1, 7)])

        self.assertEqual(len(summary["recentReviewEvents"]), 5)
        self.assertEqual([item["page"] for item in summary["recentReviewEvents"]], [6, 5, 4, 3, 2])
        self.assertEqual(summary["recentReviewEvents"][0]["username"], "审核人0")


if __name__ == "__main__":
    unittest.main()
