"""Persistent SQLite job queue shared by the API and analysis workers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator


ACTIVE_STATUSES = ("queued", "leased", "processing", "cancelling")
TERMINAL_STATUSES = ("complete", "failed", "cancelled")


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
                    archived_at TEXT,
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
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    current_job_id TEXT
                );
                """
            )
            columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(jobs)")}
            if "creator_user_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN creator_user_id TEXT")
            if "creator_username" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN creator_username TEXT")

    @staticmethod
    def _job_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
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
                    creator_user_id, creator_username, created_at, updated_at
                ) VALUES (?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, analysis_signature, max(1, max_attempts), str(job_folder),
                    original_target_name, algorithm_version, creator_user_id,
                    creator_username, now, now,
                ),
            )
            self._event(connection, job_id, "queued")
            connection.commit()
        return self.get(job_id) or {}

    def import_existing(self, data_root: Path, algorithm_version: str) -> int:
        """Register legacy job folders once without disturbing known live leases."""

        if not data_root.is_dir():
            return 0
        imported = 0
        for result_path in data_root.glob("*/result.json"):
            meta_path = result_path.parent / "job.json"
            if not meta_path.is_file() or len(result_path.parent.name) != 32:
                continue
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            raw_status = str(result.get("status") or "failed")
            status = "queued" if raw_status == "processing" else "cancelled" if raw_status == "cancelling" else raw_status
            if status not in (*ACTIVE_STATUSES, *TERMINAL_STATUSES):
                status = "failed"
            created_at = str(meta.get("createdAt") or result.get("createdAt") or _now_text())
            updated_at = str(result.get("updatedAt") or created_at)
            with self._connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO jobs(
                        job_id, analysis_signature, status, job_folder,
                        original_target_name, algorithm_version, archived_at,
                        error, creator_user_id, creator_username, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_path.parent.name,
                        str(meta.get("analysisSignature") or f"legacy:{result_path.parent.name}"),
                        status,
                        str(result_path.parent),
                        str(meta.get("originalTargetName") or result.get("originalTargetName") or "未命名图纸"),
                        str(meta.get("analysisAlgorithmVersion") or algorithm_version),
                        meta.get("archivedAt"),
                        result.get("error"),
                        (meta.get("createdBy") or {}).get("userId"),
                        (meta.get("createdBy") or {}).get("username"),
                        created_at,
                        updated_at,
                    ),
                )
                imported += int(cursor.rowcount > 0)
        return imported

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
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            query = """
                UPDATE jobs SET status = ?, error = ?, lease_owner = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ?
                WHERE job_id = ?
            """
            parameters: tuple[Any, ...] = (status, error, _now_text(), job_id)
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
