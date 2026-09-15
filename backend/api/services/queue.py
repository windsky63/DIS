"""Read models and commands for the persistent analysis queue."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
import json
from pathlib import Path
from typing import Any

try:
    from ..exceptions import ApiError
    from ...job_store import JobStore
    from ...progress import result_progress_percent
except ImportError:  # Direct ``python backend/server.py`` execution.
    from api.exceptions import ApiError
    from job_store import JobStore
    from progress import result_progress_percent


def review_summary(events: list[dict[str, Any]], pages: list[dict[str, Any]]) -> dict[str, Any]:
    page_records: dict[int, dict[str, Any]] = {
        int(page.get("page") or 0): {
            "page": int(page.get("page") or 0), "saveCount": 0,
            "lastSavedAt": None, "reviewers": {},
        }
        for page in pages if int(page.get("page") or 0) > 0
    }
    for event in events:
        page_number = int(event.get("page_number") or 0)
        if page_number <= 0:
            continue
        page_record = page_records.setdefault(page_number, {
            "page": page_number, "saveCount": 0, "lastSavedAt": None, "reviewers": {},
        })
        username = str(event.get("username") or "未知用户")
        user_id = str(event.get("user_id") or "")
        saved_at = str(event.get("saved_at") or "") or None
        key = user_id or username
        reviewer = page_record["reviewers"].setdefault(key, {
            "userId": user_id, "username": username, "saveCount": 0, "lastSavedAt": None,
        })
        reviewer["saveCount"] += 1
        reviewer["lastSavedAt"] = saved_at or reviewer["lastSavedAt"]
        page_record["saveCount"] += 1
        page_record["lastSavedAt"] = saved_at or page_record["lastSavedAt"]

    # Older jobs predate the event table. Preserve their latest reviewer in the
    # queue while every new save is recorded as a durable database event above.
    for page in pages:
        page_number = int(page.get("page") or 0)
        page_record = page_records.get(page_number)
        reviewed_by = page.get("reviewedBy") if isinstance(page.get("reviewedBy"), dict) else None
        if not page_record or page_record["saveCount"] or not reviewed_by:
            continue
        username = str(reviewed_by.get("username") or "未知用户")
        user_id = str(reviewed_by.get("userId") or "")
        saved_at = str(page.get("reviewedAt") or "") or None
        page_record["saveCount"] = max(1, int(page.get("reviewRevision") or 1))
        page_record["lastSavedAt"] = saved_at
        page_record["reviewers"][user_id or username] = {
            "userId": user_id, "username": username,
            "saveCount": page_record["saveCount"], "lastSavedAt": saved_at,
        }

    task_reviewers: dict[str, dict[str, Any]] = {}
    serialized_pages = []
    for page_record in sorted(page_records.values(), key=lambda item: item["page"]):
        reviewers = sorted(page_record["reviewers"].values(), key=lambda item: item["username"])
        for reviewer in reviewers:
            key = reviewer["userId"] or reviewer["username"]
            task_reviewer = task_reviewers.setdefault(key, {
                "userId": reviewer["userId"], "username": reviewer["username"],
                "saveCount": 0, "lastSavedAt": None,
            })
            task_reviewer["saveCount"] += reviewer["saveCount"]
            if reviewer["lastSavedAt"] and (not task_reviewer["lastSavedAt"] or reviewer["lastSavedAt"] > task_reviewer["lastSavedAt"]):
                task_reviewer["lastSavedAt"] = reviewer["lastSavedAt"]
        serialized_pages.append({**page_record, "reviewers": reviewers})
    recent_events = [
        {
            "page": int(event.get("page_number") or 0),
            "revision": int(event.get("revision") or 0),
            "userId": str(event.get("user_id") or ""),
            "username": str(event.get("username") or "未知用户"),
            "savedAt": str(event.get("saved_at") or "") or None,
        }
        for event in events
        if int(event.get("page_number") or 0) > 0
    ]
    if not recent_events:
        recent_events = [
            {
                "page": item["page"], "revision": item["saveCount"],
                "userId": reviewer["userId"], "username": reviewer["username"],
                "savedAt": reviewer["lastSavedAt"],
            }
            for item in serialized_pages
            for reviewer in item["reviewers"]
            if reviewer["lastSavedAt"]
        ]
    recent_events.sort(key=lambda item: (str(item["savedAt"] or ""), item["revision"]), reverse=True)
    return {
        "pageReviews": serialized_pages,
        "reviewers": sorted(task_reviewers.values(), key=lambda item: item["username"]),
        "reviewedPageCount": sum(item["saveCount"] > 0 for item in serialized_pages),
        "recentReviewEvents": recent_events[:5],
    }


def snapshot(
    store: JobStore,
    data_root: Path,
    algorithm_version: str,
    max_concurrent: int,
) -> dict[str, Any]:
    store.import_existing(data_root, algorithm_version)
    records = store.list_jobs()
    queued_ids = [str(item["job_id"]) for item in records if item["status"] == "queued"]
    pending_positions = {job_id: index + 1 for index, job_id in enumerate(queued_ids)}
    running_statuses = {"leased", "processing", "cancelling"}
    jobs = []
    for record in records:
        job_id = str(record["job_id"])
        folder = data_root / job_id
        result_path, meta_path = folder / "result.json", folder / "job.json"
        if not result_path.is_file() or not meta_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        database_status = str(record.get("status") or result.get("status") or "unknown")
        queue_state = "running" if database_status in running_statuses else database_status
        pages = result.get("pages") if isinstance(result.get("pages"), list) else []
        review = review_summary(store.page_review_history(job_id), pages)
        reference_count = len(meta.get("referenceFiles") or [])
        jobs.append({
            "jobId": job_id,
            "fileName": meta.get("originalTargetName") or result.get("originalTargetName") or "未命名图纸",
            "status": "processing" if database_status in {"queued", *running_statuses} else database_status,
            "queueState": queue_state,
            "queuePosition": pending_positions.get(job_id),
            "progressStage": result.get("progressStage"),
            "progressMessage": result.get("progressMessage") or "",
            "completedPages": int(result.get("completedPages") or 0),
            "totalPages": int(result.get("totalPages") or 0),
            "completedReferenceFiles": int(result.get("completedReferenceFiles") or 0),
            "totalReferenceFiles": int(result.get("totalReferenceFiles") or 0),
            "drawingCount": 1 + reference_count,
            "designDrawingCount": 1,
            "referenceDrawingCount": reference_count,
            "pageCount": int(result.get("totalPages") or len(pages)),
            "project": meta.get("project") or result.get("project") or {},
            **review,
            "progressCompletedUnits": float(result.get("progressCompletedUnits") or 0),
            "progressTotalUnits": float(result.get("progressTotalUnits") or 0),
            "progressPercent": result_progress_percent(result),
            "createdAt": meta.get("createdAt") or result.get("createdAt"),
            "createdBy": record.get("createdBy") or meta.get("createdBy") or result.get("createdBy"),
            "updatedAt": result.get("updatedAt") or datetime.fromtimestamp(result_path.stat().st_mtime).isoformat(timespec="seconds"),
            "archivedAt": record.get("archived_at") or meta.get("archivedAt"),
            "isArchived": bool(record.get("archived_at") or meta.get("archivedAt")),
            "canRestore": database_status == "complete" and bool(result.get("pages")),
            "canCancel": database_status in {"queued", *running_statuses},
            "canReorder": database_status == "queued",
            "canArchive": database_status == "complete",
            "canDelete": database_status == "complete",
        })
    live = [item for item in jobs if item["queueState"] in {"running", "queued"}]
    live.sort(key=lambda item: (0 if item["queueState"] == "running" else 1, item.get("queuePosition") or 0))
    terminal = [item for item in jobs if item["queueState"] not in {"running", "queued"}]
    terminal.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    return {
        "jobs": live + terminal,
        "runningCount": sum(item["status"] in running_statuses for item in records),
        "queuedCount": len(pending_positions),
        "maxConcurrent": max_concurrent,
    }


def move(store: JobStore, job_id: str, direction: str) -> dict[str, Any]:
    try:
        return store.move_queued(job_id, direction)
    except KeyError as exc:
        raise ApiError("任务不存在", HTTPStatus.NOT_FOUND) from exc
    except (RuntimeError, ValueError) as exc:
        raise ApiError(str(exc), HTTPStatus.CONFLICT) from exc
