"""Account and browser-session persistence backed by the shared jobs database."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Callable, Iterator
import uuid


SCRYPT_PARAMETERS = {"n": 16384, "r": 8, "p": 1, "dklen": 32}


class UsernameTaken(ValueError):
    pass


class InvalidCredentials(ValueError):
    pass


class AuthStore:
    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], float] = time.time,
        session_ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        self.path = Path(path)
        self._now = now
        self.session_ttl_seconds = max(60, int(session_ttl_seconds))

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
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    password_hash BLOB NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_parameters TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                """
            )
            connection.execute(
                "DELETE FROM sessions WHERE rowid NOT IN (SELECT MAX(rowid) FROM sessions GROUP BY user_id)"
            )
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS sessions_user ON sessions(user_id)")
            columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(users)")}
            if "is_admin" not in columns:
                connection.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, object]:
        return {
            "userId": str(row["user_id"]),
            "username": str(row["username"]),
            "createdAt": str(row["created_at"]),
            "isAdmin": bool(row["is_admin"]),
        }

    @staticmethod
    def _validate_registration(username: str, password: str) -> tuple[str, str]:
        clean_username = str(username).strip()
        if not 3 <= len(clean_username) <= 40:
            raise ValueError("用户名长度必须为 3 到 40 个字符")
        if not 6 <= len(password) <= 128:
            raise ValueError("密码长度必须为 6 到 128 个字符")
        return clean_username, clean_username.casefold()

    @staticmethod
    def _password_hash(password: str, salt: bytes, parameters: dict[str, int]) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, **parameters)

    @staticmethod
    def _session_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(self, username: str, password: str, *, is_admin: bool = False) -> dict[str, object]:
        clean_username, username_key = self._validate_registration(username, password)
        user_id = uuid.uuid4().hex
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt, SCRYPT_PARAMETERS)
        created_at = datetime.fromtimestamp(self._now()).isoformat(timespec="seconds")
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO users(
                        user_id, username, username_key, password_hash,
                        password_salt, password_parameters, is_admin, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        clean_username,
                        username_key,
                        password_hash,
                        salt,
                        json.dumps(SCRYPT_PARAMETERS, separators=(",", ":")),
                        int(bool(is_admin)),
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise UsernameTaken("用户名已被注册") from error
        return {
            "userId": user_id,
            "username": clean_username,
            "createdAt": created_at,
            "isAdmin": bool(is_admin),
        }

    def _find_active_user(self, username: str) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_key = ? AND disabled = 0",
                (str(username).strip().casefold(),),
            ).fetchone()
        return self._public_user(row) if row is not None else None

    def ensure_default_user(self, username: str, password: str, *, is_admin: bool = False) -> dict[str, object]:
        existing = self._find_active_user(username)
        if existing is not None:
            if is_admin and not existing.get("isAdmin"):
                with self._connection() as connection:
                    connection.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (existing["userId"],))
                return self._find_active_user(username) or existing
            return existing
        try:
            return self.register(username, password, is_admin=is_admin)
        except UsernameTaken:
            existing = self._find_active_user(username)
            if existing is None:
                raise ValueError("默认账号不可用")
            return existing

    def authenticate(self, username: str, password: str) -> dict[str, object]:
        username_key = str(username).strip().casefold()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_key = ? AND disabled = 0",
                (username_key,),
            ).fetchone()
            if row is None:
                raise InvalidCredentials("用户名或密码错误")
            parameters = json.loads(str(row["password_parameters"]))
            actual = self._password_hash(password, bytes(row["password_salt"]), parameters)
            if not hmac.compare_digest(actual, bytes(row["password_hash"])):
                raise InvalidCredentials("用户名或密码错误")
            connection.execute(
                "UPDATE users SET last_login_at = ? WHERE user_id = ?",
                (datetime.fromtimestamp(self._now()).isoformat(timespec="seconds"), row["user_id"]),
            )
        return self._public_user(row)

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        with self._connection() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                """INSERT INTO sessions(session_hash, user_id, created_at, last_seen_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       session_hash=excluded.session_hash,
                       created_at=excluded.created_at,
                       last_seen_at=excluded.last_seen_at,
                       expires_at=excluded.expires_at""",
                (self._session_hash(token), user_id, now, now, now + self.session_ttl_seconds),
            )
        return token

    def resolve_session(self, token: str | None) -> dict[str, object] | None:
        if not token:
            return None
        now = self._now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT users.* FROM sessions
                JOIN users ON users.user_id = sessions.user_id
                WHERE sessions.session_hash = ? AND sessions.expires_at > ? AND users.disabled = 0
                """,
                (self._session_hash(token), now),
            ).fetchone()
            if row is None:
                connection.execute("DELETE FROM sessions WHERE session_hash = ?", (self._session_hash(token),))
                return None
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE session_hash = ?",
                (now, self._session_hash(token)),
            )
        return self._public_user(row)

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connection() as connection:
            connection.execute("DELETE FROM sessions WHERE session_hash = ?", (self._session_hash(token),))
