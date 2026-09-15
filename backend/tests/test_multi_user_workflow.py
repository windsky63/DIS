from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from backend.auth_store import AuthStore
from backend.job_store import JobStore
from backend.page_lock_store import PageLockConflict, PageLockStore
from backend.page_reviews import merge_review_page


class MultiUserWorkflowTests(unittest.TestCase):
    def test_creator_and_two_independent_page_reviews_survive_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            database = root / "jobs.db"
            auth = AuthStore(database)
            jobs = JobStore(database)
            locks = PageLockStore(database)
            auth.initialize(); jobs.initialize(); locks.initialize()
            alice = auth.register("alice", "correct-horse-battery")
            bob = auth.register("bob", "correct-horse-battery")
            job_id = "a" * 32
            job_folder = root / job_id
            job_folder.mkdir()
            jobs.create_job(
                job_id=job_id, analysis_signature="sig", job_folder=job_folder,
                original_target_name="drawing.pdf", algorithm_version="test",
                creator_user_id=alice["userId"], creator_username=alice["username"],
            )
            result = {
                "status": "complete", "analyzedRange": [1, 2],
                "pages": [
                    {"page": 1, "candidates": [{"id": "a", "page": 1, "number": ""}]},
                    {"page": 2, "candidates": [{"id": "b", "page": 2, "number": ""}]},
                ],
            }
            alice_lock = locks.acquire(job_id, 1, str(alice["userId"]), "alice", "alice-tab")
            bob_lock = locks.acquire(job_id, 2, str(bob["userId"]), "bob", "bob-tab")
            with self.assertRaises(PageLockConflict):
                locks.acquire(job_id, 1, str(bob["userId"]), "bob", "bob-tab")
            locks.assert_owner(job_id, 1, str(alice["userId"]), "alice-tab", str(alice_lock["lockToken"]))
            merge_review_page(result, 1, {"page": 1, "candidates": [{"id": "a", "page": 1, "number": "A1"}]}, 0, alice)
            locks.assert_owner(job_id, 2, str(bob["userId"]), "bob-tab", str(bob_lock["lockToken"]))
            merge_review_page(result, 2, {"page": 2, "candidates": [{"id": "b", "page": 2, "number": "B1"}]}, 0, bob)
            (job_folder / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

            reopened_jobs = JobStore(database)
            reopened_jobs.initialize()
            reopened_result = json.loads((job_folder / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(reopened_jobs.get(job_id)["createdBy"]["username"], "alice")
            self.assertEqual([page["candidates"][0]["number"] for page in reopened_result["pages"]], ["A1", "B1"])
            self.assertEqual([page["reviewRevision"] for page in reopened_result["pages"]], [1, 1])


if __name__ == "__main__":
    unittest.main()
