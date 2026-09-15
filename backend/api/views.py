"""HTTP API for the topology-first weld marker.

Long-running recognition is delegated to independent durable queue workers.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import threading
import time
import traceback
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen
import uuid
import zipfile

import fitz

try:
    from .. import ai_assistant
    from ..assistant_conversation_store import AssistantConversationStore
    from ..engine import dump_result, render_page, write_annotated_pdf
    from ..auth_store import AuthStore, InvalidCredentials, UsernameTaken
    from ..iso_weld_matcher.idf_topology import parser_availability
    from ..job_store import ActiveJobExists, JobStore
    from ..page_lock_store import PageLockConflict, PageLockStore
    from ..page_reviews import PageRevisionConflict, merge_review_page
    from ..progress import result_progress_percent
    from .exceptions import ApiError
    from .services import queue, uploads
except ImportError:  # Direct ``python backend/server.py`` execution.
    import ai_assistant
    from assistant_conversation_store import AssistantConversationStore
    from engine import dump_result, render_page, write_annotated_pdf
    from auth_store import AuthStore, InvalidCredentials, UsernameTaken
    from iso_weld_matcher.idf_topology import parser_availability
    from job_store import ActiveJobExists, JobStore
    from page_lock_store import PageLockConflict, PageLockStore
    from page_reviews import PageRevisionConflict, merge_review_page
    from progress import result_progress_percent
    from api.exceptions import ApiError
    from api.services import queue, uploads


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "data" / "jobs"
UPLOAD_ROOT = ROOT / "data" / "uploads"
FRONTEND_DIST = ROOT / "frontend" / "dist"
PCF_LIBRARY_ROOT = ROOT / "backend" / "PCF"
TUTORIAL_ROOT = ROOT / "backend" / "tutorial"
TUTORIAL_EXPORT_ROOT = ROOT / "data" / "tutorial-exports"
AUDIT_ROOT = ROOT / "data" / "audit"
APP_VERSION = os.environ.get("DRAWING_MARK_RECOGNITION_VERSION", "2.0.6")
BUILD_ID = os.environ.get("DRAWING_MARK_RECOGNITION_BUILD_ID", "local")
MAX_BODY_BYTES = int(os.environ.get("DRAWING_MARK_RECOGNITION_MAX_BODY_MB", "350")) * 1024 * 1024
MAX_JOB_REQUEST_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = int(os.environ.get("DRAWING_MARK_RECOGNITION_MAX_UPLOAD_FILE_MB", "350")) * 1024 * 1024
MIN_FREE_DISK_BYTES = int(os.environ.get("DRAWING_MARK_RECOGNITION_MIN_FREE_DISK_MB", "512")) * 1024 * 1024
UPLOAD_RETENTION_HOURS = max(1, int(os.environ.get("DRAWING_MARK_RECOGNITION_UPLOAD_RETENTION_HOURS", "24")))
MAX_CONCURRENT_UPLOADS = max(1, int(os.environ.get("DRAWING_MARK_RECOGNITION_MAX_CONCURRENT_UPLOADS", "2")))
MAX_PENDING_ANALYSES = max(1, int(os.environ.get("DRAWING_MARK_RECOGNITION_MAX_PENDING_ANALYSES", "4")))
MAX_CONCURRENT_ANALYSES = max(1, int(os.environ.get("DRAWING_MARK_RECOGNITION_MAX_CONCURRENT_ANALYSES", "1")))
JOB_RETENTION_DAYS = max(1, int(os.environ.get("DRAWING_MARK_RECOGNITION_JOB_RETENTION_DAYS", "90")))
AUDIT_MAX_BYTES = max(1024 * 1024, int(os.environ.get("DRAWING_MARK_RECOGNITION_AUDIT_MAX_MB", "10")) * 1024 * 1024)
AUDIT_BACKUP_COUNT = max(1, int(os.environ.get("DRAWING_MARK_RECOGNITION_AUDIT_BACKUPS", "5")))
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
LOCK = threading.RLock()
UPLOAD_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_UPLOADS)
JOB_STORES: dict[Path, JobStore] = {}
AUTH_STORES: dict[Path, AuthStore] = {}
ASSISTANT_CONVERSATION_STORES: dict[Path, AssistantConversationStore] = {}
PAGE_LOCK_STORES: dict[Path, PageLockStore] = {}
SESSION_COOKIE_NAME = "drawing_marker_session"
SESSION_COOKIE_SECURE = os.environ.get("DRAWING_MARK_RECOGNITION_SESSION_SECURE", "false").casefold() in {"1", "true", "yes"}
MINERU_BASE_URL = os.environ.get("MINERU_BASE_URL", "http://192.168.32.61:8081").rstrip("/")
# Increment whenever recognition semantics change so completed results from an
# older engine are not silently reused for the same files and configuration.
ANALYSIS_ALGORITHM_VERSION = os.environ.get(
    "DRAWING_MARK_RECOGNITION_ALGORITHM_VERSION", "2026-09-08.1"
)


def _job_store() -> JobStore:
    path = DATA_ROOT / ".queue" / "jobs.db"
    with LOCK:
        store = JOB_STORES.get(path)
        if store is None:
            store = JobStore(path)
            store.initialize()
            JOB_STORES[path] = store
        return store


def _auth_store() -> AuthStore:
    path = DATA_ROOT / ".queue" / "jobs.db"
    with LOCK:
        store = AUTH_STORES.get(path)
        if store is None:
            store = AuthStore(path)
            store.initialize()
            store.ensure_default_user("admin", "123456")
            AUTH_STORES[path] = store
        return store


def _assistant_conversation_store() -> AssistantConversationStore:
    path = DATA_ROOT / ".queue" / "jobs.db"
    with LOCK:
        _auth_store()
        store = ASSISTANT_CONVERSATION_STORES.get(path)
        if store is None:
            store = AssistantConversationStore(path)
            store.initialize()
            ASSISTANT_CONVERSATION_STORES[path] = store
        return store


def _page_lock_store() -> PageLockStore:
    path = DATA_ROOT / ".queue" / "jobs.db"
    with LOCK:
        store = PAGE_LOCK_STORES.get(path)
        if store is None:
            store = PageLockStore(path)
            store.initialize()
            PAGE_LOCK_STORES[path] = store
        return store


def _require_job_page(job_id: str, page_number: int) -> None:
    result_path = _job_folder(job_id) / "result.json"
    if not result_path.is_file():
        raise ApiError("任务不存在", HTTPStatus.NOT_FOUND)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("status") != "complete":
        raise ApiError("任务尚未完成，不能开始核对", HTTPStatus.CONFLICT)
    if not any(int(page.get("page") or 0) == page_number for page in result.get("pages") or []):
        raise ApiError("页面结果不存在", HTTPStatus.NOT_FOUND)


def _console(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def _rotate_audit_log() -> None:
    path = AUDIT_ROOT / "events.jsonl"
    if not path.is_file() or path.stat().st_size < AUDIT_MAX_BYTES:
        return
    oldest = path.with_name(f"{path.name}.{AUDIT_BACKUP_COUNT}")
    if oldest.exists():
        oldest.unlink()
    for index in range(AUDIT_BACKUP_COUNT - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def _job_status(folder: Path) -> str:
    result_path = folder / "result.json"
    if not result_path.is_file():
        return ""
    try:
        return str(json.loads(result_path.read_text(encoding="utf-8")).get("status") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def _analysis_queue_snapshot() -> dict[str, Any]:
    return queue.snapshot(
        _job_store(), DATA_ROOT, ANALYSIS_ALGORITHM_VERSION, MAX_CONCURRENT_ANALYSES
    )


def _move_queued_job(job_id: str, direction: str) -> dict[str, Any]:
    return queue.move(_job_store(), job_id, direction)


def _set_job_archived(job_id: str, archived: bool) -> dict[str, Any]:
    folder = _job_folder(job_id)
    meta_path = folder / "job.json"
    with LOCK:
        if not meta_path.is_file() or _job_status(folder) != "complete":
            raise ApiError("只有已完成的解析任务可以归档", HTTPStatus.CONFLICT)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if archived:
            meta["archivedAt"] = datetime.now().isoformat(timespec="seconds")
        else:
            meta.pop("archivedAt", None)
        _atomic_dump(meta_path, meta)
        _job_store().set_archived(job_id, meta.get("archivedAt"))
    return {"jobId": job_id, "archived": archived, "archivedAt": meta.get("archivedAt")}


def _delete_completed_job(job_id: str) -> None:
    folder = _job_folder(job_id)
    with LOCK:
        record = _job_store().get(job_id)
        if record and record.get("status") in {"queued", "leased", "processing", "cancelling"}:
            raise ApiError("正在解析或等待中的任务不能删除", HTTPStatus.CONFLICT)
        if (record and record.get("status") != "complete") or (not record and _job_status(folder) != "complete"):
            raise ApiError("只有已完成的解析任务可以删除", HTTPStatus.CONFLICT)
        if not folder.is_dir():
            raise ApiError("任务不存在", HTTPStatus.NOT_FOUND)
        active_locks = _page_lock_store().list_active(job_id)
        if active_locks:
            owners = "、".join(f"第 {lock['page']} 页（{lock['owner']['username']}）" for lock in active_locks)
            raise ApiError(f"任务仍有页面正在核对：{owners}", HTTPStatus.LOCKED)
        shutil.rmtree(folder)
        _job_store().delete(job_id)


def _reconcile_interrupted_jobs() -> int:
    """Import legacy folders; live Worker leases remain untouched."""

    return _job_store().import_existing(DATA_ROOT, ANALYSIS_ALGORITHM_VERSION)


def _cleanup_expired_jobs() -> int:
    """Remove terminal jobs older than the configured retention period."""

    removed = 0
    if not DATA_ROOT.is_dir():
        return removed
    cutoff = datetime.now().timestamp() - timedelta(days=JOB_RETENTION_DAYS).total_seconds()
    for folder in DATA_ROOT.iterdir():
        if not folder.is_dir() or not JOB_ID.fullmatch(folder.name):
            continue
        result_path = folder / "result.json"
        try:
            modified = result_path.stat().st_mtime
        except OSError:
            modified = folder.stat().st_mtime
        record = _job_store().get(folder.name)
        if record and record.get("archived_at"):
            continue
        status = str(record.get("status")) if record else _job_status(folder)
        if modified >= cutoff or status not in {"complete", "failed", "cancelled"}:
            continue
        shutil.rmtree(folder)
        _job_store().delete(folder.name)
        removed += 1
    return removed


def _cleanup_expired_uploads() -> int:
    """Remove streamed uploads that were never attached to a job."""

    removed = 0
    if not UPLOAD_ROOT.is_dir():
        return removed
    cutoff = time.time() - timedelta(hours=UPLOAD_RETENTION_HOURS).total_seconds()
    for folder in UPLOAD_ROOT.iterdir():
        if not folder.is_dir() or not JOB_ID.fullmatch(folder.name):
            continue
        try:
            if folder.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(folder)
            removed += 1
        except OSError:
            _console(f"无法清理临时上传：{folder.name}")
    return removed


def _mineru_health() -> dict[str, Any]:
    url = f"{MINERU_BASE_URL}/health"
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        ready = (payload.get("ready") is True or payload.get("status") == "ok") and payload.get("worker_alive") is not False
        return {
            "status": "ready" if ready else "offline",
            "url": MINERU_BASE_URL,
            "message": "MinerU 服务及工作进程已就绪" if ready else "MinerU 已响应，但工作进程未就绪",
            "details": payload,
        }
    except Exception as exc:
        return {"status": "offline", "url": MINERU_BASE_URL, "message": f"无法连接 MinerU：{exc}"}


def _analysis_worker_health() -> dict[str, Any]:
    workers = _job_store().live_workers()
    return {
        "status": "ready" if workers else "offline",
        "count": len(workers),
        "message": "解析Worker可用" if workers else "未检测到解析Worker；新任务会保留在队列中等待恢复",
    }


def _safe_name(value: str, fallback: str) -> str:
    return uploads.safe_name(value, fallback)


def _decode_file(payload: dict[str, Any] | None, folder: Path, fallback: str) -> Path | None:
    return uploads.decode_file(payload, folder, fallback)


def _decode_files(payloads: Any, folder: Path, prefix: str, default_suffix: str = ".pdf") -> list[Path]:
    return uploads.decode_files(payloads, folder, prefix, default_suffix)


def _upload_record(upload_id: str) -> tuple[Path, dict[str, Any]]:
    return uploads.upload_record(UPLOAD_ROOT, upload_id)


def _store_upload(source: Any, length: int, original_name: str) -> dict[str, Any]:
    return uploads.store_upload(
        source,
        length,
        original_name,
        upload_root=UPLOAD_ROOT,
        max_upload_bytes=MAX_UPLOAD_BYTES,
        min_free_disk_bytes=MIN_FREE_DISK_BYTES,
        dump_json=_atomic_dump,
    )


def _consume_upload(payload: Any, folder: Path, fallback: str) -> Path | None:
    return uploads.consume_upload(UPLOAD_ROOT, payload, folder, fallback)


def _consume_uploads(payloads: Any, folder: Path, prefix: str, default_suffix: str = ".pdf") -> list[Path]:
    return uploads.consume_uploads(UPLOAD_ROOT, payloads, folder, prefix, default_suffix)


def _job_folder(job_id: str) -> Path:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("任务编号无效")
    folder = (DATA_ROOT / job_id).resolve()
    if folder.parent != DATA_ROOT.resolve():
        raise ValueError("任务路径无效")
    return folder


def _job_reference_manifest(job_id: str) -> list[dict[str, Any]]:
    """Describe reference PDFs retained with a job without exposing paths."""

    folder = _job_folder(job_id)
    meta_path = folder / "job.json"
    if not meta_path.is_file():
        raise ApiError("任务不存在", HTTPStatus.NOT_FOUND)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    manifest = []
    for index, stored_name in enumerate(meta.get("referenceFiles") or []):
        safe_stored_name = Path(str(stored_name)).name
        path = folder / safe_stored_name
        if not safe_stored_name or not path.is_file():
            continue
        display_name = safe_stored_name.split("__", 1)[-1]
        manifest.append({
            "index": index,
            "name": display_name,
            "url": f"/api/jobs/{job_id}/references/{index}",
            "size": path.stat().st_size,
        })
    return manifest


def _pcf_library_folders() -> list[dict[str, Any]]:
    if not PCF_LIBRARY_ROOT.is_dir():
        return []
    result = []
    for folder in sorted((path for path in PCF_LIBRARY_ROOT.rglob("*") if path.is_dir()), key=lambda path: str(path).casefold()):
        files = _pcf_files_in_folder(folder)
        if not files:
            continue
        relative = folder.relative_to(PCF_LIBRARY_ROOT).as_posix()
        result.append({"value": relative, "title": relative.replace("/", " / "), "count": len(files)})
    return result


def _resolve_pcf_library_folder(value: Any) -> list[Path]:
    if not value:
        return []
    root = PCF_LIBRARY_ROOT.resolve()
    relative = str(value).strip().replace("\\", "/")
    if not relative:
        return []
    folder = (root / relative).resolve()
    if folder != root and root not in folder.parents:
        raise ValueError("PCF 文件夹路径无效")
    if not folder.is_dir():
        raise ValueError("所选 PCF 文件夹不存在")
    files = _pcf_files_in_folder(folder)
    if not files:
        raise ValueError("所选文件夹中没有 PCF 文件")
    return files


def _pcf_files_in_folder(folder: Path) -> list[Path]:
    """Return a stable, case-insensitive PCF inventory for a server-side folder."""
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix.casefold() == ".pcf"),
        key=lambda path: path.name.casefold(),
    )


def _payload_file_signature(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    upload_id = payload.get("uploadId")
    if upload_id:
        _path, meta = _upload_record(str(upload_id))
        return {
            "name": _safe_name(payload.get("name") or meta.get("name"), "file").casefold(),
            "content": str(meta.get("sha256") or ""),
        }
    encoded = payload.get("dataBase64")
    if not isinstance(encoded, str) or not encoded:
        return None
    return {
        "name": _safe_name(payload.get("name"), "file").casefold(),
        "content": hashlib.sha256(encoded.encode("ascii", errors="ignore")).hexdigest(),
    }


def _analysis_signature(payload: dict[str, Any]) -> str:
    pcf_inventory = []
    for path in _resolve_pcf_library_folder(payload.get("pcfFolder")) if payload.get("pcfFolder") else []:
        stat = path.stat()
        pcf_inventory.append([path.name.casefold(), stat.st_size, stat.st_mtime_ns])
    descriptor = {
        "algorithmVersion": ANALYSIS_ALGORITHM_VERSION,
        "target": _payload_file_signature(payload.get("targetUpload") or payload.get("targetPdf")),
        "referencePdfs": [_payload_file_signature(item) for item in (payload.get("referenceUploads") or payload.get("referencePdfs") or [])],
        "referencePdf": _payload_file_signature(payload.get("referencePdf")),
        "pcfFiles": [_payload_file_signature(item) for item in (payload.get("pcfUploads") or payload.get("pcfFiles") or [])],
        "pcfFile": _payload_file_signature(payload.get("pcfFile")),
        "pcfFolder": payload.get("pcfFolder") or None,
        "pcfInventory": pcf_inventory,
        "symbolConfig": payload.get("symbolConfig") or {},
        "startPage": max(1, int(payload.get("startPage") or 1)),
        "endPage": int(payload["endPage"]) if payload.get("endPage") else None,
    }
    canonical = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_existing_analysis(signature: str) -> dict[str, Any] | None:
    records = []
    if not DATA_ROOT.is_dir():
        return None
    for meta_path in DATA_ROOT.glob("*/job.json"):
        result_path = meta_path.parent / "result.json"
        if not result_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("analysisSignature") != signature:
                continue
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job_id = meta_path.parent.name
        if result.get("status") != "complete":
            continue
        result.pop("labelLayout", None)
        result["createdBy"] = meta.get("createdBy") or result.get("createdBy")
        result["progressPercent"] = result_progress_percent(result)
        records.append({"jobId": job_id, "result": result, "modified": result_path.stat().st_mtime})
    if not records:
        return None
    records.sort(key=lambda item: item["modified"], reverse=True)
    return records[0]


def _atomic_dump(path: Path, payload: dict[str, Any]) -> None:
    # A fixed ``result.json.tmp`` lets writers from another server process
    # clobber each other's staging file.  It also turns a short-lived Windows
    # file lock (virus scanner/indexer/reader without delete sharing) into a
    # failed analysis job.  Keep the staging file beside the destination so
    # the final replace remains atomic, but make it private to this write and
    # retry transient sharing/access violations.
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        dump_result(temporary, payload)
        delay = 0.02
        for attempt in range(12):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.25)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # Do not mask the original replace error if an external process
            # is still holding the staging file too.
            pass


def _recent_batch_results() -> dict[str, Any]:
    records = []
    if not DATA_ROOT.is_dir():
        return {"batchId": None, "jobs": []}
    for result_path in DATA_ROOT.glob("*/result.json"):
        meta_path = result_path.parent / "job.json"
        if not meta_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("tutorial") or meta.get("archivedAt") or meta.get("analysisAlgorithmVersion") != ANALYSIS_ALGORITHM_VERSION:
            continue
        if result.get("status") != "complete" or not result.get("pages"):
            continue
        records.append({"modified": result_path.stat().st_mtime, "result": result, "meta": meta})
    if not records:
        return {"batchId": None, "jobs": []}
    latest = max(records, key=lambda item: item["modified"])
    batch_id = latest["meta"].get("batchId")
    selected = [item for item in records if batch_id and item["meta"].get("batchId") == batch_id] if batch_id else [latest]
    selected.sort(key=lambda item: (int(item["meta"].get("batchIndex") or 0), item["modified"]))
    jobs = []
    for item in selected:
        restored = _workspace_result(item["result"])
        restored["originalTargetName"] = item["meta"].get("originalTargetName") or restored.get("originalTargetName")
        restored["createdBy"] = item["meta"].get("createdBy") or restored.get("createdBy")
        jobs.append(restored)
    return {"batchId": batch_id, "jobs": jobs}


def _workspace_result(result: dict[str, Any], initial_page: int | None = None) -> dict[str, Any]:
    """Return editable data while deferring geometry for non-visible pages."""

    pages = list(result.get("pages") or [])
    if initial_page is None and pages:
        initial_page = int(pages[0].get("page") or 1)
    lightweight_pages = []
    for page in pages:
        item = dict(page)
        if int(item.get("page") or 0) != int(initial_page or 0):
            item.pop("layoutObstacles", None)
            item["detailsLoaded"] = False
        else:
            item["detailsLoaded"] = True
        lightweight_pages.append(item)
    workspace = dict(result) | {"pages": lightweight_pages, "workspaceView": "lazy-page-details"}
    workspace.pop("labelLayout", None)
    return workspace


def _merge_saved_pages(payload: Any, current: dict[str, Any]) -> list[dict[str, Any]]:
    """Preserve deferred immutable fields when a lazy workspace is saved."""

    validated = _validated_pages(payload, current)
    existing = {int(page.get("page") or 0): page for page in current.get("pages") or []}
    return [dict(existing.get(int(page.get("page") or 0), {})) | page for page in validated]


def _active_analysis(signature: str) -> dict[str, Any] | None:
    record = _job_store().find_active_by_signature(signature)
    if not record:
        return None
    job_id = str(record["job_id"])
    result_path = _job_folder(job_id) / "result.json"
    if not result_path.is_file():
        return {"jobId": job_id, "status": "processing", "pages": [], "progressMessage": "相同任务正在初始化"}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"jobId": job_id, "status": "processing", "pages": [], "progressMessage": "相同任务正在初始化"}
    result["status"] = "processing" if record["status"] in {"queued", "leased", "processing"} else record["status"]
    result["createdBy"] = record.get("createdBy") or result.get("createdBy")
    result["queueState"] = "queued" if record["status"] == "queued" else "running"
    result["progressPercent"] = result_progress_percent(result)
    return result


def _validated_pages(payload: Any, current: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ApiError("pages 必须是数组")
    if len(payload) > 10_000:
        raise ApiError("页面数据数量超过限制")
    analyzed = current.get("analyzedRange") or [1, int(current.get("pageCount") or 1)]
    first = max(1, int(analyzed[0] or 1))
    last = max(first, int(analyzed[1] or first))
    candidate_total = 0
    seen_pages: set[int] = set()
    for page in payload:
        if not isinstance(page, dict):
            raise ApiError("页面数据格式无效")
        try:
            page_number = int(page.get("page"))
        except (TypeError, ValueError) as exc:
            raise ApiError("页面编号无效") from exc
        if page_number < first or page_number > last or page_number in seen_pages:
            raise ApiError(f"页面编号 {page_number} 不在任务范围内或重复")
        seen_pages.add(page_number)
        candidates = page.get("candidates")
        if not isinstance(candidates, list):
            raise ApiError(f"第 {page_number} 页候选数据必须是数组")
        candidate_total += len(candidates)
        if candidate_total > 200_000:
            raise ApiError("候选对象数量超过限制")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ApiError(f"第 {page_number} 页存在无效候选对象")
            try:
                candidate_page = int(candidate.get("page") or page_number)
            except (TypeError, ValueError) as exc:
                raise ApiError("候选对象页码无效") from exc
            if candidate_page != page_number:
                raise ApiError("候选对象页码与所属页面不一致")
            for key in ("id", "number", "referenceLabel"):
                if key in candidate and len(str(candidate.get(key) or "")) > 200:
                    raise ApiError(f"候选对象字段 {key} 超过长度限制")
            for key in ("x", "y", "labelX", "labelY"):
                if key not in candidate:
                    continue
                try:
                    value = float(candidate[key])
                except (TypeError, ValueError) as exc:
                    raise ApiError(f"候选对象坐标 {key} 无效") from exc
                if not (-1_000_000.0 <= value <= 1_000_000.0):
                    raise ApiError(f"候选对象坐标 {key} 超出限制")
    return payload


def _require_complete_job(folder: Path) -> tuple[Path, dict[str, Any]]:
    result_path = folder / "result.json"
    if not result_path.is_file():
        raise ApiError("任务不存在", HTTPStatus.NOT_FOUND)
    current = json.loads(result_path.read_text(encoding="utf-8"))
    if current.get("status") != "complete":
        raise ApiError("任务尚未完成，不能保存或导出", HTTPStatus.CONFLICT)
    return result_path, current


def _tutorial_reference_sources() -> list[Path]:
    return sorted((TUTORIAL_ROOT / "000207").glob("*.pdf"), key=lambda path: path.name.casefold())


def _create_tutorial_session() -> dict[str, Any]:
    """Load the bundled tutorial as a protected resource, without creating a job."""
    source_pdf = TUTORIAL_ROOT / "000207.pdf"
    source_result = TUTORIAL_ROOT / "result.json"
    if not source_pdf.is_file() or not source_result.is_file():
        raise ApiError("教程示例资源尚未安装", HTTPStatus.SERVICE_UNAVAILABLE)

    result = json.loads(source_result.read_text(encoding="utf-8"))
    result.update({
        "jobId": "tutorial-000207",
        "status": "complete",
        "targetFile": "000207.pdf",
        "originalTargetName": "000207.pdf",
        "revision": 0,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "progressStage": "complete",
        "progressMessage": "教程预解析结果已载入",
        "referenceMode": "tutorial-reference-pdf",
    })
    reference_sources = _tutorial_reference_sources()
    tutorial_pages = sorted(result.get("pages", []), key=lambda item: int(item.get("page") or 0))
    if not reference_sources or len(reference_sources) != len(tutorial_pages):
        raise ApiError("教程对照资源数量与教程页数不一致", HTTPStatus.SERVICE_UNAVAILABLE)

    inventory_documents = result.get("ep3dReferenceInventory", {}).get("documents", [])
    if len(inventory_documents) != len(reference_sources):
        raise ApiError("教程预解析结果缺少真实对照图识别数据", HTTPStatus.SERVICE_UNAVAILABLE)
    if any(not (page.get("reference") or {}).get("eligibleReferencePages") for page in tutorial_pages):
        raise ApiError("教程预解析结果缺少设计图与对照图关系", HTTPStatus.SERVICE_UNAVAILABLE)
    result["referenceFiles"] = [path.name for path in reference_sources]
    return result


def _write_training_sample(
    job_id: str, folder: Path, *, confirmed_by_human: bool = False
) -> tuple[Path, int]:
    """Build the training archive without requiring a frontend request."""

    result_path, meta_path = folder / "result.json", folder / "job.json"
    if not result_path.exists() or not meta_path.exists():
        raise ValueError("任务不存在")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    candidates = [
        {**candidate, "confirmedByHuman": confirmed_by_human}
        for page in result.get("pages", [])
        for candidate in page.get("candidates", [])
        if candidate.get("included", True)
    ]
    page_numbers = sorted({int(item.get("page") or 1) for item in candidates})
    image_paths = []
    for page_number in page_numbers:
        image = folder / f"training-page-{page_number}.png"
        if not image.exists():
            render_page(folder / meta["targetFile"], page_number, image, scale=2.0)
        image_paths.append((page_number, image))
    manifest = {
        "schema": "weld-marker.training.v1",
        "jobId": job_id,
        "createdAt": datetime.now().isoformat(),
        "exportMode": "post-analysis-auto" if not confirmed_by_human else "manual-confirmed",
        "candidateCount": len(candidates),
        "candidates": candidates,
    }
    output = folder / "training-sample.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for page_number, image in image_paths:
            archive.write(image, f"pages/page-{page_number}.png")
    return output, len(candidates)


CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


def _is_client_disconnect(exc: BaseException) -> bool:
    """Return whether a response failed because the browser closed the socket."""

    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, CLIENT_DISCONNECT_ERRORS):
            return True
        if isinstance(current, OSError) and (
            getattr(current, "winerror", None) in {10053, 10054}
            or getattr(current, "errno", None) in {32, 54, 103, 104}
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


class ApiHandler(SimpleHTTPRequestHandler):
    server_version = f"DrawingMarkRecognition/{APP_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        if args and "GET /api/health " in str(args[0]):
            return
        _console(fmt % args)

    def end_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in {"http://127.0.0.1:3004", "http://localhost:3004"}:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-File-Name, X-Page-Lock-Token, X-Client-Instance-Id")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        if origin in {"http://127.0.0.1:3004", "http://localhost:3004"}:
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
        except Exception as exc:
            self._handle_exception(exc)

    def _json(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _sse_event(self, event: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _stream_ai_chat(self, payload: dict[str, Any]) -> None:
        try:
            events = iter(ai_assistant.stream_chat_completion(
                payload.get("messages"), context=payload.get("context"),
            ))
            first_event = next(events)
        except ai_assistant.AssistantInputError as exc:
            raise ApiError(str(exc), HTTPStatus.BAD_REQUEST) from exc
        except ai_assistant.AssistantConfigurationError as exc:
            raise ApiError(str(exc), HTTPStatus.SERVICE_UNAVAILABLE) from exc
        except (ai_assistant.AssistantRequestError, StopIteration) as exc:
            message = str(exc) or "AI 服务返回了空回答"
            raise ApiError(message, HTTPStatus.BAD_GATEWAY) from exc

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            def send_event(event: object) -> None:
                event_type = getattr(event, "type", "")
                event_payload = getattr(event, "payload", None)
                if event_type not in {"status", "source", "delta"} or not isinstance(event_payload, dict):
                    raise ai_assistant.AssistantRequestError("AI 服务产生了未知事件")
                self._sse_event(event_type, event_payload)

            send_event(first_event)
            for event in events:
                send_event(event)
            self._sse_event("done", {})
        except ai_assistant.AssistantError as exc:
            self._sse_event("error", {"error": str(exc)})
        except OSError:
            return

    def _read_json(self, max_bytes: int = MAX_BODY_BYTES) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ApiError("Content-Length 无效") from exc
        if length <= 0:
            return {}
        if length > max_bytes:
            raise ApiError(f"请求内容超过 {max_bytes // 1024 // 1024}MB 限制", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("请求内容不是有效的 JSON") from exc
        if not isinstance(payload, dict):
            raise ApiError("JSON 请求主体必须是对象")
        return payload

    def _handle_exception(self, exc: Exception) -> None:
        # Closing a tab cancels in-flight PDF and API requests.  The standard
        # library reports this as WinError 10053/10054 (or BrokenPipe on Unix);
        # it is normal client behaviour and must not trigger a second write.
        if _is_client_disconnect(exc):
            return
        try:
            if isinstance(exc, ApiError):
                self._json(exc.status, {"error": str(exc)})
                return
            if isinstance(exc, PageLockConflict):
                payload: dict[str, Any] = {"error": str(exc)}
                if exc.lock:
                    payload["lock"] = exc.lock
                self._json(HTTPStatus.LOCKED, payload)
                return
            if isinstance(exc, PageRevisionConflict):
                self._json(HTTPStatus.CONFLICT, {"error": str(exc), "currentPageRevision": exc.current_revision})
                return
            if isinstance(exc, (ValueError, TypeError, KeyError, json.JSONDecodeError)):
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            error_id = uuid.uuid4().hex[:12]
            _console(f"请求处理失败 [{error_id}]：{exc}")
            traceback.print_exc()
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"服务器内部错误，错误编号：{error_id}"})
        except OSError as response_error:
            # The client can disconnect between the original failure and the
            # error response headers/body.  Nothing remains to send.
            if _is_client_disconnect(response_error):
                return
            raise

    def _session_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE_NAME)
        return morsel.value if morsel else None

    def _current_user(self) -> dict[str, object] | None:
        cached = getattr(self, "_request_user", None)
        if cached:
            return cached
        user = _auth_store().resolve_session(self._session_token())
        if user:
            self._request_user = user
        return user

    def _require_user(self) -> dict[str, object]:
        user = self._current_user()
        if not user:
            raise ApiError("请先登录", HTTPStatus.UNAUTHORIZED)
        return user

    def _require_same_origin(self) -> None:
        headers = getattr(self, "headers", {})
        origin = str(headers.get("Origin") or "").rstrip("/")
        if not origin:
            return
        host = str(headers.get("Host") or "")
        allowed = {f"http://{host}", f"https://{host}", "http://127.0.0.1:3004", "http://localhost:3004"}
        if origin not in allowed:
            raise ApiError("拒绝跨站修改请求", HTTPStatus.FORBIDDEN)

    @staticmethod
    def _session_cookie(token: str, *, clear: bool = False) -> str:
        parts = [f"{SESSION_COOKIE_NAME}={'' if clear else token}", "Path=/", "HttpOnly", "SameSite=Lax"]
        if clear:
            parts.extend(("Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"))
        else:
            parts.append(f"Max-Age={7 * 24 * 60 * 60}")
        if SESSION_COOKIE_SECURE:
            parts.append("Secure")
        return "; ".join(parts)

    def _register(self) -> None:
        payload = self._read_json(MAX_JOB_REQUEST_BYTES)
        try:
            user = _auth_store().register(str(payload.get("username") or ""), str(payload.get("password") or ""))
        except UsernameTaken as exc:
            raise ApiError(str(exc), HTTPStatus.CONFLICT) from exc
        token = _auth_store().create_session(str(user["userId"]))
        self._request_user = user
        self._json(HTTPStatus.CREATED, {"user": user}, {"Set-Cookie": self._session_cookie(token)})

    def _login(self) -> None:
        payload = self._read_json(MAX_JOB_REQUEST_BYTES)
        try:
            user = _auth_store().authenticate(str(payload.get("username") or ""), str(payload.get("password") or ""))
        except InvalidCredentials as exc:
            raise ApiError(str(exc), HTTPStatus.UNAUTHORIZED) from exc
        token = _auth_store().create_session(str(user["userId"]))
        self._request_user = user
        self._json(HTTPStatus.OK, {"user": user}, {"Set-Cookie": self._session_cookie(token)})

    def _logout(self) -> None:
        _auth_store().delete_session(self._session_token())
        self._request_user = None
        self._json(HTTPStatus.OK, {"loggedOut": True}, {"Set-Cookie": self._session_cookie("", clear=True)})

    def _file(self, path: Path, download_name: str | None = None, cache_control: str | None = None) -> None:
        if not path.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "文件不存在"})
            return
        self.send_response(HTTPStatus.OK)
        content_type = {
            ".js": "application/javascript; charset=utf-8",
            ".mjs": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".wasm": "application/wasm",
        }.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        if download_name:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download_name)}")
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile)

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = unquote(urlparse(self.path).path)
            if path.startswith("/api/") and path != "/api/health":
                self._require_user()
            self._do_GET()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self._json(HTTPStatus.OK, {
                "status": "ready",
                "version": APP_VERSION,
                "buildId": BUILD_ID,
                "algorithmVersion": ANALYSIS_ALGORITHM_VERSION,
                "engine": "PDF轴测图图元研究",
                "architecture": "vue3+python-api+sqlite-queue+worker",
                "analysisWorkers": _analysis_worker_health(),
                "mineru": _mineru_health(),
                "pcfParsers": parser_availability(),
            })
            return
        if path == "/api/auth/me":
            self._json(HTTPStatus.OK, {"user": self._require_user()})
            return
        if path == "/api/ai/status":
            self._json(HTTPStatus.OK, ai_assistant.configuration_status())
            return
        if path == "/api/ai/conversations":
            user = self._require_user()
            conversations = _assistant_conversation_store().list_for_user(str(user["userId"]))
            self._json(HTTPStatus.OK, {"conversations": conversations})
            return
        if path == "/api/tutorial/sample":
            self._file(TUTORIAL_ROOT / "000207.pdf", "000207.pdf")
            return
        if path == "/api/tutorial/session":
            result = _create_tutorial_session()
            query = parse_qs(parsed.query)
            if query.get("view") == ["workspace"]:
                requested_page = query.get("page", [None])[0]
                try:
                    initial_page = int(requested_page) if requested_page is not None else None
                except (TypeError, ValueError):
                    initial_page = None
                result = _workspace_result(result, initial_page)
            self._json(HTTPStatus.OK, result)
            return
        match = re.fullmatch(r"/api/tutorial/pages/(\d+)", path)
        if match:
            page_number = int(match.group(1))
            result = _create_tutorial_session()
            page = next((item for item in result.get("pages", []) if int(item.get("page") or 0) == page_number), None)
            if page is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "教程页面结果不存在"})
                return
            self._json(HTTPStatus.OK, {"page": page})
            return
        if path == "/api/tutorial/references":
            self._json(HTTPStatus.OK, {"files": [
                {"index": index, "name": source.name, "url": f"/api/tutorial/references/{index}"}
                for index, source in enumerate(_tutorial_reference_sources())
            ]})
            return
        match = re.fullmatch(r"/api/tutorial/references/(\d+)", path)
        if match:
            sources = _tutorial_reference_sources()
            index = int(match.group(1))
            if index < 0 or index >= len(sources):
                raise ApiError("教程对照 PDF 不存在", HTTPStatus.NOT_FOUND)
            self._file(sources[index], sources[index].name)
            return
        match = re.fullmatch(r"/api/tutorial/exports/([a-f0-9]{32})\.pdf", path)
        if match:
            self._file(TUTORIAL_EXPORT_ROOT / f"{match.group(1)}.pdf", "教程标识结果.pdf")
            return
        if path == "/api/pcf-folders":
            self._json(HTTPStatus.OK, {"folders": _pcf_library_folders()})
            return
        if path == "/api/jobs/recent-batch":
            self._json(HTTPStatus.OK, _recent_batch_results())
            return
        if path == "/api/analysis-queue":
            self._json(HTTPStatus.OK, _analysis_queue_snapshot())
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/locks", path)
        if match:
            self._json(HTTPStatus.OK, {"locks": _page_lock_store().list_active(match.group(1))})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path)
        if match:
            job_id = match.group(1)
            result_path = _job_folder(job_id) / "result.json"
            if not result_path.is_file():
                initializing = bool(_job_store().get(job_id))
                if initializing:
                    self._json(HTTPStatus.OK, {
                        "jobId": job_id,
                        "status": "processing",
                        "pages": [],
                        "progressStage": "initializing",
                        "progressPercent": 15,
                        "progressMessage": "任务正在接收并校验输入文件",
                    })
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
                return
            result = json.loads(result_path.read_text(encoding="utf-8"))
            record = _job_store().get(job_id)
            result["createdBy"] = result.get("createdBy") or (record or {}).get("createdBy")
            result.pop("labelLayout", None)
            result["progressPercent"] = result_progress_percent(result)
            query = parse_qs(parsed.query)
            if query.get("view") == ["workspace"]:
                requested_page = query.get("page", [None])[0]
                try:
                    initial_page = int(requested_page) if requested_page is not None else None
                except (TypeError, ValueError):
                    initial_page = None
                result = _workspace_result(result, initial_page)
            self._json(HTTPStatus.OK, result)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/references", path)
        if match:
            self._json(HTTPStatus.OK, {"files": _job_reference_manifest(match.group(1))})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/target", path)
        if match:
            folder = _job_folder(match.group(1))
            meta_path = folder / "job.json"
            if not meta_path.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
                return
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            target_name = Path(str(meta.get("targetFile") or "target.pdf")).name

            self._file(folder / target_name, meta.get("originalTargetName") or "target.pdf")
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/references/(\d+)", path)
        if match:
            job_id, requested_index = match.group(1), int(match.group(2))
            entry = next((item for item in _job_reference_manifest(job_id) if item["index"] == requested_index), None)
            if not entry:
                self._json(HTTPStatus.NOT_FOUND, {"error": "对照 PDF 不存在"})
                return
            meta = json.loads((_job_folder(job_id) / "job.json").read_text(encoding="utf-8"))
            stored_name = Path(str((meta.get("referenceFiles") or [])[requested_index])).name
            self._file(_job_folder(job_id) / stored_name, entry["name"])
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/page-details", path)
        if match:
            result_path = _job_folder(match.group(1)) / "result.json"
            if not result_path.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
                return
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self._json(HTTPStatus.OK, {"pages": [
                {
                    "page": int(page.get("page") or 0),
                    "layoutObstacles": page.get("layoutObstacles") or {"textRects": [], "processSegments": []},
                }
                for page in result.get("pages") or []
            ]})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/pages/(\d+)\.png", path)
        if match:
            folder = _job_folder(match.group(1))
            page_number = int(match.group(2))
            image_path = folder / f"page-{page_number}.png"
            if not image_path.exists():
                meta_path = folder / "job.json"
                if not meta_path.exists():
                    self._json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
                    return
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                render_page(folder / meta["targetFile"], page_number, image_path)
            self._file(image_path)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/pages/(\d+)", path)
        if match:
            result_path = _job_folder(match.group(1)) / "result.json"
            if not result_path.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
                return
            page_number = int(match.group(2))
            result = json.loads(result_path.read_text(encoding="utf-8"))
            page = next((item for item in result.get("pages", []) if int(item.get("page") or 0) == page_number), None)
            if page is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "页面结果不存在"})
                return
            self._json(HTTPStatus.OK, {"page": page})
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/annotated\.pdf", path)
        if match:
            self._file(_job_folder(match.group(1)) / "annotated.pdf", "焊口标识结果.pdf")
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/training-sample\.zip", path)
        if match:
            self._file(_job_folder(match.group(1)) / "training-sample.zip", "焊口AI训练样本.zip")
            return
        self._serve_frontend(path)

    def _serve_frontend(self, request_path: str) -> None:
        if not FRONTEND_DIST.is_dir():
            self._json(HTTPStatus.NOT_FOUND, {"error": "前端尚未构建，请在 frontend 目录运行 npm install 和 npm run build"})
            return
        relative = request_path.lstrip("/") or "index.html"
        candidate = (FRONTEND_DIST / relative).resolve()
        if FRONTEND_DIST.resolve() not in candidate.parents or not candidate.is_file():
            candidate = FRONTEND_DIST / "index.html"
        cache_control = "public, max-age=31536000, immutable" if candidate.parent.name == "assets" else "no-store, no-cache, must-revalidate"
        self._file(candidate, cache_control=cache_control)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            self._require_same_origin()
            if parsed.path == "/api/auth/register":
                self._register()
                return
            if parsed.path == "/api/auth/login":
                self._login()
                return
            if parsed.path == "/api/auth/logout":
                self._logout()
                return
            self._require_user()
            if parsed.path == "/api/uploads":
                self._upload_file()
                return
            if parsed.path == "/api/jobs":
                self._create_job()
                return
            if parsed.path == "/api/tutorial/export":
                self._export_tutorial()
                return
            if parsed.path == "/api/audit":
                payload = self._read_json()
                action = re.sub(r"[^a-zA-Z0-9_.:-]", "-", str(payload.get("action") or "event"))[:80]
                details = payload.get("details") or {}
                if not isinstance(details, dict):
                    raise ApiError("审计详情必须是对象")
                encoded_details = json.dumps(details, ensure_ascii=False)
                if len(encoded_details.encode("utf-8")) > 64 * 1024:
                    raise ApiError("审计详情超过 64KB 限制", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                event = {"action": action, "timestamp": datetime.now().isoformat(timespec="seconds"), "details": details}
                AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
                with LOCK:
                    _rotate_audit_log()
                    with (AUDIT_ROOT / "events.jsonl").open("a", encoding="utf-8") as target:
                        target.write(json.dumps(event, ensure_ascii=False) + "\n")
                self._json(HTTPStatus.OK, {"logged": True})
                return
            if parsed.path == "/api/ai/chat":
                payload = self._read_json(MAX_JOB_REQUEST_BYTES)
                try:
                    content = ai_assistant.chat_completion(payload.get("messages"), context=payload.get("context"))
                except ai_assistant.AssistantInputError as exc:
                    raise ApiError(str(exc), HTTPStatus.BAD_REQUEST) from exc
                except ai_assistant.AssistantConfigurationError as exc:
                    raise ApiError(str(exc), HTTPStatus.SERVICE_UNAVAILABLE) from exc
                except ai_assistant.AssistantRequestError as exc:
                    raise ApiError(str(exc), HTTPStatus.BAD_GATEWAY) from exc
                self._json(HTTPStatus.OK, {"message": {"role": "assistant", "content": content}})
                return
            if parsed.path == "/api/ai/chat/stream":
                self._stream_ai_chat(self._read_json(MAX_JOB_REQUEST_BYTES))
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/pages/(\d+)/lock", parsed.path)
            if match:
                job_id, page_number = match.group(1), int(match.group(2))
                _require_job_page(job_id, page_number)
                payload = self._read_json(MAX_JOB_REQUEST_BYTES)
                client_id = str(payload.get("clientInstanceId") or "").strip()
                if not client_id:
                    raise ApiError("缺少浏览器窗口标识")
                user = self._require_user()
                lock = _page_lock_store().acquire(
                    job_id, page_number, str(user["userId"]), str(user["username"]), client_id,
                )
                self._json(HTTPStatus.OK, {"lock": lock})
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/pages/(\d+)/lock/heartbeat", parsed.path)
            if match:
                payload = self._read_json(MAX_JOB_REQUEST_BYTES)
                user = self._require_user()
                lock = _page_lock_store().refresh(
                    match.group(1), int(match.group(2)), str(user["userId"]),
                    str(payload.get("clientInstanceId") or ""), str(payload.get("lockToken") or ""),
                )
                self._json(HTTPStatus.OK, {"lock": lock})
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/cancel", parsed.path)
            if match:
                self._cancel_job(match.group(1))
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/queue-position", parsed.path)
            if match:
                payload = self._read_json()
                direction = str(payload.get("direction") or "")
                if direction not in {"up", "down", "front"}:
                    raise ApiError("队列调整方向无效")
                self._json(HTTPStatus.OK, _move_queued_job(match.group(1), direction))
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/archive", parsed.path)
            if match:
                payload = self._read_json()
                self._json(HTTPStatus.OK, _set_job_archived(match.group(1), bool(payload.get("archived", True))))
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/export", parsed.path)
            if match:
                self._export_job(match.group(1))
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/training-sample", parsed.path)
            if match:
                self._training_sample(match.group(1))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
        except Exception as exc:
            self._handle_exception(exc)

    def _upload_file(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ApiError("Content-Length 无效") from exc
        if not UPLOAD_SLOTS.acquire(blocking=False):
            raise ApiError("同时上传的文件过多，请稍后重试", HTTPStatus.TOO_MANY_REQUESTS)
        try:
            _cleanup_expired_uploads()
            record = _store_upload(self.rfile, length, self.headers.get("X-File-Name") or "upload.bin")
        finally:
            UPLOAD_SLOTS.release()
        self._json(HTTPStatus.CREATED, record)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        lock_match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/pages/(\d+)/lock", path)
        if lock_match:
            try:
                self._require_same_origin()
                user = self._require_user()
                payload = self._read_json(MAX_JOB_REQUEST_BYTES)
                released = _page_lock_store().release(
                    lock_match.group(1), int(lock_match.group(2)), str(user["userId"]),
                    str(payload.get("clientInstanceId") or ""), str(payload.get("lockToken") or ""),
                )
                self._json(HTTPStatus.OK, {"released": released})
            except Exception as exc:
                self._handle_exception(exc)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path)
        if not match:
            self._json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        try:
            self._require_same_origin()
            self._require_user()
            _delete_completed_job(match.group(1))
            self._json(HTTPStatus.OK, {"deleted": True, "jobId": match.group(1)})
        except Exception as exc:
            self._handle_exception(exc)

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/ai/conversations":
            try:
                self._require_same_origin()
                user = self._require_user()
                payload = self._read_json(MAX_JOB_REQUEST_BYTES)
                conversations = _assistant_conversation_store().replace_all(
                    str(user["userId"]), payload.get("conversations"),
                )
                self._json(HTTPStatus.OK, {"conversations": conversations})
            except Exception as exc:
                self._handle_exception(exc)
            return
        page_match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/pages/(\d+)", path)
        if page_match:
            try:
                self._require_same_origin()
                user = self._require_user()
                job_id, page_number = page_match.group(1), int(page_match.group(2))
                payload = self._read_json()
                client_id = str(payload.get("clientInstanceId") or "")
                lock_token = str(payload.get("lockToken") or "")
                lock_store = _page_lock_store()
                lock_store.assert_owner(job_id, page_number, str(user["userId"]), client_id, lock_token)
                with LOCK:
                    lock_store.assert_owner(job_id, page_number, str(user["userId"]), client_id, lock_token)
                    result_path, current = _require_complete_job(_job_folder(job_id))
                    incoming_page = payload.get("page")
                    if not isinstance(incoming_page, dict):
                        raise ApiError("缺少页面保存内容")
                    validated = _validated_pages([incoming_page], current)[0]
                    updated_page = merge_review_page(
                        current, page_number, validated, int(payload.get("basePageRevision") or 0), user,
                    )
                    current.pop("labelLayout", None)
                    _atomic_dump(result_path, current)
                self._json(HTTPStatus.OK, {
                    "saved": True,
                    "page": page_number,
                    "reviewRevision": updated_page["reviewRevision"],
                    "reviewedBy": updated_page["reviewedBy"],
                    "reviewedAt": updated_page["reviewedAt"],
                })
            except Exception as exc:
                self._handle_exception(exc)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path)
        if not match:
            self._json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        try:
            self._require_same_origin()
            self._require_user()
            raise ApiError("整图保存已停用，请逐页保存核对结果", HTTPStatus.CONFLICT)
        except Exception as exc:
            self._handle_exception(exc)

    def _create_job(self) -> None:
        # Files use /api/uploads. Keeping job creation metadata small prevents
        # legacy Base64 JSON requests from recreating the previous memory spike.
        payload = self._read_json(MAX_JOB_REQUEST_BYTES)
        symbol_config = payload.get("symbolConfig") if isinstance(payload.get("symbolConfig"), dict) else {}
        detection_mode = str(symbol_config.get("detectionMode") or "placement").casefold()
        if detection_mode != "placement":
            raise ApiError("对照模式暂未开放，请使用落图模式", HTTPStatus.BAD_REQUEST)
        payload["symbolConfig"] = {**symbol_config, "detectionMode": "placement"}
        request_user = getattr(self, "_request_user", None)
        created_by = (
            {"userId": str(request_user["userId"]), "username": str(request_user["username"])}
            if request_user else None
        )
        analysis_signature = _analysis_signature(payload)
        store = _job_store()
        with LOCK:
            if not payload.get("forceReanalyze"):
                active = _active_analysis(analysis_signature)
                if active:
                    reused = dict(active)
                    reused.update({"reusedExisting": True, "duplicateStatus": reused.get("status")})
                    self._json(HTTPStatus.OK, reused)
                    return
                existing = _find_existing_analysis(analysis_signature)
                if existing:
                    reused = dict(existing["result"])
                    reused.update({
                        "reusedExisting": True,
                        "duplicateStatus": reused.get("status"),
                        "requiresReanalysisConfirmation": reused.get("status") == "complete",
                        "existingUpdatedAt": datetime.fromtimestamp(existing["modified"]).isoformat(timespec="seconds"),
                    })
                    self._json(HTTPStatus.OK, reused)
                    return
            if store.count_active() >= MAX_PENDING_ANALYSES:
                raise ApiError("当前等待或执行中的分析任务过多，请稍后重试", HTTPStatus.TOO_MANY_REQUESTS)
            job_id = uuid.uuid4().hex
            folder = DATA_ROOT / job_id
            folder.mkdir(parents=True, exist_ok=False)
        _console(f"[智能编号] 收到任务 {job_id}")
        registered = False
        try:
            target = (
                _consume_upload(payload.get("targetUpload"), folder, "target.pdf")
                if payload.get("targetUpload")
                else _decode_file(payload.get("targetPdf"), folder, "target.pdf")
            )
            if target is None or target.suffix.lower() != ".pdf":
                raise ValueError("必须上传待标识 PDF")
            references = (
                _consume_uploads(payload.get("referenceUploads"), folder, "reference")
                if payload.get("referenceUploads") is not None
                else _decode_files(payload.get("referencePdfs"), folder, "reference")
            )
            legacy_reference = _decode_file(payload.get("referencePdf"), folder, "reference-legacy.pdf")
            if legacy_reference:
                references.append(legacy_reference)
            if any(reference.suffix.lower() != ".pdf" for reference in references):
                raise ValueError("对照图必须全部是 PDF")
            job_pcfs = (
                _consume_uploads(payload.get("pcfUploads"), folder, "source", ".pcf")
                if payload.get("pcfUploads") is not None
                else _decode_files(payload.get("pcfFiles"), folder, "source", ".pcf")
            )
            legacy_pcf = _decode_file(payload.get("pcfFile"), folder, "source-legacy.pcf")
            if legacy_pcf:
                job_pcfs.append(legacy_pcf)
            library_pcfs = _resolve_pcf_library_folder(payload.get("pcfFolder"))
            pcfs = [*job_pcfs, *library_pcfs]
            if any(pcf.suffix.lower() != ".pcf" for pcf in pcfs):
                raise ValueError("管道源文件必须全部是 PCF")
            requested_start = int(payload.get("startPage") or 1)
            requested_end = int(payload["endPage"]) if payload.get("endPage") else None
            with fitz.open(target) as target_document:
                resolved_start = max(1, requested_start)
                resolved_end = min(target_document.page_count, requested_end or target_document.page_count)
                total_pages = max(0, resolved_end - resolved_start + 1)
            target_payload = payload.get("targetUpload") if isinstance(payload.get("targetUpload"), dict) else {}
            if not target_payload:
                target_payload = payload.get("targetPdf") if isinstance(payload.get("targetPdf"), dict) else {}
            original_target_name = _safe_name(payload.get("originalTargetName") or target_payload.get("name"), target.name)
            _atomic_dump(folder / "job.json", {
                "targetFile": target.name,
                "originalTargetName": original_target_name,
                "referenceFiles": [item.name for item in references],
                "pcfSources": [
                    *[{"scope": "job", "path": item.name} for item in job_pcfs],
                    *[{"scope": "absolute", "path": str(item)} for item in library_pcfs],
                ],
                "pcfFolder": payload.get("pcfFolder"),
                "batchId": payload.get("batchId"),
                "batchIndex": int(payload.get("batchIndex") or 0),
                "analysisSignature": analysis_signature,
                "analysisAlgorithmVersion": ANALYSIS_ALGORITHM_VERSION,
                "symbolConfig": payload.get("symbolConfig") if isinstance(payload.get("symbolConfig"), dict) else {},
                "startPage": requested_start,
                "endPage": requested_end,
                "createdAt": datetime.now().isoformat(timespec="seconds"),
                "createdBy": created_by,
                "queuedForReview": bool(payload.get("queuedForReview")),
            })
            initial_result = {
                "schema": "weld-marker.topology.v1",
                "jobId": job_id,
                "status": "processing",
                "targetFile": target.name,
                "originalTargetName": original_target_name,
                "analyzedRange": [requested_start, requested_end],
                "pages": [],
                "completedPages": 0,
                "totalPages": total_pages,
                "progressStage": "queued",
                "completedReferenceFiles": 0,
                "totalReferenceFiles": len(references),
                "progressCompletedUnits": 0,
                "progressTotalUnits": max(1, len(references) + total_pages),
                "progressPercent": 15,
                "progressMessage": "任务已创建，等待进入解析队列",
                "numberingConfig": payload.get("numberingConfig") if isinstance(payload.get("numberingConfig"), dict) else {},
                "createdBy": created_by,
            }
            _atomic_dump(folder / "result.json", initial_result)
            store.create_job(
                job_id=job_id,
                analysis_signature=analysis_signature,
                job_folder=folder,
                original_target_name=original_target_name,
                algorithm_version=ANALYSIS_ALGORITHM_VERSION,
                creator_user_id=str(request_user["userId"]) if request_user else None,
                creator_username=str(request_user["username"]) if request_user else None,
            )
            registered = True
        except ActiveJobExists as exc:
            shutil.rmtree(folder, ignore_errors=True)
            active = _active_analysis(analysis_signature)
            if active:
                reused = dict(active)
                reused.update({"reusedExisting": True, "duplicateStatus": reused.get("status")})
                self._json(HTTPStatus.OK, reused)
                return
            raise ApiError(f"相同任务已在队列中：{exc.job_id}", HTTPStatus.CONFLICT) from exc
        except Exception:
            _console(f"[智能编号] 任务 {job_id} 失败")
            if registered:
                store.delete(job_id)
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self._json(HTTPStatus.ACCEPTED, initial_result)

    def _cancel_job(self, job_id: str) -> None:
        folder = _job_folder(job_id)
        result_path = folder / "result.json"
        store = _job_store()
        store.import_existing(DATA_ROOT, ANALYSIS_ALGORITHM_VERSION)
        record = store.get(job_id)
        if not result_path.is_file() or not record:
            raise ApiError("任务不存在", HTTPStatus.NOT_FOUND)
        with LOCK:
            current = json.loads(result_path.read_text(encoding="utf-8"))
            if current.get("status") in {"complete", "failed", "cancelled"}:
                self._json(HTTPStatus.OK, {"cancelled": current.get("status") == "cancelled", "status": current.get("status")})
                return
            try:
                status = store.request_cancel(job_id)
            except KeyError as exc:
                raise ApiError("任务不存在", HTTPStatus.NOT_FOUND) from exc
            if status in {"complete", "failed", "cancelled"} and status != "cancelled":
                self._json(HTTPStatus.OK, {"cancelled": False, "status": status})
                return
            if status == "cancelling":
                current.update({"status": "cancelling", "progressMessage": "正在取消解析任务"})
            else:
                current.update({"status": "cancelled", "progressMessage": "解析任务已取消"})
            _atomic_dump(result_path, current)
        self._json(HTTPStatus.OK, {"cancelled": True, "status": current["status"]})

    def _export_job(self, job_id: str) -> None:
        folder = _job_folder(job_id)
        meta_path = folder / "job.json"
        if not meta_path.exists():
            raise ApiError("任务不存在", HTTPStatus.NOT_FOUND)
        payload = self._read_json()
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise ApiError("缺少焊口数据")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        _, embedded_result = _require_complete_job(folder)
        grouped: dict[int, list[dict[str, Any]]] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ApiError("候选对象格式无效")
            grouped.setdefault(int(candidate.get("page") or 1), []).append(candidate)
        _validated_pages(
            [{"page": page_number, "candidates": items} for page_number, items in grouped.items()],
            embedded_result,
        )
        for page in embedded_result.get("pages", []):
            page_number = int(page.get("page") or 0)
            if page_number in grouped:
                page["candidates"] = grouped[page_number]
                page["candidateCount"] = len(grouped[page_number])
        weld_style: dict[str, Any] = {}
        component_styles: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            style = candidate.get("markerStyle")
            if not isinstance(style, dict) or not style:
                continue
            component_type = str(candidate.get("componentType") or "").lower()
            if component_type in {"valve", "flange", "support"}:
                component_styles.setdefault(component_type, style)
            elif not weld_style:
                weld_style = style
        embedded_result.update({
            "schema": "weld-marker.editable.v1",
            "jobId": job_id,
            "status": "complete",
            "markerStyle": weld_style,
            "componentMarkerStyles": component_styles,
            "markerStyles": {"weld": weld_style, "components": component_styles},
            "sourceFileName": meta.get("originalTargetName") or meta.get("targetFile") or "source.pdf",
        })
        output = write_annotated_pdf(folder / meta["targetFile"], folder / "annotated.pdf", candidates, embedded_result)
        self._json(HTTPStatus.OK, {"downloadUrl": f"/api/jobs/{job_id}/annotated.pdf", "size": output.stat().st_size})

    def _export_tutorial(self) -> None:
        payload = self._read_json()
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise ApiError("缺少焊口数据")
        embedded_result = _create_tutorial_session()
        grouped: dict[int, list[dict[str, Any]]] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ApiError("候选对象格式无效")
            grouped.setdefault(int(candidate.get("page") or 1), []).append(candidate)
        _validated_pages(
            [{"page": page_number, "candidates": items} for page_number, items in grouped.items()],
            embedded_result,
        )
        for page in embedded_result.get("pages", []):
            page_number = int(page.get("page") or 0)
            if page_number in grouped:
                page["candidates"] = grouped[page_number]
                page["candidateCount"] = len(grouped[page_number])
        weld_style: dict[str, Any] = {}
        component_styles: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            style = candidate.get("markerStyle")
            if not isinstance(style, dict) or not style:
                continue
            component_type = str(candidate.get("componentType") or "").lower()
            if component_type in {"valve", "flange", "support"}:
                component_styles.setdefault(component_type, style)
            elif not weld_style:
                weld_style = style
        embedded_result.update({
            "markerStyle": weld_style,
            "componentMarkerStyles": component_styles,
            "markerStyles": {"weld": weld_style, "components": component_styles},
            "sourceFileName": "000207.pdf",
        })
        export_id = uuid.uuid4().hex
        TUTORIAL_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
        output = write_annotated_pdf(
            TUTORIAL_ROOT / "000207.pdf",
            TUTORIAL_EXPORT_ROOT / f"{export_id}.pdf",
            candidates,
            embedded_result,
        )
        self._json(HTTPStatus.OK, {
            "downloadUrl": f"/api/tutorial/exports/{export_id}.pdf",
            "size": output.stat().st_size,
        })

    def _training_sample(self, job_id: str) -> None:
        folder = _job_folder(job_id)
        _require_complete_job(folder)
        output, candidate_count = _write_training_sample(job_id, folder, confirmed_by_human=True)
        self._json(HTTPStatus.OK, {"downloadUrl": f"/api/jobs/{job_id}/training-sample.zip", "candidateCount": candidate_count, "size": output.stat().st_size})


def main() -> None:
    parser = argparse.ArgumentParser(description="图纸标识识别系统 Python API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    reconciled = _reconcile_interrupted_jobs()
    removed = _cleanup_expired_jobs()
    removed_uploads = _cleanup_expired_uploads()
    if reconciled:
        _console(f"已将 {reconciled} 个历史任务登记到持久队列")
    if removed:
        _console(f"已清理 {removed} 个超过 {JOB_RETENTION_DAYS} 天的历史任务")
    if removed_uploads:
        _console(f"已清理 {removed_uploads} 个过期临时上传")
    server = ThreadingHTTPServer((args.host, args.port), ApiHandler)
    _console(f"图纸标识识别系统 API 已启动：http://{args.host}:{args.port}")
    _console(f"MinerU 服务地址：{MINERU_BASE_URL}")
    server.serve_forever()


if __name__ == "__main__":
    main()
