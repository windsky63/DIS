from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from backend.job_store import ActiveJobExists, JobStore
from backend.api.services.queue import review_summary


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
