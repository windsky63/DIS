from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from business_stats import BusinessStats
import server


class BusinessStatsTests(unittest.TestCase):
    def test_page_business_deltas_are_derived_from_saved_candidate_changes(self) -> None:
        before = {"candidates": [
            {"id": "auto", "origin": "recognized", "included": True, "number": "1", "x": 1, "y": 2},
            {"id": "removed", "origin": "recognized", "included": True, "number": "2"},
        ]}
        after = {"candidates": [
            {"id": "auto", "origin": "recognized", "included": True, "number": "10", "x": 2, "y": 2},
            {"id": "removed", "origin": "recognized", "included": False, "number": "2"},
            {"id": "manual", "origin": "manual", "included": True, "number": "11"},
        ]}

        self.assertEqual(server._page_business_deltas(before, after), {
            "marker.manual_added": 1,
            "marker.content_corrected": 2,
            "marker.layout_adjusted": 1,
            "marker.deleted": 1,
        })

    def test_audit_endpoint_attaches_server_authenticated_identity(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            audit_root = Path(folder_name) / "audit"
            handler = object.__new__(server.ApiHandler)
            handler.path = "/api/audit"
            handler.headers = {}
            handler._request_user = {"userId": "server-user-id", "username": "reviewer", "isAdmin": False}
            handler._read_json = lambda *_args: {
                "action": "weld.manual_added",
                "details": {"username": "spoofed", "jobId": "job-1"},
            }
            responses = []
            handler._json = lambda status, payload, headers=None: responses.append((status, payload))

            with patch.object(server, "AUDIT_ROOT", audit_root):
                handler.do_POST()

            event = json.loads((audit_root / "events.jsonl").read_text(encoding="utf-8").strip())
            self.assertEqual(responses, [(200, {"logged": True})])
            self.assertEqual(event["source"], "client")
            self.assertEqual(event["actor"], {"userId": "server-user-id", "username": "reviewer"})
            self.assertEqual(event["details"]["username"], "spoofed")

    def test_audit_endpoint_skips_every_tutorial_event(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            audit_root = Path(folder_name) / "audit"
            handler = object.__new__(server.ApiHandler)
            handler.path = "/api/audit"
            handler.headers = {}
            handler._request_user = {"userId": "u1", "username": "reviewer", "isAdmin": False}
            responses = []
            handler._json = lambda status, payload, headers=None: responses.append((status, payload))

            with patch.object(server, "AUDIT_ROOT", audit_root):
                handler._read_json = lambda *_args: {
                    "action": "marker.deleted", "details": {"jobId": "tutorial-000207", "page": 1},
                }
                handler.do_POST()
                handler._read_json = lambda *_args: {"action": "tutorial.saved-locally", "details": {}}
                handler.do_POST()

            self.assertEqual(responses, [
                (200, {"logged": False, "skipped": "tutorial"}),
                (200, {"logged": False, "skipped": "tutorial"}),
            ])
            self.assertFalse((audit_root / "events.jsonl").exists())

    def test_snapshot_combines_task_pages_and_attested_audit_actor(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            database = root / "jobs.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE jobs (
                    job_id TEXT, original_target_name TEXT, status TEXT,
                    creator_username TEXT, summary_json TEXT, error TEXT,
                    created_at TEXT, updated_at TEXT, completed_at TEXT
                );
                CREATE TABLE job_pages (
                    job_id TEXT, page_number INTEGER, page_json TEXT,
                    reviewed_by_username TEXT, reviewed_at TEXT, updated_at TEXT
                    , created_at TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "job-1", "drawing.pdf", "complete", "reviewer",
                    json.dumps({"project": {"name": "P1"}}), "",
                    "2026-09-19T08:00:00", "2026-09-20T09:00:00", "2026-09-20T09:00:00",
                ),
            )
            connection.execute(
                "INSERT INTO job_pages VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "job-1", 1,
                    json.dumps({"candidates": [
                        {"id": "auto", "origin": "recognized", "included": True},
                        {"id": "manual", "origin": "manual", "included": True},
                        {"id": "removed", "origin": "recognized", "included": False},
                    ]}),
                    "reviewer", "2026-09-21T09:00:00", "2026-09-21T09:00:00", "2026-09-20T09:00:00",
                ),
            )
            connection.commit()
            connection.close()

            audit_root = root / "audit"
            audit_root.mkdir()
            (audit_root / "events.jsonl").write_text(
                json.dumps({
                    "action": "marker.manual_added", "source": "server", "timestamp": "2026-09-20T08:30:00",
                    "actor": {"userId": "u1", "username": "operator"},
                    "details": {"jobId": "job-1", "page": 1, "count": 1, "projectName": "spoofed"},
                }) + "\n" + json.dumps({
                    "action": "marker.deleted", "source": "server", "timestamp": "2026-09-20T08:40:00",
                    "actor": {"userId": "u1", "username": "reviewer"},
                    "details": {"jobId": "job-1", "page": 1, "count": 2},
                }) + "\n" + json.dumps({
                    "action": "draft.saved", "timestamp": "2026-09-20T08:50:00",
                    "actor": {"userId": "u1", "username": "reviewer"},
                    "details": {"jobId": "job-1", "page": 1},
                }) + "\n" + json.dumps({
                    "action": "marker.manual_added", "source": "client", "timestamp": "2026-09-20T08:55:00",
                    "actor": {"userId": "u1", "username": "reviewer"},
                    "details": {"jobId": "job-1", "page": 1, "count": 999},
                }) + "\n",
                encoding="utf-8",
            )

            result = BusinessStats(database, audit_root).snapshot(
                date_from="2026-09-20", date_to="2026-09-20", project="P1",
            )

            self.assertEqual(result["summary"]["processedPages"], 1)
            self.assertEqual(result["summary"]["totalJobs"], 1)
            self.assertEqual(result["summary"]["reviewedPages"], 1)
            self.assertEqual(result["summary"]["automaticMarkers"], 1)
            self.assertEqual(result["summary"]["manualMarkers"], 1)
            self.assertEqual(result["summary"]["manualAdditions"], 1)
            self.assertEqual(result["summary"]["deletedMarkers"], 2)
            self.assertEqual(result["summary"]["activeUsers"], 2)
            self.assertEqual(result["projects"][0]["reviewRate"], 1.0)
            self.assertEqual(result["projects"][0]["project"], "P1")
            self.assertEqual(result["drawings"][0]["targetName"], "drawing.pdf")
            self.assertEqual(result["drawings"][0]["reviewRate"], 1.0)
            self.assertEqual(result["drawings"][0]["manualAdditions"], 1)
            self.assertNotIn("draft.saved", result["filterOptions"]["actions"])
            self.assertEqual(result["trends"][0]["processedPages"], 1)
            self.assertEqual(result["trends"][0]["reviewedPages"], 0)

    def test_snapshot_reads_durable_sqlite_business_events(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            database = root / "jobs.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE jobs (
                    job_id TEXT, original_target_name TEXT, status TEXT,
                    creator_username TEXT, summary_json TEXT, error TEXT,
                    created_at TEXT, updated_at TEXT, completed_at TEXT
                );
                CREATE TABLE job_pages (
                    job_id TEXT, page_number INTEGER, page_json TEXT,
                    reviewed_by_username TEXT, reviewed_at TEXT, created_at TEXT, updated_at TEXT
                );
                CREATE TABLE business_events (
                    event_id INTEGER PRIMARY KEY, action TEXT, user_id TEXT, username TEXT,
                    job_id TEXT, page_number INTEGER, amount INTEGER, created_at TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("job-1", "drawing.pdf", "complete", "creator", json.dumps({"project": {"name": "P1"}}),
                 "", "2026-09-20T08:00:00", "2026-09-20T09:00:00", "2026-09-20T09:00:00"),
            )
            connection.execute(
                "INSERT INTO business_events(action, user_id, username, job_id, page_number, amount, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("marker.content_corrected", "u1", "reviewer", "job-1", 1, 3, "2026-09-20T10:00:00"),
            )
            connection.execute(
                "INSERT INTO business_events(action, user_id, username, job_id, page_number, amount, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("tutorial.exported", "u1", "reviewer", "tutorial-000207", None, 5, "2026-09-20T10:30:00"),
            )
            connection.commit()
            connection.close()

            result = BusinessStats(database, root / "missing-audit").snapshot(
                date_from="2026-09-20", date_to="2026-09-20",
            )

        self.assertEqual(result["summary"]["contentCorrections"], 3)
        self.assertEqual(result["summary"]["exports"], 0)
        self.assertIn("marker.content_corrected", result["filterOptions"]["actions"])
        self.assertNotIn("tutorial.exported", result["filterOptions"]["actions"])
        self.assertIn("reviewer", {item["username"] for item in result["users"]})

    def test_cancelled_jobs_are_excluded_from_every_dashboard_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            database = root / "jobs.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE jobs (
                    job_id TEXT, original_target_name TEXT, status TEXT,
                    creator_username TEXT, summary_json TEXT, error TEXT,
                    created_at TEXT, updated_at TEXT, completed_at TEXT
                );
                CREATE TABLE job_pages (
                    job_id TEXT, page_number INTEGER, page_json TEXT,
                    reviewed_by_username TEXT, reviewed_at TEXT, created_at TEXT, updated_at TEXT
                );
                CREATE TABLE business_events (
                    event_id INTEGER PRIMARY KEY, action TEXT, user_id TEXT, username TEXT,
                    job_id TEXT, page_number INTEGER, amount INTEGER, created_at TEXT
                );
                """
            )
            jobs = [
                ("kept", "kept.pdf", "complete", "kept-user", json.dumps({"project": {"name": "保留项目"}}),
                 "", "2026-09-20T08:00:00", "2026-09-20T09:00:00", "2026-09-20T09:00:00"),
                ("cancelled", "cancelled.pdf", "cancelled", "cancel-user", json.dumps({"project": {"name": "取消项目"}}),
                 "", "2026-09-20T08:00:00", "2026-09-20T09:00:00", None),
            ]
            connection.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", jobs)
            pages = [
                ("kept", 1, json.dumps({"candidates": [{"id": "a", "origin": "recognized"}]}),
                 "kept-user", "2026-09-20T10:00:00", "2026-09-20T09:00:00", "2026-09-20T10:00:00"),
                ("cancelled", 1, json.dumps({"candidates": [{"id": "b", "origin": "manual"}]}),
                 "cancel-user", "2026-09-20T10:00:00", "2026-09-20T09:00:00", "2026-09-20T10:00:00"),
            ]
            connection.executemany("INSERT INTO job_pages VALUES (?, ?, ?, ?, ?, ?, ?)", pages)
            connection.executemany(
                "INSERT INTO business_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (1, "marker.content_corrected", "u1", "kept-user", "kept", 1, 1, "2026-09-20T11:00:00"),
                    (2, "marker.manual_added", "u2", "cancel-user", "cancelled", 1, 9, "2026-09-20T11:00:00"),
                ],
            )
            connection.commit()
            connection.close()

            result = BusinessStats(database, root / "audit").snapshot(
                date_from="2026-09-20", date_to="2026-09-20",
            )

        self.assertEqual(result["summary"]["totalJobs"], 1)
        self.assertEqual(result["summary"]["processedDocuments"], 1)
        self.assertEqual(result["summary"]["processedPages"], 1)
        self.assertEqual(result["summary"]["reviewedPages"], 1)
        self.assertEqual(result["summary"]["manualMarkers"], 0)
        self.assertEqual(result["summary"]["manualAdditions"], 0)
        self.assertEqual(result["summary"]["contentCorrections"], 1)
        self.assertEqual([item["project"] for item in result["projects"]], ["保留项目"])
        self.assertEqual([item["username"] for item in result["users"]], ["kept-user"])
        self.assertNotIn("取消项目", result["filterOptions"]["projects"])
        self.assertNotIn("cancel-user", result["filterOptions"]["users"])


if __name__ == "__main__":
    unittest.main()
