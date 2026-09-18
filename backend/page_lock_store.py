"""Exclusive per-page review leases shared by all API threads."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Callable, Iterator, Sequence


class PageLockConflict(RuntimeError):
    def __init__(self, lock: dict[str, object] | None = None) -> None:
        super().__init__("该页正在被其他窗口核对" if lock else "页面锁已失效，请重新打开该页")
        self.lock = lock or {}


class PageLockStore:
    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], float] = time.time,
        lease_seconds: int = 180,
    ) -> None:
        self.path = Path(path)
        self._now = now
        self.lease_seconds = max(10, int(lease_seconds))

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS page_locks (
                    job_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    owner_username TEXT NOT NULL,
                    client_instance_id TEXT NOT NULL,
                    lock_token_hash TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY(job_id, page_number)
                );
                CREATE INDEX IF NOT EXISTS page_locks_expiry ON page_locks(expires_at);
                """
            )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _public(row: sqlite3.Row, token: str | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "jobId": str(row["job_id"]),
            "page": int(row["page_number"]),
            "owner": {"userId": str(row["owner_user_id"]), "username": str(row["owner_username"])},
            "clientInstanceId": str(row["client_instance_id"]),
            "acquiredAt": datetime.fromtimestamp(float(row["acquired_at"])).isoformat(timespec="seconds"),
            "expiresAt": datetime.fromtimestamp(float(row["expires_at"])).isoformat(timespec="seconds"),
        }
        if token is not None:
            result["lockToken"] = token
        return result

    def _active_row(self, connection: sqlite3.Connection, job_id: str, page: int) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM page_locks WHERE job_id = ? AND page_number = ? AND expires_at > ?",
            (job_id, page, self._now()),
        ).fetchone()

    def get(self, job_id: str, page: int) -> dict[str, object] | None:
        with self._connection() as connection:
            connection.execute("DELETE FROM page_locks WHERE expires_at <= ?", (self._now(),))
            row = self._active_row(connection, job_id, int(page))
        return self._public(row) if row else None

    def list_active(self, job_id: str) -> list[dict[str, object]]:
        with self._connection() as connection:
            connection.execute("DELETE FROM page_locks WHERE expires_at <= ?", (self._now(),))
            rows = connection.execute(
                "SELECT * FROM page_locks WHERE job_id = ? AND expires_at > ? ORDER BY page_number",
                (job_id, self._now()),
            ).fetchall()
        return [self._public(row) for row in rows]

    def acquire(
        self,
        job_id: str,
        page: int,
        user_id: str,
        username: str,
        client_instance_id: str,
    ) -> dict[str, object]:
        return self.acquire_many(job_id, [page], user_id, username, client_instance_id)[0]

    def acquire_many(
        self,
        job_id: str,
        pages: Sequence[int],
        user_id: str,
        username: str,
        client_instance_id: str,
    ) -> list[dict[str, object]]:
        page_numbers = sorted({int(page) for page in pages})
        if not page_numbers or any(page < 1 for page in page_numbers):
            raise ValueError("页码无效")
        if not str(user_id).strip() or not str(username).strip() or not str(client_instance_id).strip():
            raise ValueError("页面锁缺少所有者或浏览器窗口标识")
        now = self._now()
        created: list[dict[str, object]] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM page_locks WHERE expires_at <= ?", (now,))
            for page in page_numbers:
                row = self._active_row(connection, job_id, page)
                if row and not (
                    str(row["owner_user_id"]) == user_id
                    and str(row["client_instance_id"]) == client_instance_id
                ):
                    connection.rollback()
                    raise PageLockConflict(self._public(row))
            for page in page_numbers:
                token = secrets.token_urlsafe(32)
                connection.execute(
                    """
                    INSERT INTO page_locks(
                        job_id, page_number, owner_user_id, owner_username,
                        client_instance_id, lock_token_hash, acquired_at,
                        heartbeat_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id, page_number) DO UPDATE SET
                        owner_user_id=excluded.owner_user_id,
                        owner_username=excluded.owner_username,
                        client_instance_id=excluded.client_instance_id,
                        lock_token_hash=excluded.lock_token_hash,
                        acquired_at=excluded.acquired_at,
                        heartbeat_at=excluded.heartbeat_at,
                        expires_at=excluded.expires_at
                    """,
                    (
                        job_id, page, user_id, username, client_instance_id,
                        self._token_hash(token), now, now, now + self.lease_seconds,
                    ),
                )
                row = self._active_row(connection, job_id, page)
                created.append(self._public(row, token))
            connection.commit()
        return created

    def assert_owner(
        self,
        job_id: str,
        page: int,
        user_id: str,
        client_instance_id: str,
        token: str,
    ) -> dict[str, object]:
        with self._connection() as connection:
            row = self._active_row(connection, job_id, int(page))
        if not row:
            raise PageLockConflict()
        valid = (
            str(row["owner_user_id"]) == user_id
            and str(row["client_instance_id"]) == client_instance_id
            and hmac.compare_digest(str(row["lock_token_hash"]), self._token_hash(token))
        )
        if not valid:
            raise PageLockConflict(self._public(row))
        return self._public(row)

    def refresh(
        self,
        job_id: str,
        page: int,
        user_id: str,
        client_instance_id: str,
        token: str,
    ) -> dict[str, object]:
        self.assert_owner(job_id, page, user_id, client_instance_id, token)
        now = self._now()
        with self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE page_locks SET heartbeat_at = ?, expires_at = ?
                WHERE job_id = ? AND page_number = ? AND owner_user_id = ?
                  AND client_instance_id = ? AND lock_token_hash = ? AND expires_at > ?
                """,
                (now, now + self.lease_seconds, job_id, int(page), user_id, client_instance_id, self._token_hash(token), now),
            )
            if updated.rowcount != 1:
                raise PageLockConflict()
            row = self._active_row(connection, job_id, int(page))
        return self._public(row)

    def release(
        self,
        job_id: str,
        page: int,
        user_id: str,
        client_instance_id: str,
        token: str,
    ) -> bool:
        with self._connection() as connection:
            deleted = connection.execute(
                """
                DELETE FROM page_locks
                WHERE job_id = ? AND page_number = ? AND owner_user_id = ?
                  AND client_instance_id = ? AND lock_token_hash = ?
                """,
                (job_id, int(page), user_id, client_instance_id, self._token_hash(token)),
            )
        return deleted.rowcount == 1

    def release_user(self, user_id: str) -> int:
        if not str(user_id).strip():
            return 0
        with self._connection() as connection:
            deleted = connection.execute(
                "DELETE FROM page_locks WHERE owner_user_id = ?",
                (str(user_id),),
            )
        return deleted.rowcount
