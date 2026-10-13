"""Business-facing administration statistics.

The dashboard deliberately combines durable task/page state from SQLite with
lightweight interaction events from the rotating JSONL audit log.  Database
rows describe the current result; audit events describe how much human work
was needed to get there.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any

try:
    from .time_utils import beijing_now_text
except ImportError:
    from time_utils import beijing_now_text


UNKNOWN_PROJECT = "未关联项目"
UNKNOWN_USER = "未记录用户"
BUSINESS_ACTIONS = {
    "marker.manual_added", "marker.content_corrected", "marker.layout_adjusted",
    "marker.deleted", "pdf.exported",
}


def _day(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 10:
        candidate = text[:10]
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError:
            return ""
    return ""


def _clean_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def _in_range(value: object, date_from: str, date_to: str) -> bool:
    current = _day(value)
    if not current:
        return False
    return (not date_from or current >= date_from) and (not date_to or current <= date_to)


def _safe_json(value: object) -> dict[str, Any]:
    try:
        result = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def _event_amount(event: dict[str, Any]) -> int:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    try:
        return max(1, min(10_000, int(details.get("count") or 1)))
    except (TypeError, ValueError):
        return 1


def _event_actor(event: dict[str, Any]) -> str:
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    # Only server-attested top-level identity is used.  Historical events did
    # not have it and are intentionally grouped as unknown rather than trusting
    # a client-supplied username in details.
    return str(actor.get("username") or event.get("username") or UNKNOWN_USER).strip() or UNKNOWN_USER


def _event_bucket(action: str) -> str:
    if action == "marker.manual_added":
        return "manualAdditions"
    if action == "marker.deleted":
        return "deletions"
    if action == "marker.content_corrected":
        return "contentCorrections"
    if action == "marker.layout_adjusted":
        return "layoutAdjustments"
    if action == "pdf.exported":
        return "exports"
    return "other"


class BusinessStats:
    def __init__(self, database_path: Path, audit_root: Path) -> None:
        self.database_path = Path(database_path)
        self.audit_root = Path(audit_root)

    def _audit_entries(self) -> list[dict[str, Any]]:
        paths = [
            path for path in self.audit_root.glob("events.jsonl*")
            if path.is_file() and not path.name.endswith(".tmp")
        ] if self.audit_root.is_dir() else []
        entries: list[dict[str, Any]] = []
        for path in sorted(paths, key=lambda item: item.name, reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
        return entries

    def _database_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        if not self.database_path.is_file():
            return [], [], []
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            tables = {str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            jobs = [dict(row) for row in connection.execute(
                """
                SELECT job_id, original_target_name, status, creator_username,
                       summary_json, error, created_at, updated_at, completed_at
                FROM jobs ORDER BY created_at DESC
                """
            )] if "jobs" in tables else []
            pages = [dict(row) for row in connection.execute(
                """
                SELECT job_id, page_number, page_json, reviewed_by_username,
                       reviewed_at, created_at, updated_at
                FROM job_pages
                """
            )] if "job_pages" in tables else []
            events = [{
                "action": str(row["action"]),
                "source": "server",
                "timestamp": str(row["created_at"]),
                "actor": {"userId": str(row["user_id"]), "username": str(row["username"])},
                "details": {
                    "jobId": str(row["job_id"] or ""),
                    "page": row["page_number"],
                    "count": int(row["amount"]),
                },
            } for row in connection.execute(
                """
                SELECT action, user_id, username, job_id, page_number, amount, created_at
                FROM business_events ORDER BY event_id
                """
            )] if "business_events" in tables else []
            return jobs, pages, events
        finally:
            connection.close()

    @staticmethod
    def _marker_counts(page_json: object) -> tuple[int, int]:
        page = _safe_json(page_json)
        automatic = manual = 0
        for candidate in page.get("candidates") if isinstance(page.get("candidates"), list) else []:
            if not isinstance(candidate, dict) or candidate.get("included") is False:
                continue
            if str(candidate.get("origin") or "recognized").casefold() == "manual":
                manual += 1
            else:
                automatic += 1
        return automatic, manual

    def snapshot(
        self, *, date_from: str = "", date_to: str = "", project: str = "",
        username: str = "", action: str = "",
    ) -> dict[str, Any]:
        date_from, date_to = _clean_date(date_from), _clean_date(date_to)
        if date_from and date_to and date_from > date_to:
            date_from, date_to = date_to, date_from
        project, username, action = (str(value or "").strip()[:120] for value in (project, username, action))

        all_jobs, all_pages, durable_events = self._database_rows()
        all_events = []
        for event in durable_events + self._audit_entries():
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            if (
                str(details.get("jobId") or "") == "tutorial-000207"
                or str(event.get("action") or "").startswith("tutorial.")
            ):
                continue
            all_events.append(event)
        job_context: dict[str, dict[str, str]] = {}
        project_options: set[str] = set()
        user_options: set[str] = set()
        for job in all_jobs:
            summary = _safe_json(job.get("summary_json"))
            project_data = summary.get("project") if isinstance(summary.get("project"), dict) else {}
            project_name = str(project_data.get("name") or UNKNOWN_PROJECT).strip() or UNKNOWN_PROJECT
            creator = str(job.get("creator_username") or UNKNOWN_USER).strip() or UNKNOWN_USER
            status = str(job.get("status") or "")
            job_context[str(job.get("job_id") or "")] = {
                "project": project_name, "username": creator, "status": status,
                "targetName": str(job.get("original_target_name") or job.get("job_id") or ""),
            }
            if status != "cancelled" and project_name != UNKNOWN_PROJECT:
                project_options.add(project_name)
            if status != "cancelled" and creator != UNKNOWN_USER:
                user_options.add(creator)

        def event_context(event: dict[str, Any]) -> dict[str, str]:
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            return job_context.get(str(details.get("jobId") or ""), {})

        for event in all_events:
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            context = event_context(event)
            if context.get("status") == "cancelled":
                continue
            project_name = str(context.get("project") or UNKNOWN_PROJECT).strip()
            actor = _event_actor(event)
            if project_name != UNKNOWN_PROJECT:
                project_options.add(project_name)
            if event.get("source") == "server" and actor != UNKNOWN_USER:
                user_options.add(actor)
        for page in all_pages:
            if job_context.get(str(page.get("job_id") or ""), {}).get("status") == "cancelled":
                continue
            reviewer = str(page.get("reviewed_by_username") or "").strip()
            if reviewer:
                user_options.add(reviewer)

        action_options = sorted({
            str(item.get("action") or "") for item in all_events
            if item.get("source") == "server" and item.get("action") in BUSINESS_ACTIONS
            and event_context(item).get("status") != "cancelled"
        })

        def job_activity_time(job: dict[str, Any]) -> object:
            if job.get("status") == "complete":
                return job.get("completed_at") or job.get("updated_at") or job.get("created_at")
            if job.get("status") in {"failed", "cancelled"}:
                return job.get("updated_at") or job.get("created_at")
            return job.get("created_at")

        def job_matches(job: dict[str, Any]) -> bool:
            context = job_context.get(str(job.get("job_id") or ""), {})
            return (
                job.get("status") != "cancelled"
                and _in_range(job_activity_time(job), date_from, date_to)
                and (not project or context.get("project") == project)
                and (not username or context.get("username") == username)
            )

        jobs = [job for job in all_jobs if job_matches(job)]

        def page_matches(page: dict[str, Any]) -> bool:
            context = job_context.get(str(page.get("job_id") or ""), {})
            creator = str(context.get("username") or UNKNOWN_USER).strip()
            return (
                context.get("status") != "cancelled"
                and _in_range(page.get("created_at") or page.get("updated_at"), date_from, date_to)
                and (not project or context.get("project") == project)
                and (not username or creator == username)
            )

        pages = [page for page in all_pages if page_matches(page)]

        def review_matches(page: dict[str, Any]) -> bool:
            context = job_context.get(str(page.get("job_id") or ""), {})
            reviewer = str(page.get("reviewed_by_username") or UNKNOWN_USER).strip()
            return (
                context.get("status") != "cancelled"
                and bool(page.get("reviewed_at"))
                and _in_range(page.get("reviewed_at"), date_from, date_to)
                and (not project or context.get("project") == project)
                and (not username or reviewer == username)
            )

        review_pages = [page for page in all_pages if review_matches(page)]

        def event_matches(event: dict[str, Any]) -> bool:
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            context = event_context(event)
            project_name = str(context.get("project") or UNKNOWN_PROJECT).strip()
            return (
                event.get("source") == "server"
                and str(event.get("action") or "") in BUSINESS_ACTIONS
                and context.get("status") != "cancelled"
                and _in_range(event.get("timestamp"), date_from, date_to)
                and (not project or project_name == project)
                and (not username or _event_actor(event) == username)
                and (not action or str(event.get("action") or "") == action)
            )

        events = [event for event in all_events if event_matches(event)]
        project_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "project": UNKNOWN_PROJECT, "jobs": 0, "completedJobs": 0, "failedJobs": 0,
            "documents": set(), "pages": 0, "reviewedPages": 0, "automaticMarkers": 0,
            "manualMarkers": 0, "manualAdditions": 0, "contentCorrections": 0,
            "layoutAdjustments": 0, "deletions": 0,
        })
        drawing_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "jobId": "", "targetName": "", "project": UNKNOWN_PROJECT, "status": "",
            "pages": 0, "reviewedPages": 0, "automaticMarkers": 0, "manualMarkers": 0,
            "manualAdditions": 0, "contentCorrections": 0, "layoutAdjustments": 0, "deletions": 0,
        })
        user_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "username": UNKNOWN_USER, "jobs": 0, "pages": 0, "reviewedPages": 0,
            "manualAdditions": 0, "contentCorrections": 0, "layoutAdjustments": 0,
            "deletions": 0, "exports": 0,
        })
        trend: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "date": "", "jobs": 0, "completedJobs": 0, "failedJobs": 0,
            "processedPages": 0, "reviewedPages": 0, "manualAdditions": 0,
            "contentCorrections": 0, "deletions": 0, "exports": 0,
        })
        active_users: set[str] = set()

        for job in jobs:
            job_id = str(job.get("job_id") or "")
            context = job_context.get(job_id, {})
            project_name = context.get("project") or UNKNOWN_PROJECT
            creator = context.get("username") or UNKNOWN_USER
            row = project_rows[project_name]
            row["project"] = project_name
            row["jobs"] += 1
            row["completedJobs"] += int(job.get("status") == "complete")
            row["failedJobs"] += int(job.get("status") == "failed")
            drawing = drawing_rows[job_id]
            drawing.update({
                "jobId": job_id,
                "targetName": str(job.get("original_target_name") or job_id),
                "project": project_name,
                "status": str(job.get("status") or ""),
            })
            user_rows[creator]["username"] = creator
            user_rows[creator]["jobs"] += 1
            if creator != UNKNOWN_USER:
                active_users.add(creator)
            day = _day(job_activity_time(job))
            if day:
                trend[day]["date"] = day
                trend[day]["jobs"] += 1
                trend[day]["completedJobs"] += int(job.get("status") == "complete")
                trend[day]["failedJobs"] += int(job.get("status") == "failed")

        automatic_markers = manual_markers = reviewed_pages = 0
        for page in pages:
            job_id = str(page.get("job_id") or "")
            context = job_context.get(job_id, {})
            project_name = context.get("project") or UNKNOWN_PROJECT
            creator = str(context.get("username") or UNKNOWN_USER).strip()
            automatic, manual = self._marker_counts(page.get("page_json"))
            automatic_markers += automatic
            manual_markers += manual
            reviewed = bool(page.get("reviewed_at"))
            reviewed_pages += int(reviewed)
            row = project_rows[project_name]
            row["project"] = project_name
            row["documents"].add(job_id)
            row["pages"] += 1
            row["reviewedPages"] += int(reviewed)
            row["automaticMarkers"] += automatic
            row["manualMarkers"] += manual
            drawing = drawing_rows[job_id]
            drawing["jobId"] = job_id
            drawing["targetName"] = str(context.get("targetName") or job_id)
            drawing["project"] = project_name
            drawing["pages"] += 1
            drawing["reviewedPages"] += int(reviewed)
            drawing["automaticMarkers"] += automatic
            drawing["manualMarkers"] += manual
            user_rows[creator]["username"] = creator
            user_rows[creator]["pages"] += 1
            if creator != UNKNOWN_USER:
                active_users.add(creator)
            day = _day(page.get("created_at") or page.get("updated_at"))
            if day:
                trend[day]["date"] = day
                trend[day]["processedPages"] += 1

        for page in review_pages:
            reviewer = str(page.get("reviewed_by_username") or UNKNOWN_USER).strip()
            user_rows[reviewer]["username"] = reviewer
            user_rows[reviewer]["reviewedPages"] += 1
            if reviewer != UNKNOWN_USER:
                active_users.add(reviewer)
            day = _day(page.get("reviewed_at"))
            if day:
                trend[day]["date"] = day
                trend[day]["reviewedPages"] += 1

        action_stats: Counter[str] = Counter()
        event_totals: Counter[str] = Counter()
        for event in events:
            event_action = str(event.get("action") or "unknown")
            if event_action not in BUSINESS_ACTIONS:
                continue
            amount = _event_amount(event)
            bucket = _event_bucket(event_action)
            action_stats[event_action] += amount
            event_totals[bucket] += amount
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            context = job_context.get(str(details.get("jobId") or ""), {})
            project_name = str(context.get("project") or UNKNOWN_PROJECT).strip()
            actor = _event_actor(event)
            user_rows[actor]["username"] = actor
            if actor != UNKNOWN_USER:
                active_users.add(actor)
            if bucket in {"manualAdditions", "contentCorrections", "layoutAdjustments", "deletions"}:
                project_rows[project_name]["project"] = project_name
                project_rows[project_name][bucket] += amount
                job_id = str(details.get("jobId") or "")
                if job_id:
                    drawing_rows[job_id]["jobId"] = job_id
                    drawing_rows[job_id]["targetName"] = str(context.get("targetName") or job_id)
                    drawing_rows[job_id]["project"] = project_name
                    drawing_rows[job_id][bucket] += amount
            if bucket in user_rows[actor]:
                user_rows[actor][bucket] += amount
            day = _day(event.get("timestamp"))
            if day and bucket in {"manualAdditions", "contentCorrections", "deletions", "exports"}:
                trend[day]["date"] = day
                trend[day][bucket] += amount

        total_jobs = len(jobs)
        completed_jobs = sum(job.get("status") == "complete" for job in jobs)
        failed_jobs = sum(job.get("status") == "failed" for job in jobs)
        total_pages = len(pages)
        processed_job_ids = {str(page.get("job_id") or "") for page in pages}
        final_markers = automatic_markers + manual_markers

        def finish_project(row: dict[str, Any]) -> dict[str, Any]:
            documents = len(row["documents"])
            pages_count = int(row["pages"])
            markers = int(row["automaticMarkers"]) + int(row["manualMarkers"])
            return {
                **{key: value for key, value in row.items() if key != "documents"},
                "documents": documents,
                "reviewRate": row["reviewedPages"] / pages_count if pages_count else None,
                "successRate": row["completedJobs"] / row["jobs"] if row["jobs"] else None,
                "automaticShare": row["automaticMarkers"] / markers if markers else None,
            }

        def finish_drawing(row: dict[str, Any]) -> dict[str, Any]:
            pages_count = int(row["pages"])
            markers = int(row["automaticMarkers"]) + int(row["manualMarkers"])
            return {
                **row,
                "targetName": row["targetName"] or row["jobId"],
                "reviewRate": row["reviewedPages"] / pages_count if pages_count else None,
                "automaticShare": row["automaticMarkers"] / markers if markers else None,
            }

        failures = [{
            "jobId": str(job.get("job_id") or ""),
            "targetName": str(job.get("original_target_name") or ""),
            "project": job_context.get(str(job.get("job_id") or ""), {}).get("project") or UNKNOWN_PROJECT,
            "username": job_context.get(str(job.get("job_id") or ""), {}).get("username") or UNKNOWN_USER,
            "error": str(job.get("error") or "未记录错误详情"),
            "updatedAt": str(job.get("updated_at") or ""),
        } for job in sorted(jobs, key=lambda item: str(item.get("updated_at") or ""), reverse=True)
          if job.get("status") == "failed"][:50]

        if date_from and date_to:
            start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
            if (end - start).days <= 366:
                current = start
                while current <= end:
                    trend[current.isoformat()]["date"] = current.isoformat()
                    current += timedelta(days=1)

        return {
            "generatedAt": beijing_now_text(),
            "filters": {"from": date_from, "to": date_to, "project": project, "username": username, "action": action},
            "filterOptions": {
                "projects": sorted(project_options), "users": sorted(user_options), "actions": action_options,
            },
            "summary": {
                "totalJobs": total_jobs, "completedJobs": completed_jobs, "failedJobs": failed_jobs,
                "processedDocuments": len(processed_job_ids), "processedPages": total_pages,
                "reviewedPages": reviewed_pages,
                "reviewCompletionRate": reviewed_pages / total_pages if total_pages else None,
                "automaticMarkers": automatic_markers, "manualMarkers": manual_markers,
                "manualAdditions": event_totals["manualAdditions"],
                "contentCorrections": event_totals["contentCorrections"],
                "layoutAdjustments": event_totals["layoutAdjustments"],
                "deletedMarkers": event_totals["deletions"], "exports": event_totals["exports"],
                "activeUsers": len(active_users),
                "successRate": completed_jobs / total_jobs if total_jobs else None,
                "automaticShare": automatic_markers / final_markers if final_markers else None,
            },
            "trends": sorted(trend.values(), key=lambda row: row["date"]),
            "projects": sorted((finish_project(row) for row in project_rows.values()), key=lambda row: (-row["pages"], row["project"])),
            "drawings": sorted((finish_drawing(row) for row in drawing_rows.values()), key=lambda row: (-row["pages"], row["targetName"])),
            "users": sorted(user_rows.values(), key=lambda row: (-(row["pages"] + row["manualAdditions"]), row["username"])),
            "actionStats": dict(action_stats.most_common()),
            "failures": failures,
            "definitions": {
                "automaticShare": "当前页面中保留的自动标识占最终标识的比例",
                "reviewCompletionRate": "已保存人工核对结果的页面占已生成页面的比例",
            },
        }
