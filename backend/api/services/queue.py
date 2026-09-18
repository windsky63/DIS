"""Read models and commands for the persistent analysis queue."""

from __future__ import annotations

from http import HTTPStatus
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
    max_concurrent: int,
    *,
    scope: str = "current",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    scope = "archived" if scope == "archived" else "current"
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    queue_page = store.list_queue_page(scope, page, page_size)
    records = queue_page["records"]
    queued_ids = queue_page["queuedJobIds"]
    pending_positions = {job_id: index + 1 for index, job_id in enumerate(queued_ids)}
    running_statuses = {"leased", "processing", "cancelling"}
    complete_job_ids = [str(record["job_id"]) for record in records if record["status"] == "complete"]
    pages_by_job = store.get_job_page_summaries_many(complete_job_ids)
    reviews_by_job = store.page_review_histories(complete_job_ids)
    jobs = []
    for record in records:
        job_id = str(record["job_id"])
        summary = record.get("queueSummary") if isinstance(record.get("queueSummary"), dict) else {}
        database_status = str(record.get("status") or "unknown")
        queue_state = "running" if database_status in running_statuses else database_status
        pages = pages_by_job.get(job_id, [])
        review = review_summary(reviews_by_job.get(job_id, []), pages)
        reference_count = int(summary.get("referenceFileCount") or summary.get("totalReferenceFiles") or 0)
        jobs.append({
            "jobId": job_id,
            "fileName": record.get("original_target_name") or "未命名图纸",
            "status": "processing" if database_status in {"queued", *running_statuses} else database_status,
            "queueState": queue_state,
            "queuePosition": pending_positions.get(job_id),
            "progressStage": summary.get("progressStage"),
            "progressMessage": summary.get("progressMessage") or "",
            "completedPages": int(summary.get("completedPages") or 0),
            "totalPages": int(summary.get("totalPages") or 0),
            "completedReferenceFiles": int(summary.get("completedReferenceFiles") or 0),
            "totalReferenceFiles": int(summary.get("totalReferenceFiles") or 0),
            "layoutCompletedPages": int(summary.get("layoutCompletedPages") or 0),
            "layoutTotalPages": int(summary.get("layoutTotalPages") or 0),
            "drawingCount": 1 + reference_count,
            "designDrawingCount": 1,
            "referenceDrawingCount": reference_count,
            "pageCount": int(summary.get("totalPages") or len(pages)),
            "project": summary.get("project") or {},
            **review,
            "progressCompletedUnits": float(summary.get("progressCompletedUnits") or 0),
            "progressTotalUnits": float(summary.get("progressTotalUnits") or 0),
            "progressPercent": result_progress_percent({**summary, "status": database_status}),
            "createdAt": summary.get("createdAt") or record.get("created_at"),
            "createdBy": record.get("createdBy"),
            "updatedAt": record.get("updated_at") or summary.get("updatedAt"),
            "archivedAt": record.get("archived_at"),
            "isArchived": bool(record.get("archived_at")),
            "canRestore": database_status == "complete" and bool(pages),
            "canCancel": database_status in {"queued", *running_statuses},
            "canReorder": database_status == "queued",
            "canArchive": database_status == "complete",
            "canDelete": database_status == "complete",
        })
    return {
        "jobs": jobs,
        "runningCount": queue_page["runningCount"],
        "queuedCount": queue_page["queuedCount"],
        "archivedCount": queue_page["archivedCount"],
        "currentCount": queue_page["currentCount"],
        "maxConcurrent": max_concurrent,
        "pagination": {
            "scope": scope,
            "page": queue_page["page"],
            "pageSize": queue_page["pageSize"],
            "totalItems": queue_page["totalItems"],
            "totalPages": queue_page["totalPages"],
        },
    }


def move(store: JobStore, job_id: str, direction: str) -> dict[str, Any]:
    try:
        return store.move_queued(job_id, direction)
    except KeyError as exc:
        raise ApiError("任务不存在", HTTPStatus.NOT_FOUND) from exc
    except (RuntimeError, ValueError) as exc:
        raise ApiError(str(exc), HTTPStatus.CONFLICT) from exc
