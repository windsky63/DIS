"""Read-only unified audit timeline for administration views."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 else ""


def _is_tutorial_event(event: dict[str, Any]) -> bool:
    return (
        str(event.get("jobId") or "") == "tutorial-000207"
        or str(event.get("action") or "").startswith("tutorial.")
    )


class AuditStore:
    """Combine durable SQLite events and rotating JSONL events."""

    def __init__(self, database_path: Path, audit_root: Path) -> None:
        self.database_path = Path(database_path)
        self.audit_root = Path(audit_root)

    def _database_events(self) -> list[dict[str, Any]]:
        if not self.database_path.is_file():
            return []
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                str(row["name"])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            events: list[dict[str, Any]] = []
            if "job_events" in tables:
                rows = connection.execute(
                    """
                    SELECT e.event_id, e.job_id, e.event_type, e.details_json, e.created_at,
                           j.original_target_name
                    FROM job_events e LEFT JOIN jobs j ON j.job_id = e.job_id
                    """
                ).fetchall()
                events.extend({
                    "id": f"job:{row['event_id']}",
                    "timestamp": str(row["created_at"] or ""),
                    "source": "job",
                    "action": f"job.{row['event_type']}",
                    "userId": "",
                    "username": "",
                    "jobId": str(row["job_id"] or ""),
                    "pageNumber": None,
                    "targetName": str(row["original_target_name"] or ""),
                    "details": _json_object(row["details_json"]),
                } for row in rows)
            if "page_review_events" in tables:
                rows = connection.execute(
                    """
                    SELECT e.event_id, e.job_id, e.page_number, e.revision, e.user_id,
                           e.username, e.saved_at, j.original_target_name
                    FROM page_review_events e LEFT JOIN jobs j ON j.job_id = e.job_id
                    """
                ).fetchall()
                events.extend({
                    "id": f"review:{row['event_id']}",
                    "timestamp": str(row["saved_at"] or ""),
                    "source": "review",
                    "action": "page.saved",
                    "userId": str(row["user_id"] or ""),
                    "username": str(row["username"] or ""),
                    "jobId": str(row["job_id"] or ""),
                    "pageNumber": int(row["page_number"]),
                    "targetName": str(row["original_target_name"] or ""),
                    "details": {"revision": int(row["revision"])},
                } for row in rows)
            if "business_events" in tables:
                rows = connection.execute(
                    """
                    SELECT e.event_id, e.action, e.user_id, e.username, e.job_id,
                           e.page_number, e.amount, e.created_at, j.original_target_name
                    FROM business_events e LEFT JOIN jobs j ON j.job_id = e.job_id
                    """
                ).fetchall()
                events.extend({
                    "id": f"business:{row['event_id']}",
                    "timestamp": str(row["created_at"] or ""),
                    "source": "business",
                    "action": str(row["action"] or ""),
                    "userId": str(row["user_id"] or ""),
                    "username": str(row["username"] or ""),
                    "jobId": str(row["job_id"] or ""),
                    "pageNumber": int(row["page_number"]) if row["page_number"] is not None else None,
                    "targetName": str(row["original_target_name"] or ""),
                    "details": {"amount": int(row["amount"] or 0)},
                } for row in rows)
            return events
        finally:
            connection.close()

    def _jsonl_events(self) -> list[dict[str, Any]]:
        if not self.audit_root.is_dir():
            return []
        paths = sorted(
            (path for path in self.audit_root.glob("events.jsonl*") if path.is_file() and not path.name.endswith(".tmp")),
            key=lambda path: path.name,
        )
        events: list[dict[str, Any]] = []
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, 1):
                try:
                    event = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
                details = event.get("details") if isinstance(event.get("details"), dict) else {}
                page = details.get("pageNumber", details.get("page"))
                try:
                    page_number = int(page) if page not in (None, "") else None
                except (TypeError, ValueError):
                    page_number = None
                events.append({
                    "id": f"jsonl:{path.name}:{line_number}",
                    "timestamp": str(event.get("timestamp") or ""),
                    "source": str(event.get("source") or "client"),
                    "action": str(event.get("action") or "event"),
                    "userId": str(actor.get("userId") or event.get("userId") or ""),
                    "username": str(actor.get("username") or event.get("username") or ""),
                    "jobId": str(details.get("jobId") or event.get("jobId") or ""),
                    "pageNumber": page_number,
                    "targetName": str(details.get("targetName") or ""),
                    "details": details,
                })
        return events

    def list_events(
        self, *, date_from: str = "", date_to: str = "", username: str = "",
        action: str = "", source: str = "", job_id: str = "", search: str = "",
        page: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        date_from, date_to = _date(date_from), _date(date_to)
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from
        username, action, source, job_id, search = (
            str(value or "").strip()[:160] for value in (username, action, source, job_id, search)
        )
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        all_events = [
            event for event in self._database_events() + self._jsonl_events()
            if not _is_tutorial_event(event)
        ]
        options = {
            "users": sorted({event["username"] for event in all_events if event["username"]}, key=str.casefold),
            "actions": sorted({event["action"] for event in all_events if event["action"]}),
            "sources": sorted({event["source"] for event in all_events if event["source"]}),
        }

        needle = search.casefold()
        filtered = []
        for event in all_events:
            event_date = _date(event["timestamp"])
            if date_from and event_date < date_from:
                continue
            if date_to and event_date > date_to:
                continue
            if username and event["username"] != username:
                continue
            if action and event["action"] != action:
                continue
            if source and event["source"] != source:
                continue
            if job_id and job_id.casefold() not in event["jobId"].casefold():
                continue
            if needle:
                haystack = "\n".join((
                    event["action"], event["username"], event["jobId"], event["targetName"],
                    json.dumps(event["details"], ensure_ascii=False, sort_keys=True),
                )).casefold()
                if needle not in haystack:
                    continue
            filtered.append(event)
        filtered.sort(key=lambda event: (event["timestamp"], event["id"]), reverse=True)
        total = len(filtered)
        offset = (page - 1) * page_size
        return {
            "events": filtered[offset:offset + page_size],
            "pagination": {
                "page": page, "pageSize": page_size, "total": total,
                "totalPages": max(1, (total + page_size - 1) // page_size),
            },
            "filterOptions": options,
        }
