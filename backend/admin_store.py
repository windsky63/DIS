"""Read-only administration queries over the shared SQLite database."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterator


VISIBLE_TABLES = (
    ("users", "用户"),
    ("sessions", "登录会话"),
    ("jobs", "解析任务"),
    ("job_events", "任务事件"),
    ("job_pages", "任务页面"),
    ("page_review_events", "页面审核事件"),
    ("workers", "解析 Worker"),
    ("page_locks", "页面锁"),
    ("assistant_conversations", "AI 会话"),
)


class AdminStore:
    def __init__(self, path: Path, *, now: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._now = now

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _existing_tables(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    def overview(self) -> dict[str, object]:
        with self._connection() as connection:
            existing = self._existing_tables(connection)
            table_rows = []
            counts: dict[str, int] = {}
            for table_name, label in VISIBLE_TABLES:
                if table_name not in existing:
                    continue
                row_count = int(connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
                counts[table_name] = row_count
                table_rows.append({"name": table_name, "label": label, "rowCount": row_count})

            active_sessions = 0
            enabled_users = 0
            if "sessions" in existing:
                active_sessions = int(connection.execute(
                    """SELECT COUNT(*) FROM sessions s
                       JOIN users u ON u.user_id = s.user_id
                       WHERE s.expires_at > ? AND u.disabled = 0""",
                    (self._now(),),
                ).fetchone()[0])
            if "users" in existing:
                enabled_users = int(connection.execute(
                    "SELECT COUNT(*) FROM users WHERE disabled = 0",
                ).fetchone()[0])

            job_statuses: dict[str, int] = {}
            if "jobs" in existing:
                job_statuses = {
                    str(row["status"]): int(row["count"])
                    for row in connection.execute(
                        "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
                    )
                }

            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).upper()
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])

        database_size = sum(
            candidate.stat().st_size for candidate in (
                self.path,
                Path(f"{self.path}-wal"),
                Path(f"{self.path}-shm"),
            ) if candidate.is_file()
        )
        return {
            "generatedAt": datetime.fromtimestamp(self._now()).isoformat(timespec="seconds"),
            "metrics": {
                "totalUsers": counts.get("users", 0),
                "enabledUsers": enabled_users,
                "activeSessions": active_sessions,
                "totalJobs": counts.get("jobs", 0),
                "queuedJobs": job_statuses.get("queued", 0),
                "runningJobs": sum(job_statuses.get(status, 0) for status in ("leased", "processing", "cancelling")),
                "completedJobs": job_statuses.get("complete", 0),
                "failedJobs": job_statuses.get("failed", 0),
                "reviewedPages": counts.get("page_review_events", 0),
            },
            "jobStatuses": job_statuses,
            "database": {
                "engine": "SQLite",
                "journalMode": journal_mode,
                "sizeBytes": database_size or page_size * page_count,
                "tableCount": len(table_rows),
                "tables": table_rows,
            },
        }

    def users(self, *, search: str = "", page: int = 1, page_size: int = 20) -> dict[str, object]:
        clean_search = str(search or "").strip().casefold()[:80]
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        where = "WHERE u.username_key LIKE ?" if clean_search else ""
        parameters: tuple[object, ...] = (f"%{clean_search}%",) if clean_search else ()
        with self._connection() as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM users u {where}", parameters,
            ).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT u.user_id, u.username, u.is_admin, u.disabled, u.created_at, u.last_login_at,
                       SUM(CASE WHEN s.expires_at > ? THEN 1 ELSE 0 END) AS active_sessions,
                       MAX(CASE WHEN s.expires_at > ? THEN s.last_seen_at ELSE NULL END) AS last_seen_at
                FROM users u
                LEFT JOIN sessions s ON s.user_id = u.user_id
                {where}
                GROUP BY u.user_id
                ORDER BY u.created_at DESC, u.username_key ASC
                LIMIT ? OFFSET ?
                """,
                (self._now(), self._now(), *parameters, page_size, (page - 1) * page_size),
            ).fetchall()
        return {
            "users": [{
                "userId": str(row["user_id"]),
                "username": str(row["username"]),
                "isAdmin": bool(row["is_admin"]),
                "disabled": bool(row["disabled"]),
                "createdAt": str(row["created_at"]),
                "lastLoginAt": str(row["last_login_at"] or ""),
                "activeSessions": int(row["active_sessions"] or 0),
                "lastSeenAt": (
                    datetime.fromtimestamp(float(row["last_seen_at"])).isoformat(timespec="seconds")
                    if row["last_seen_at"] is not None else ""
                ),
            } for row in rows],
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "total": total,
                "totalPages": max(1, (total + page_size - 1) // page_size),
            },
            "search": clean_search,
        }
