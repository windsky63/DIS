"""Per-user AI assistant conversation persistence in the shared SQLite database."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from typing import Iterator


MAX_CONVERSATIONS = 20
MAX_MESSAGES = 100
MAX_MESSAGE_CHARS = 4_000
MAX_MESSAGE_SOURCES = 12
SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")
SOURCE_PATH_PATTERN = re.compile(r"^(?:README\.md|docs/[a-z0-9-]+\.md)$", re.IGNORECASE)


def _normalize_sources(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    sources: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("documentId") or "")
        title = str(item.get("title") or "").strip()[:160]
        relative_path = str(item.get("path") or "").replace("\\", "/").strip()
        section = str(item.get("section") or "").strip()[:240]
        try:
            start_line = int(item.get("startLine"))
        except (TypeError, ValueError):
            continue
        if (
            not SOURCE_ID_PATTERN.fullmatch(document_id)
            or not title
            or not section
            or not SOURCE_PATH_PATTERN.fullmatch(relative_path)
            or start_line < 1
        ):
            continue
        key = (document_id, section, start_line)
        if key in seen:
            continue
        seen.add(key)
        sources.append({
            "documentId": document_id,
            "title": title,
            "path": relative_path,
            "section": section,
            "startLine": start_line,
        })
        if len(sources) >= MAX_MESSAGE_SOURCES:
            break
    return sources


class AssistantConversationStore:
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
                CREATE TABLE IF NOT EXISTS assistant_conversations (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(user_id, conversation_id),
                    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS assistant_conversations_user_updated
                    ON assistant_conversations(user_id, updated_at DESC);
                """
            )

    @staticmethod
    def _normalize(conversation: object) -> dict[str, object]:
        if not isinstance(conversation, dict):
            raise ValueError("会话必须是对象")
        conversation_id = str(conversation.get("id") or "").strip()
        if not conversation_id or len(conversation_id) > 100:
            raise ValueError("会话标识无效")
        title = str(conversation.get("title") or "新对话").strip()[:40] or "新对话"
        try:
            updated_at = max(0, int(conversation.get("updatedAt") or 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("会话更新时间无效") from exc
        raw_messages = conversation.get("messages")
        if not isinstance(raw_messages, list):
            raise ValueError("会话消息必须是数组")
        messages: list[dict[str, object]] = []
        for message in raw_messages[-MAX_MESSAGES:]:
            if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
                raise ValueError("消息角色仅支持 user 或 assistant")
            content = str(message.get("content") or "").strip()
            if not content:
                raise ValueError("消息内容不能为空")
            if len(content) > MAX_MESSAGE_CHARS:
                raise ValueError(f"单条消息不能超过 {MAX_MESSAGE_CHARS} 个字符")
            normalized_message: dict[str, object] = {"role": str(message["role"]), "content": content}
            if message["role"] == "assistant":
                sources = _normalize_sources(message.get("sources"))
                if sources:
                    normalized_message["sources"] = sources
            messages.append(normalized_message)
        return {"id": conversation_id, "title": title, "updatedAt": updated_at, "messages": messages}

    def replace_all(self, user_id: str, conversations: object) -> list[dict[str, object]]:
        clean_user_id = str(user_id).strip()
        if not clean_user_id:
            raise ValueError("缺少用户标识")
        if not isinstance(conversations, list):
            raise ValueError("会话历史必须是数组")
        normalized = sorted(
            (self._normalize(item) for item in conversations),
            key=lambda item: int(item["updatedAt"]),
            reverse=True,
        )[:MAX_CONVERSATIONS]
        if len({str(item["id"]) for item in normalized}) != len(normalized):
            raise ValueError("会话标识不能重复")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("DELETE FROM assistant_conversations WHERE user_id = ?", (clean_user_id,))
                connection.executemany(
                    """
                    INSERT INTO assistant_conversations(user_id, conversation_id, title, messages_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [(
                        clean_user_id,
                        item["id"],
                        item["title"],
                        json.dumps(item["messages"], ensure_ascii=False, separators=(",", ":")),
                        item["updatedAt"],
                    ) for item in normalized],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return normalized

    def list_for_user(self, user_id: str) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT conversation_id, title, messages_json, updated_at
                FROM assistant_conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (str(user_id), MAX_CONVERSATIONS),
            ).fetchall()
        return [{
            "id": str(row["conversation_id"]),
            "title": str(row["title"]),
            "updatedAt": int(row["updated_at"]),
            "messages": json.loads(str(row["messages_json"])),
        } for row in rows]
