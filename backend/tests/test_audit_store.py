from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from audit_store import AuditStore
from job_store import JobStore


class AuditStoreTests(unittest.TestCase):
    def test_combines_filters_and_pages_database_and_jsonl_events(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            database = root / "jobs.db"
            audit_root = root / "audit"
            store = JobStore(database)
            store.initialize()
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """INSERT INTO jobs(job_id, analysis_signature, status, job_folder,
                       original_target_name, algorithm_version, summary_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("a" * 32, "sig", "complete", str(root), "A图.pdf", "v1", "{}",
                     "2026-09-20T08:00:00", "2026-09-20T08:00:00"),
                )
                connection.execute(
                    "INSERT INTO job_events(job_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                    ("a" * 32, "complete", '{"workerId":"worker-1"}', "2026-09-20T09:00:00"),
                )
                connection.execute(
                    """INSERT INTO page_review_events(job_id, page_number, revision, user_id, username, saved_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("a" * 32, 2, 3, "u1", "审核员", "2026-09-20T10:00:00"),
                )
                connection.execute(
                    """INSERT INTO business_events(action, user_id, username, job_id, page_number, amount, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("pdf.exported", "u1", "审核员", "a" * 32, None, 1, "2026-09-20T11:00:00"),
                )
                connection.execute(
                    """INSERT INTO business_events(action, user_id, username, job_id, page_number, amount, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("tutorial.exported", "u1", "审核员", "tutorial-000207", None, 1, "2026-09-20T11:30:00"),
                )
                connection.commit()
            finally:
                connection.close()
            audit_root.mkdir()
            (audit_root / "events.jsonl").write_text(
                json.dumps({
                    "action": "auth.login", "source": "server", "timestamp": "2026-09-20T12:00:00",
                    "actor": {"userId": "u1", "username": "审核员"}, "details": {},
                }, ensure_ascii=False) + "\n" + json.dumps({
                    "action": "marker.deleted", "source": "client", "timestamp": "2026-09-20T12:01:00",
                    "actor": {"userId": "u1", "username": "审核员"},
                    "details": {"jobId": "tutorial-000207", "page": 1},
                }, ensure_ascii=False) + "\n无效行\n",
                encoding="utf-8",
            )

            result = AuditStore(database, audit_root).list_events(
                date_from="2026-09-20", date_to="2026-09-20", username="审核员", page_size=2,
            )

            self.assertEqual(result["pagination"], {"page": 1, "pageSize": 2, "total": 3, "totalPages": 2})
            self.assertEqual([item["action"] for item in result["events"]], ["auth.login", "pdf.exported"])
            self.assertIn("page.saved", result["filterOptions"]["actions"])
            self.assertIn("job", result["filterOptions"]["sources"])
            self.assertNotIn("tutorial.exported", result["filterOptions"]["actions"])
            self.assertFalse(any(item["jobId"] == "tutorial-000207" for item in result["events"]))

    def test_search_matches_job_target_and_details(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            database = root / "jobs.db"
            store = JobStore(database)
            store.initialize()
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """INSERT INTO jobs(job_id, analysis_signature, status, job_folder,
                       original_target_name, algorithm_version, summary_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("b" * 32, "sig", "complete", str(root), "目标管线.pdf", "v1", "{}",
                     "2026-09-20T08:00:00", "2026-09-20T08:00:00"),
                )
                connection.execute(
                    "INSERT INTO job_events(job_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                    ("b" * 32, "failed", '{"error":"解析超时"}', "2026-09-20T09:00:00"),
                )
                connection.commit()
            finally:
                connection.close()

            audit = AuditStore(database, root / "audit")
            self.assertEqual(audit.list_events(search="目标管线")["pagination"]["total"], 1)
            self.assertEqual(audit.list_events(search="解析超时")["pagination"]["total"], 1)
            self.assertEqual(audit.list_events(search="不存在")["pagination"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
