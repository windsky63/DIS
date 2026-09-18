"""Persistent SQLite job queue shared by the API and analysis workers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator

try:
    from .page_reviews import PageRevisionConflict, merge_review_page
    from .page_lock_store import PageLockConflict, PageLockStore
except ImportError:  # Direct ``python backend/worker.py`` execution.
    from page_reviews import PageRevisionConflict, merge_review_page
    from page_lock_store import PageLockConflict, PageLockStore


ACTIVE_STATUSES = ("queued", "leased", "processing", "cancelling")
TERMINAL_STATUSES = ("complete", "failed", "cancelled")
QUEUE_SUMMARY_FIELDS = {
    "completedPages", "totalPages", "progressStage", "progressMessage",
    "completedReferenceFiles", "totalReferenceFiles", "layoutCompletedPages",
    "layoutTotalPages", "progressCompletedUnits", "progressTotalUnits",
    "progressPercent", "project", "referenceFileCount", "createdAt", "updatedAt",
}


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ActiveJobExists(RuntimeError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"相同分析任务已在队列中：{job_id}")
        self.job_id = job_id


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    analysis_signature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 2,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    heartbeat_at REAL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    job_folder TEXT NOT NULL,
                    original_target_name TEXT,
                    algorithm_version TEXT NOT NULL,
                    creator_user_id TEXT,
                    creator_username TEXT,
                    batch_id TEXT,
                    batch_index INTEGER,
                    completed_at TEXT,
                    archived_at TEXT,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_queue_order
                    ON jobs(status, priority DESC, created_at ASC);
                CREATE INDEX IF NOT EXISTS jobs_signature
                    ON jobs(analysis_signature, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS page_review_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    UNIQUE(job_id, page_number, revision)
                );
                CREATE INDEX IF NOT EXISTS page_review_events_job_page
                    ON page_review_events(job_id, page_number, saved_at);
                CREATE TABLE IF NOT EXISTS job_pages (
                    job_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    page_json TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    reviewed_by_user_id TEXT,
                    reviewed_by_username TEXT,
                    reviewed_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, page_number)
                );
                CREATE INDEX IF NOT EXISTS job_pages_job
                    ON job_pages(job_id, page_number);
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    current_job_id TEXT
                );
                """
            )
            columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(jobs)")}
            migrate_batch_metadata = any(
                column not in columns for column in ("batch_id", "batch_index", "completed_at")
            )
            if "creator_user_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN creator_user_id TEXT")
            if "creator_username" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN creator_username TEXT")
            if "summary_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN summary_json TEXT NOT NULL DEFAULT '{}'")
            if "batch_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT")
            if "batch_index" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN batch_index INTEGER")
            if "completed_at" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN completed_at TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS jobs_recent_batch "
                "ON jobs(status, archived_at, algorithm_version, completed_at DESC, batch_id, batch_index)"
            )
            if migrate_batch_metadata:
                rows = connection.execute(
                    """
                    SELECT job_id, job_folder, status
                    FROM jobs
                    """
                ).fetchall()
                for row in rows:
                    metadata: dict[str, Any] = {}
                    job_folder = Path(str(row["job_folder"]))
                    try:
                        loaded = json.loads((job_folder / "job.json").read_text(encoding="utf-8"))
                        if isinstance(loaded, dict):
                            metadata = loaded
                    except (OSError, json.JSONDecodeError):
                        pass
                    batch_id = str(metadata.get("batchId") or "").strip() or None
                    try:
                        batch_index = int(metadata["batchIndex"]) if metadata.get("batchIndex") is not None else None
                    except (TypeError, ValueError):
                        batch_index = None
                    completed_at = None
                    result_path = job_folder / "result.json"
                    if row["status"] == "complete" and result_path.is_file():
                        completed_at = datetime.fromtimestamp(result_path.stat().st_mtime).isoformat(timespec="seconds")
                    connection.execute(
                        """
                        UPDATE jobs
                        SET batch_id = ?, batch_index = ?, completed_at = ?
                        WHERE job_id = ?
                        """,
                        (batch_id, batch_index, completed_at, row["job_id"]),
                    )

    @staticmethod
    def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try:
            summary = json.loads(str(result.get("summary_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            summary = {}
        result["queueSummary"] = summary if isinstance(summary, dict) else {}
        result["createdBy"] = (
            {"userId": result["creator_user_id"], "username": result["creator_username"]}
            if result.get("creator_user_id") and result.get("creator_username")
            else None
        )
        return result

    def _event(self, connection: sqlite3.Connection, job_id: str, event_type: str, details: dict[str, Any] | None = None) -> None:
        connection.execute(
            "INSERT INTO job_events(job_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
            (job_id, event_type, json.dumps(details or {}, ensure_ascii=False), _now_text()),
        )

    def create_job(
        self,
        *,
        job_id: str,
        analysis_signature: str,
        job_folder: Path,
        original_target_name: str,
        algorithm_version: str,
        max_attempts: int = 2,
        creator_user_id: str | None = None,
        creator_username: str | None = None,
        batch_id: str | None = None,
        batch_index: int | None = None,
        queue_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now_text()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT job_id FROM jobs
                WHERE analysis_signature = ?
                  AND status IN ('queued','leased','processing','cancelling')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (analysis_signature,),
            ).fetchone()
            if active:
                connection.rollback()
                raise ActiveJobExists(str(active["job_id"]))
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, analysis_signature, status, priority, max_attempts,
                    job_folder, original_target_name, algorithm_version,
                    creator_user_id, creator_username, batch_id, batch_index,
                    summary_json, created_at, updated_at
                ) VALUES (?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, analysis_signature, max(1, max_attempts), str(job_folder),
                    original_target_name, algorithm_version, creator_user_id,
                    creator_username, str(batch_id).strip() if batch_id else None,
                    int(batch_index) if batch_index is not None else None, json.dumps(
                        self._queue_summary(queue_summary or {}), ensure_ascii=False, separators=(",", ":")
                    ), now, now,
                ),
            )
            self._event(connection, job_id, "queued")
            connection.commit()
        return self.get(job_id) or {}

    def latest_completed_batch(self, algorithm_version: str) -> list[dict[str, Any]]:
        """Return lightweight rows for the newest restorable completed batch."""

        eligible = """
            status = 'complete' AND archived_at IS NULL AND algorithm_version = ?
            AND EXISTS (SELECT 1 FROM job_pages WHERE job_pages.job_id = jobs.job_id)
        """
        with self._connection() as connection:
            latest = connection.execute(
                f"""
                SELECT * FROM jobs WHERE {eligible}
                ORDER BY COALESCE(completed_at, updated_at, created_at) DESC, job_id DESC
                LIMIT 1
                """,
                (algorithm_version,),
            ).fetchone()
            if latest is None:
                return []
            batch_id = str(latest["batch_id"] or "").strip()
            if not batch_id:
                rows = [latest]
            else:
                rows = connection.execute(
                    f"""
                    SELECT * FROM jobs WHERE {eligible} AND batch_id = ?
                    ORDER BY COALESCE(batch_index, 0),
                             COALESCE(completed_at, updated_at, created_at), job_id
                    """,
                    (algorithm_version, batch_id),
                ).fetchall()
        return [self._job_dict(row) for row in rows]

    @staticmethod
    def _queue_summary(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: payload[key] for key in QUEUE_SUMMARY_FIELDS if key in payload}

    def update_queue_summary(self, job_id: str, update: dict[str, Any]) -> dict[str, Any]:
        """Merge lightweight queue fields without ever storing page bodies."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT summary_json FROM jobs WHERE job_id = ?", (job_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError("任务不存在")
            try:
                current = json.loads(str(row["summary_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                current = {}
            if not isinstance(current, dict):
                current = {}
            current.update(self._queue_summary(update))
            connection.execute(
                "UPDATE jobs SET summary_json = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(current, ensure_ascii=False, separators=(",", ":")), _now_text(), job_id),
            )
            connection.commit()
        return current

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_dict(row) if row else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                ORDER BY
                    CASE WHEN status IN ('leased', 'processing', 'cancelling') THEN 0
                         WHEN status = 'queued' THEN 1 ELSE 2 END,
                    priority DESC,
                    created_at ASC,
                    updated_at DESC
                """
            ).fetchall()
        return [self._job_dict(row) for row in rows]

    def list_queue_page(self, scope: str, page: int, page_size: int) -> dict[str, Any]:
        """Return one ordered queue page plus global queue counters."""

        archived = scope == "archived"
        where = "archived_at IS NOT NULL" if archived else "archived_at IS NULL AND status != 'cancelled'"
        with self._connection() as connection:
            counts = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN status IN ('leased','processing','cancelling') THEN 1 ELSE 0 END) AS running_count,
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_count,
                    SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END) AS archived_count,
                    SUM(CASE WHEN archived_at IS NULL AND status != 'cancelled' THEN 1 ELSE 0 END) AS current_count
                FROM jobs
                """
            ).fetchone()
            total_items = int((counts["archived_count"] if archived else counts["current_count"]) or 0)
            total_pages = max(1, (total_items + page_size - 1) // page_size)
            resolved_page = min(max(1, page), total_pages)
            rows = connection.execute(
                f"""
                SELECT * FROM jobs
                WHERE {where}
                ORDER BY
                    CASE WHEN status IN ('leased','processing','cancelling') THEN 0
                         WHEN status = 'queued' THEN 1 ELSE 2 END,
                    CASE WHEN status IN ('leased','processing','cancelling','queued') THEN priority END DESC,
                    CASE WHEN status IN ('leased','processing','cancelling','queued') THEN created_at END ASC,
                    CASE WHEN status NOT IN ('leased','processing','cancelling','queued') THEN updated_at END DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, (resolved_page - 1) * page_size),
            ).fetchall()
            queued_rows = connection.execute(
                "SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY priority DESC, created_at ASC"
            ).fetchall()
        return {
            "records": [self._job_dict(row) for row in rows],
            "queuedJobIds": [str(row["job_id"]) for row in queued_rows],
            "runningCount": int(counts["running_count"] or 0),
            "queuedCount": int(counts["queued_count"] or 0),
            "archivedCount": int(counts["archived_count"] or 0),
            "currentCount": int(counts["current_count"] or 0),
            "page": resolved_page,
            "pageSize": page_size,
            "totalItems": total_items,
            "totalPages": (total_items + page_size - 1) // page_size,
        }

    def record_page_review(
        self,
        job_id: str,
        page_number: int,
        revision: int,
        user: dict[str, object],
        saved_at: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO page_review_events(
                    job_id, page_number, revision, user_id, username, saved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, int(page_number), int(revision),
                    str(user["userId"]), str(user["username"]), str(saved_at),
                ),
            )

    @staticmethod
    def _page_payload(row: sqlite3.Row) -> dict[str, Any]:
        page = json.loads(str(row["page_json"]))
        page["reviewRevision"] = int(row["revision"])
        if row["reviewed_by_user_id"] and row["reviewed_by_username"]:
            page["reviewedBy"] = {
                "userId": str(row["reviewed_by_user_id"]),
                "username": str(row["reviewed_by_username"]),
            }
        if row["reviewed_at"]:
            page["reviewedAt"] = str(row["reviewed_at"])
        return page

    def replace_job_pages(self, job_id: str, pages: list[dict[str, Any]]) -> None:
        """Publish the complete page set produced by a Worker."""

        now = _now_text()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM job_pages WHERE job_id = ?", (job_id,))
            for page in pages:
                page_number = int(page.get("page") or 0)
                if page_number < 1:
                    continue
                revision = int(page.get("reviewRevision") or 0)
                reviewed_by = page.get("reviewedBy") if isinstance(page.get("reviewedBy"), dict) else {}
                connection.execute(
                    """
                    INSERT INTO job_pages(
                        job_id, page_number, page_json, revision,
                        reviewed_by_user_id, reviewed_by_username, reviewed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id, page_number,
                        json.dumps(page, ensure_ascii=False, separators=(",", ":")),
                        revision, reviewed_by.get("userId"), reviewed_by.get("username"),
                        page.get("reviewedAt"), now,
                    ),
                )
            connection.commit()

    def clear_job_pages(self, job_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM job_pages WHERE job_id = ?", (job_id,))

    def publish_analysis_page(self, job_id: str, page: dict[str, Any]) -> int:
        """Upsert one Worker-produced page and return the published page count."""

        page_number = int(page.get("page") or 0)
        if page_number < 1:
            raise ValueError("页面编号无效")
        page = dict(page)
        page.setdefault("reviewRevision", 0)
        now = _now_text()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO job_pages(
                    job_id, page_number, page_json, revision,
                    reviewed_by_user_id, reviewed_by_username, reviewed_at, updated_at
                ) VALUES (?, ?, ?, 0, NULL, NULL, NULL, ?)
                ON CONFLICT(job_id, page_number) DO UPDATE SET
                    page_json=excluded.page_json,
                    revision=0,
                    reviewed_by_user_id=NULL,
                    reviewed_by_username=NULL,
                    reviewed_at=NULL,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id, page_number,
                    json.dumps(page, ensure_ascii=False, separators=(",", ":")), now,
                ),
            )
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM job_pages WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            connection.commit()
        return int(row["count"])

    def get_job_pages(self, job_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM job_pages WHERE job_id = ? ORDER BY page_number",
                (job_id,),
            ).fetchall()
        return [self._page_payload(row) for row in rows]

    @staticmethod
    def _workspace_page_summary(row: sqlite3.Row, page: dict[str, Any]) -> dict[str, Any]:
        candidates = page.get("candidates") if isinstance(page.get("candidates"), list) else []
        included = [
            candidate for candidate in candidates
            if candidate.get("included", True)
            and candidate.get("componentKind") != "design-component"
            and candidate.get("componentKind") != "special-marker"
            and candidate.get("componentType") != "special"
            and candidate.get("specialMarker") is not True
        ]
        unmatched = [
            candidate for candidate in included
            if not candidate.get("referenceMatched") or not str(candidate.get("referenceLabel") or "").strip()
        ]
        unresolved = max(0, int((page.get("reference") or {}).get("unresolvedCalloutGap") or 0))
        keys = (
            "page", "width", "height", "candidateCount", "weldCandidateCount",
            "designComponentCount", "referenceMatchedCount", "componentReferenceMatchedCount",
            "validatedCount", "drawingCount", "reference", "pcfResearchFile",
        )
        summary = {key: page[key] for key in keys if key in page}
        summary["page"] = int(row["page_number"])
        summary["candidateCount"] = int(page.get("candidateCount") or len(candidates))
        summary["candidates"] = []
        summary["detailsLoaded"] = False
        summary["matchSummary"] = {
            "matched": len(included) - len(unmatched),
            "unmatched": len(unmatched),
            "unresolved": unresolved,
            "complete": bool(included) and not unmatched and unresolved == 0,
        }
        summary["reviewRevision"] = int(row["revision"])
        if row["reviewed_by_user_id"] and row["reviewed_by_username"]:
            summary["reviewedBy"] = {
                "userId": str(row["reviewed_by_user_id"]),
                "username": str(row["reviewed_by_username"]),
            }
        if row["reviewed_at"]:
            summary["reviewedAt"] = str(row["reviewed_at"])
        return summary

    def get_job_workspace_pages(self, job_id: str, initial_page: int | None = None) -> list[dict[str, Any]]:
        """Load one editable page and lightweight summaries for every other page."""

        with self._connection() as connection:
            if initial_page is None:
                first = connection.execute(
                    "SELECT MIN(page_number) AS page_number FROM job_pages WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                initial_page = int(first["page_number"]) if first and first["page_number"] is not None else None
            cursor = connection.execute(
                "SELECT * FROM job_pages WHERE job_id = ? ORDER BY page_number",
                (job_id,),
            )
            pages = []
            for row in cursor:
                page = json.loads(str(row["page_json"]))
                if int(row["page_number"]) == int(initial_page or 0):
                    pages.append(self._page_payload(row) | {"detailsLoaded": True})
                else:
                    pages.append(self._workspace_page_summary(row, page))
        return pages

    def get_job_page_summaries(self, job_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT page_number, revision, reviewed_by_user_id,
                       reviewed_by_username, reviewed_at
                FROM job_pages WHERE job_id = ? ORDER BY page_number
                """,
                (job_id,),
            ).fetchall()
        return [
            {
                "page": int(row["page_number"]),
                "reviewRevision": int(row["revision"]),
                **({
                    "reviewedBy": {
                        "userId": str(row["reviewed_by_user_id"]),
                        "username": str(row["reviewed_by_username"]),
                    },
                    "reviewedAt": str(row["reviewed_at"]),
                } if row["reviewed_by_user_id"] and row["reviewed_by_username"] else {}),
            }
            for row in rows
        ]

    def get_job_page_summaries_many(self, job_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        result = {job_id: [] for job_id in job_ids}
        if not job_ids:
            return result
        placeholders = ",".join("?" for _ in job_ids)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT job_id, page_number, revision, reviewed_by_user_id,
                       reviewed_by_username, reviewed_at
                FROM job_pages WHERE job_id IN ({placeholders})
                ORDER BY job_id, page_number
                """,
                job_ids,
            ).fetchall()
        for row in rows:
            result[str(row["job_id"])].append({
                "page": int(row["page_number"]),
                "reviewRevision": int(row["revision"]),
                **({
                    "reviewedBy": {
                        "userId": str(row["reviewed_by_user_id"]),
                        "username": str(row["reviewed_by_username"]),
                    },
                    "reviewedAt": str(row["reviewed_at"]),
                } if row["reviewed_by_user_id"] and row["reviewed_by_username"] else {}),
            })
        return result

    def get_job_page(self, job_id: str, page_number: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM job_pages WHERE job_id = ? AND page_number = ?",
                (job_id, int(page_number)),
            ).fetchone()
        return self._page_payload(row) if row else None

    def save_job_page(
        self,
        job_id: str,
        page_number: int,
        incoming_page: dict[str, Any],
        base_revision: int,
        user: dict[str, object],
        *,
        client_instance_id: str | None = None,
        lock_token: str | None = None,
    ) -> dict[str, Any]:
        """Atomically save one page and its review event."""

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if client_instance_id is not None or lock_token is not None:
                now = time.time()
                lock_row = connection.execute(
                    """
                    SELECT * FROM page_locks
                    WHERE job_id = ? AND page_number = ? AND expires_at > ?
                    """,
                    (job_id, int(page_number), now),
                ).fetchone()
                valid_lock = bool(
                    lock_row
                    and str(lock_row["owner_user_id"]) == str(user["userId"])
                    and str(lock_row["client_instance_id"]) == str(client_instance_id or "")
                    and str(lock_row["lock_token_hash"]) == PageLockStore._token_hash(str(lock_token or ""))
                )
                if not valid_lock:
                    connection.rollback()
                    raise PageLockConflict(PageLockStore._public(lock_row) if lock_row else None)
            row = connection.execute(
                "SELECT * FROM job_pages WHERE job_id = ? AND page_number = ?",
                (job_id, int(page_number)),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError("页面结果不存在")
            current_revision = int(row["revision"])
            if int(base_revision) != current_revision:
                connection.rollback()
                raise PageRevisionConflict(current_revision)
            current_page = self._page_payload(row)
            container = {"pages": [current_page]}
            updated_page = merge_review_page(
                container, int(page_number), incoming_page, current_revision, user,
            )
            reviewed_by = updated_page.get("reviewedBy") or {}
            updated = connection.execute(
                """
                UPDATE job_pages
                SET page_json = ?, revision = ?, reviewed_by_user_id = ?,
                    reviewed_by_username = ?, reviewed_at = ?, updated_at = ?
                WHERE job_id = ? AND page_number = ? AND revision = ?
                """,
                (
                    json.dumps(updated_page, ensure_ascii=False, separators=(",", ":")),
                    int(updated_page["reviewRevision"]), reviewed_by.get("userId"),
                    reviewed_by.get("username"), updated_page.get("reviewedAt"),
                    str(updated_page.get("reviewedAt") or _now_text()),
                    job_id, int(page_number), current_revision,
                ),
            )
            if updated.rowcount != 1:
                latest = connection.execute(
                    "SELECT revision FROM job_pages WHERE job_id = ? AND page_number = ?",
                    (job_id, int(page_number)),
                ).fetchone()
                connection.rollback()
                raise PageRevisionConflict(int(latest["revision"]) if latest else current_revision)
            connection.execute(
                """
                INSERT INTO page_review_events(
                    job_id, page_number, revision, user_id, username, saved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, int(page_number), int(updated_page["reviewRevision"]),
                    str(user["userId"]), str(user["username"]), str(updated_page["reviewedAt"]),
                ),
            )
            connection.execute(
                "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
                (str(updated_page["reviewedAt"]), job_id),
            )
            connection.commit()
        return updated_page

    def page_review_history(self, job_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT page_number, revision, user_id, username, saved_at
                FROM page_review_events
                WHERE job_id = ?
                ORDER BY page_number, revision, event_id
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def page_review_histories(self, job_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        result = {job_id: [] for job_id in job_ids}
        if not job_ids:
            return result
        placeholders = ",".join("?" for _ in job_ids)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT job_id, page_number, revision, user_id, username, saved_at
                FROM page_review_events
                WHERE job_id IN ({placeholders})
                ORDER BY job_id, page_number, revision, event_id
                """,
                job_ids,
            ).fetchall()
        for row in rows:
            result[str(row["job_id"])].append({
                key: row[key] for key in ("page_number", "revision", "user_id", "username", "saved_at")
            })
        return result

    def count_active(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','leased','processing','cancelling')"
            ).fetchone()
        return int(row["count"])

    def find_active_by_signature(self, signature: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE analysis_signature = ? AND status IN ('queued','leased','processing','cancelling')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (signature,),
            ).fetchone()
        return self._job_dict(row) if row else None

    def move_queued(self, job_id: str, direction: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not current:
                connection.rollback()
                raise KeyError("任务不存在")
            if current["status"] in {"leased", "processing", "cancelling"}:
                connection.rollback()
                if direction == "front":
                    return {"moved": False, "queueState": "running", "queuePosition": None, "previousQueuePosition": None}
                raise RuntimeError("正在解析的任务不能调整顺序")
            if current["status"] != "queued":
                connection.rollback()
                raise ValueError("只有等待中的任务可以调整顺序")
            rows = connection.execute(
                "SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY priority DESC, created_at ASC"
            ).fetchall()
            order = [str(row["job_id"]) for row in rows]
            previous = order.index(job_id)
            if direction == "front":
                target = 0
            elif direction == "up":
                target = max(0, previous - 1)
            elif direction == "down":
                target = min(len(order) - 1, previous + 1)
            else:
                connection.rollback()
                raise ValueError("队列调整方向无效")
            order.insert(target, order.pop(previous))
            now = _now_text()
            for index, queued_id in enumerate(order):
                connection.execute(
                    "UPDATE jobs SET priority = ?, updated_at = ? WHERE job_id = ?",
                    (len(order) - index, now, queued_id),
                )
            self._event(connection, job_id, "queue-moved", {"from": previous + 1, "to": target + 1})
            connection.commit()
        return {
            "moved": target != previous,
            "queueState": "queued",
            "queuePosition": target + 1,
            "previousQueuePosition": previous + 1,
        }

    def claim_next(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued' AND cancel_requested = 0
                ORDER BY priority DESC, created_at ASC LIMIT 1
                """
            ).fetchone()
            if not row:
                connection.rollback()
                return None
            now_epoch = time.time()
            now = _now_text()
            updated = connection.execute(
                """
                UPDATE jobs
                SET status = 'processing', lease_owner = ?, lease_expires_at = ?,
                    heartbeat_at = ?, attempt = attempt + 1, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (worker_id, now_epoch + lease_seconds, now_epoch, now, row["job_id"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            self._event(connection, row["job_id"], "claimed", {"workerId": worker_id})
            connection.commit()
        return self.get(str(row["job_id"]))

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        now_epoch = time.time()
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND lease_owner = ? AND status IN ('processing','cancelling')
                """,
                (now_epoch, now_epoch + lease_seconds, _now_text(), job_id, worker_id),
            )
        return updated.rowcount == 1

    def cancellation_requested(self, job_id: str) -> bool:
        row = self.get(job_id)
        return bool(row and row.get("cancel_requested"))

    def touch_worker(self, worker_id: str, current_job_id: str | None = None) -> None:
        now_text, now_epoch = _now_text(), time.time()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO workers(worker_id, started_at, heartbeat_at, current_job_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    heartbeat_at = excluded.heartbeat_at,
                    current_job_id = excluded.current_job_id
                """,
                (worker_id, now_text, now_epoch, current_job_id),
            )

    def remove_worker(self, worker_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM workers WHERE worker_id = ?", (worker_id,))

    def live_workers(self, stale_after_seconds: int = 30) -> list[dict[str, Any]]:
        cutoff = time.time() - max(1, stale_after_seconds)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workers WHERE heartbeat_at >= ? ORDER BY started_at",
                (cutoff,),
            ).fetchall()
        return [dict(row) for row in rows]

    def request_cancel(self, job_id: str) -> str:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                connection.rollback()
                raise KeyError("任务不存在")
            status = str(row["status"])
            if status in TERMINAL_STATUSES:
                connection.rollback()
                return status
            next_status = "cancelled" if status == "queued" else "cancelling"
            connection.execute(
                "UPDATE jobs SET status = ?, cancel_requested = 1, updated_at = ? WHERE job_id = ?",
                (next_status, _now_text(), job_id),
            )
            self._event(connection, job_id, "cancel-requested")
            connection.commit()
        return next_status

    def finish(
        self,
        job_id: str,
        status: str,
        error: str | None = None,
        *,
        worker_id: str | None = None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"无效终态：{status}")
        finished_at = _now_text()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            query = """
                UPDATE jobs SET status = ?, error = ?, lease_owner = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ?,
                    completed_at = CASE
                        WHEN ? = 'complete' THEN COALESCE(completed_at, ?)
                        ELSE completed_at
                    END
                WHERE job_id = ?
            """
            parameters: tuple[Any, ...] = (
                status, error, finished_at, status, finished_at, job_id,
            )
            if worker_id is not None:
                query += " AND lease_owner = ? AND status IN ('processing','cancelling')"
                parameters += (worker_id,)
                if status == "complete":
                    query += " AND cancel_requested = 0"
            updated = connection.execute(query, parameters)
            if updated.rowcount != 1:
                connection.rollback()
                return False
            self._event(connection, job_id, status, {"error": error} if error else {})
            connection.commit()
        return True

    def set_archived(self, job_id: str, archived_at: str | None) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE jobs SET archived_at = ?, updated_at = ? WHERE job_id = ?",
                (archived_at, _now_text(), job_id),
            )

    def delete(self, job_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM job_pages WHERE job_id = ?", (job_id,))
            connection.execute("DELETE FROM page_review_events WHERE job_id = ?", (job_id,))
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))

    def requeue_expired(self) -> dict[str, list[str]]:
        now_epoch = time.time()
        result: dict[str, list[str]] = {"requeued": [], "failed": [], "cancelled": []}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status IN ('processing','cancelling')
                  AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                """,
                (now_epoch,),
            ).fetchall()
            for row in rows:
                if row["cancel_requested"]:
                    status = "cancelled"
                elif int(row["attempt"]) >= int(row["max_attempts"]):
                    status = "failed"
                else:
                    status = "queued"
                connection.execute(
                    """
                    UPDATE jobs SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                        heartbeat_at = NULL, error = ?, updated_at = ? WHERE job_id = ?
                    """,
                    (
                        status,
                        "Worker租约过期，已超过最大重试次数" if status == "failed" else None,
                        _now_text(),
                        row["job_id"],
                    ),
                )
                self._event(connection, row["job_id"], f"lease-expired-{status}")
                result["requeued" if status == "queued" else status].append(str(row["job_id"]))
            connection.commit()
        return result
