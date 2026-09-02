"""Small local API for the topology-first weld marker.

No web framework is used: this is a local, single-user engineering tool and the
standard library keeps deployment and auditing straightforward.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta
import hashlib
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import threading
import traceback
from typing import Any
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen
import uuid
import zipfile

import fitz

from engine import analyze_documents, dump_result, render_page, write_annotated_pdf
from iso_weld_matcher.idf_topology import parser_availability


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "jobs"
FRONTEND_DIST = ROOT / "frontend" / "dist"
PCF_LIBRARY_ROOT = ROOT / "backend" / "PCF"
AUDIT_ROOT = ROOT / "data" / "audit"
MAX_BODY_BYTES = int(os.environ.get("WELD_MARKER_MAX_BODY_MB", "350")) * 1024 * 1024
MAX_PENDING_ANALYSES = max(1, int(os.environ.get("WELD_MARKER_MAX_PENDING_ANALYSES", "4")))
MAX_CONCURRENT_ANALYSES = max(1, int(os.environ.get("WELD_MARKER_MAX_CONCURRENT_ANALYSES", "2")))
JOB_RETENTION_DAYS = max(1, int(os.environ.get("WELD_MARKER_JOB_RETENTION_DAYS", "90")))
AUDIT_MAX_BYTES = max(1024 * 1024, int(os.environ.get("WELD_MARKER_AUDIT_MAX_MB", "10")) * 1024 * 1024)
AUDIT_BACKUP_COUNT = max(1, int(os.environ.get("WELD_MARKER_AUDIT_BACKUPS", "5")))
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
LOCK = threading.RLock()
ANALYSIS_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_ANALYSES)
JOB_CANCEL_EVENTS: dict[str, threading.Event] = {}
ACTIVE_ANALYSIS_SIGNATURES: dict[str, str] = {}
MINERU_BASE_URL = os.environ.get("MINERU_BASE_URL", "http://192.168.32.61:8081").rstrip("/")
# Increment whenever recognition semantics change so completed results from an
# older engine are not silently reused for the same files and configuration.
ANALYSIS_ALGORITHM_VERSION = "2026-09-02.3"


class JobCancelled(RuntimeError):
    pass


class ApiError(ValueError):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = int(status)


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


def _reconcile_interrupted_jobs() -> int:
    """Mark daemon-backed jobs interrupted by a previous process exit as failed."""

    reconciled = 0
    if not DATA_ROOT.is_dir():
        return reconciled
    for result_path in DATA_ROOT.glob("*/result.json"):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("status") not in {"processing", "cancelling"}:
                continue
            result.update({
                "status": "failed",
                "error": "服务曾在任务完成前停止，请重新发起分析",
                "progressMessage": "任务因服务重启而中断",
                "interruptedAt": datetime.now().isoformat(timespec="seconds"),
            })
            _atomic_dump(result_path, result)
            reconciled += 1
        except (OSError, json.JSONDecodeError, ValueError):
            _console(f"无法恢复任务状态：{result_path}")
    return reconciled


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
        if modified >= cutoff or _job_status(folder) not in {"complete", "failed", "cancelled"}:
            continue
        shutil.rmtree(folder)
        removed += 1
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


def _safe_name(value: str, fallback: str) -> str:
    cleaned = Path(str(value or "")).name.strip()
    return cleaned or fallback


def _decode_file(payload: dict[str, Any] | None, folder: Path, fallback: str) -> Path | None:
    if not payload:
        return None
    encoded = payload.get("dataBase64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError(f"{fallback} 缺少文件数据")
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError(f"{fallback} 不是有效的 Base64 文件") from exc
    name = _safe_name(payload.get("name"), fallback)
    suffix = Path(name).suffix.lower() or Path(fallback).suffix
    path = folder / f"{Path(fallback).stem}{suffix}"
    path.write_bytes(data)
    return path


def _decode_files(payloads: Any, folder: Path, prefix: str, default_suffix: str = ".pdf") -> list[Path]:
    if payloads is None:
        return []
    if not isinstance(payloads, list):
        raise ValueError(f"{prefix}文件列表格式无效")
    result = []
    for index, payload in enumerate(payloads, start=1):
        path = _decode_file(payload, folder, f"{prefix}-{index:03d}{default_suffix}")
        if path:
            original_name = _safe_name(payload.get("name") if isinstance(payload, dict) else "", path.name)
            destination = folder / f"{prefix}-{index:03d}__{original_name}"
            path.replace(destination)
            result.append(destination)
    return result


def _job_folder(job_id: str) -> Path:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("任务编号无效")
    folder = (DATA_ROOT / job_id).resolve()
    if folder.parent != DATA_ROOT.resolve():
        raise ValueError("任务路径无效")
    return folder


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
        "target": _payload_file_signature(payload.get("targetPdf")),
        "referencePdfs": [_payload_file_signature(item) for item in (payload.get("referencePdfs") or [])],
        "referencePdf": _payload_file_signature(payload.get("referencePdf")),
        "pcfFiles": [_payload_file_signature(item) for item in (payload.get("pcfFiles") or [])],
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
        status = result.get("status")
        if status == "processing" and job_id not in JOB_CANCEL_EVENTS:
            continue
        if status not in {"processing", "complete"}:
            continue
        records.append({"jobId": job_id, "result": result, "modified": result_path.stat().st_mtime})
    if not records:
        return None
    records.sort(key=lambda item: (item["result"].get("status") == "processing", item["modified"]), reverse=True)
    return records[0]


def _atomic_dump(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    dump_result(temporary, payload)
    temporary.replace(path)


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
        restored = dict(item["result"])
        restored["originalTargetName"] = item["meta"].get("originalTargetName") or restored.get("originalTargetName")
        jobs.append(restored)
    return {"batchId": batch_id, "jobs": jobs}


def _update_job_result(folder: Path, update: dict[str, Any]) -> dict[str, Any]:
    result_path = folder / "result.json"
    with LOCK:
        current = json.loads(result_path.read_text(encoding="utf-8"))
        current.update(update)
        _atomic_dump(result_path, current)
        return current


def _active_analysis(signature: str) -> dict[str, Any] | None:
    job_id = ACTIVE_ANALYSIS_SIGNATURES.get(signature)
    if not job_id:
        return None
    result_path = _job_folder(job_id) / "result.json"
    if not result_path.is_file():
        return {"jobId": job_id, "status": "processing", "pages": [], "progressMessage": "相同任务正在初始化"}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"jobId": job_id, "status": "processing", "pages": [], "progressMessage": "相同任务正在初始化"}
    if result.get("status") in {"processing", "cancelling"}:
        return result
    ACTIVE_ANALYSIS_SIGNATURES.pop(signature, None)
    return None


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


def _run_analysis_job(
    job_id: str,
    folder: Path,
    target: Path,
    references: list[Path],
    pcfs: list[Path],
    symbol_config: dict[str, Any] | None,
    start_page: int,
    end_page: int | None,
    analysis_signature: str,
) -> None:
    slot_acquired = False
    queue_announced = False

    def ensure_not_cancelled() -> None:
        cancel_event = JOB_CANCEL_EVENTS.get(job_id)
        if cancel_event and cancel_event.is_set():
            raise JobCancelled("用户取消了解析任务")

    try:
        while not ANALYSIS_SLOTS.acquire(timeout=0.25):
            ensure_not_cancelled()
            if not queue_announced:
                _update_job_result(folder, {"progressStage": "queued", "progressMessage": "正在等待可用的分析资源"})
                queue_announced = True
        slot_acquired = True
        ensure_not_cancelled()
        initial_state = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        total_pages = max(0, int(initial_state.get("totalPages") or 0))
        total_reference_files = len(references)
        progress_total_units = max(1, total_reference_files + total_pages)
        completed_reference_files = 0
        _console(
            f"[智能编号] 开始研究 {target.name}；"
            f"对照 PDF {len(references)} 份，PCF {len(pcfs)} 份"
        )

        def report_progress(message: str) -> None:
            nonlocal completed_reference_files
            ensure_not_cancelled()
            _console(f"[智能编号] {message}")
            update: dict[str, Any] = {"progressMessage": message}
            reference_match = re.search(r"对照 PDF (\d+)/(\d+) 解析完成", message)
            if reference_match:
                completed_reference_files = max(completed_reference_files, int(reference_match.group(1)))
                update.update({
                    "progressStage": "reference",
                    "completedReferenceFiles": completed_reference_files,
                    "totalReferenceFiles": total_reference_files,
                    "progressCompletedUnits": completed_reference_files,
                    "progressTotalUnits": progress_total_units,
                })
            elif message.startswith("研究设计图页面"):
                completed_reference_files = total_reference_files
                update.update({
                    "progressStage": "design",
                    "completedReferenceFiles": completed_reference_files,
                    "totalReferenceFiles": total_reference_files,
                    "progressCompletedUnits": completed_reference_files,
                    "progressTotalUnits": progress_total_units,
                })
            _update_job_result(folder, update)

        def publish_page(page: dict[str, Any]) -> None:
            ensure_not_cancelled()
            result_path = folder / "result.json"
            with LOCK:
                current = json.loads(result_path.read_text(encoding="utf-8"))
                pages = [
                    existing for existing in current.get("pages", [])
                    if int(existing.get("page") or 0) != int(page.get("page") or 0)
                ]
                pages.append(page)
                pages.sort(key=lambda item: int(item.get("page") or 0))
                current.update({
                    "pages": pages,
                    "completedPages": len(pages),
                    "progressStage": "design",
                    "completedReferenceFiles": total_reference_files,
                    "totalReferenceFiles": total_reference_files,
                    "progressCompletedUnits": total_reference_files + len(pages),
                    "progressTotalUnits": progress_total_units,
                    "progressMessage": f"第 {page.get('page')} 页分析完成，已同步到前端",
                })
                _atomic_dump(result_path, current)

        result = analyze_documents(
            target,
            reference_pdfs=references,
            pcf_files=pcfs,
            symbol_config=symbol_config,
            start_page=start_page,
            end_page=end_page,
            progress_callback=report_progress,
            page_callback=publish_page,
        )
        ensure_not_cancelled()
        result.update({
            "jobId": job_id,
            "status": "processing",
            "completedPages": len(result.get("pages", [])),
            "progressStage": "training-sample",
            "completedReferenceFiles": total_reference_files,
            "totalReferenceFiles": total_reference_files,
            "progressCompletedUnits": progress_total_units,
            "progressTotalUnits": progress_total_units,
            "progressMessage": "页面解析完成，正在自动生成 AI 训练样本",
            "revision": 0,
        })
        with LOCK:
            _atomic_dump(folder / "result.json", result)
        training_error = ""
        try:
            training_output, training_count = _write_training_sample(job_id, folder)
            result["autoTrainingSample"] = {
                "status": "complete",
                "file": training_output.name,
                "candidateCount": training_count,
                "size": training_output.stat().st_size,
            }
        except Exception as exc:
            training_error = str(exc)
            result["autoTrainingSample"] = {"status": "failed", "error": training_error}
            _console(f"[智能编号] 自动训练样本生成失败：{exc}")
        result.update({
            "status": "complete",
            "progressStage": "complete",
            "progressMessage": "全部页面分析与训练样本导出完成" if not training_error else "页面分析完成，训练样本导出失败",
        })
        with LOCK:
            _atomic_dump(folder / "result.json", result)
        candidate_count = sum(len(page.get("candidates", [])) for page in result.get("pages", []))
        _console(f"[智能编号] 任务 {job_id} 完成，共识别 {candidate_count} 个候选焊口")
    except JobCancelled:
        _console(f"[智能编号] 任务 {job_id} 已取消")
        try:
            _update_job_result(folder, {"status": "cancelled", "progressMessage": "解析任务已取消"})
        except Exception:
            traceback.print_exc()
    except Exception as exc:
        traceback.print_exc()
        _console(f"[智能编号] 任务 {job_id} 失败：{exc}")
        try:
            _update_job_result(folder, {
                "status": "failed",
                "error": str(exc),
                "progressMessage": "分析失败",
            })
        except Exception:
            traceback.print_exc()
    finally:
        if slot_acquired:
            ANALYSIS_SLOTS.release()
        with LOCK:
            JOB_CANCEL_EVENTS.pop(job_id, None)
            if ACTIVE_ANALYSIS_SIGNATURES.get(analysis_signature) == job_id:
                ACTIVE_ANALYSIS_SIGNATURES.pop(analysis_signature, None)


class ApiHandler(SimpleHTTPRequestHandler):
    server_version = "WeldMarker/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        if args and "GET /api/health " in str(args[0]):
            return
        _console(fmt % args)

    def end_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in {"http://127.0.0.1:3004", "http://localhost:3004"}:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ApiError("Content-Length 无效") from exc
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ApiError(f"上传内容超过 {MAX_BODY_BYTES // 1024 // 1024}MB 限制", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("请求内容不是有效的 JSON") from exc
        if not isinstance(payload, dict):
            raise ApiError("JSON 请求主体必须是对象")
        return payload

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, ApiError):
            self._json(exc.status, {"error": str(exc)})
            return
        if isinstance(exc, (ValueError, TypeError, KeyError, json.JSONDecodeError)):
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        error_id = uuid.uuid4().hex[:12]
        _console(f"请求处理失败 [{error_id}]：{exc}")
        traceback.print_exc()
        self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"服务器内部错误，错误编号：{error_id}"})

    def _file(self, path: Path, download_name: str | None = None) -> None:
        if not path.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "文件不存在"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        if download_name:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download_name)}")
        self.end_headers()
        with path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile)

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._do_GET()
        except Exception as exc:
            self._handle_exception(exc)

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self._json(HTTPStatus.OK, {
                "status": "ready",
                "engine": "PDF轴测图图元研究",
                "architecture": "vue3+python-stdlib-api",
                "mineru": _mineru_health(),
                "pcfParsers": parser_availability(),
            })
            return
        if path == "/api/pcf-folders":
            self._json(HTTPStatus.OK, {"folders": _pcf_library_folders()})
            return
        if path == "/api/jobs/recent-batch":
            self._json(HTTPStatus.OK, _recent_batch_results())
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path)
        if match:
            job_id = match.group(1)
            result_path = _job_folder(job_id) / "result.json"
            if not result_path.is_file():
                with LOCK:
                    initializing = job_id in JOB_CANCEL_EVENTS
                if initializing:
                    self._json(HTTPStatus.OK, {
                        "jobId": job_id,
                        "status": "processing",
                        "pages": [],
                        "progressStage": "initializing",
                        "progressMessage": "任务正在接收并校验输入文件",
                    })
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "任务不存在"})
                return
            self._json(HTTPStatus.OK, json.loads(result_path.read_text(encoding="utf-8")))
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
        self._file(candidate)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/jobs":
                self._create_job()
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
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/cancel", parsed.path)
            if match:
                self._cancel_job(match.group(1))
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

    def do_PUT(self) -> None:  # noqa: N802
        match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})", urlparse(self.path).path)
        if not match:
            self._json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        try:
            folder = _job_folder(match.group(1))
            payload = self._read_json()
            with LOCK:
                result_path, current = _require_complete_job(folder)
                expected_revision = payload.get("baseRevision")
                current_revision = int(current.get("revision") or 0)
                if expected_revision is not None and int(expected_revision) != current_revision:
                    raise ApiError("结果已被其他窗口修改，请刷新后重试", HTTPStatus.CONFLICT)
                current["pages"] = _validated_pages(payload.get("pages", current.get("pages", [])), current)
                for key in ("markerStyle", "componentMarkerStyles", "markerStyles"):
                    if isinstance(payload.get(key), dict):
                        current[key] = payload[key]
                current["revision"] = current_revision + 1
                current["updatedAt"] = datetime.now().isoformat(timespec="seconds")
                _atomic_dump(result_path, current)
            self._json(HTTPStatus.OK, {"saved": True, "revision": current["revision"], "updatedAt": current["updatedAt"]})
        except Exception as exc:
            self._handle_exception(exc)

    def _create_job(self) -> None:
        payload = self._read_json()
        analysis_signature = _analysis_signature(payload)
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
            if len(JOB_CANCEL_EVENTS) >= MAX_PENDING_ANALYSES:
                raise ApiError("当前等待或执行中的分析任务过多，请稍后重试", HTTPStatus.TOO_MANY_REQUESTS)
            job_id = uuid.uuid4().hex
            folder = DATA_ROOT / job_id
            folder.mkdir(parents=True, exist_ok=False)
            ACTIVE_ANALYSIS_SIGNATURES[analysis_signature] = job_id
            JOB_CANCEL_EVENTS[job_id] = threading.Event()
        _console(f"[智能编号] 收到任务 {job_id}")
        try:
            target = _decode_file(payload.get("targetPdf"), folder, "target.pdf")
            if target is None or target.suffix.lower() != ".pdf":
                raise ValueError("必须上传待标识 PDF")
            references = _decode_files(payload.get("referencePdfs"), folder, "reference")
            legacy_reference = _decode_file(payload.get("referencePdf"), folder, "reference-legacy.pdf")
            if legacy_reference:
                references.append(legacy_reference)
            if any(reference.suffix.lower() != ".pdf" for reference in references):
                raise ValueError("对照图必须全部是 PDF")
            pcfs = _decode_files(payload.get("pcfFiles"), folder, "source", ".pcf")
            legacy_pcf = _decode_file(payload.get("pcfFile"), folder, "source-legacy.pcf")
            if legacy_pcf:
                pcfs.append(legacy_pcf)
            pcfs.extend(_resolve_pcf_library_folder(payload.get("pcfFolder")))
            if any(pcf.suffix.lower() != ".pcf" for pcf in pcfs):
                raise ValueError("管道源文件必须全部是 PCF")
            requested_start = int(payload.get("startPage") or 1)
            requested_end = int(payload["endPage"]) if payload.get("endPage") else None
            with fitz.open(target) as target_document:
                resolved_start = max(1, requested_start)
                resolved_end = min(target_document.page_count, requested_end or target_document.page_count)
                total_pages = max(0, resolved_end - resolved_start + 1)
            target_payload = payload.get("targetPdf") if isinstance(payload.get("targetPdf"), dict) else {}
            original_target_name = _safe_name(payload.get("originalTargetName") or target_payload.get("name"), target.name)
            (folder / "job.json").write_text(
                json.dumps({
                    "targetFile": target.name,
                    "originalTargetName": original_target_name,
                    "referenceFiles": [item.name for item in references],
                    "pcfFolder": payload.get("pcfFolder"),
                    "batchId": payload.get("batchId"),
                    "batchIndex": int(payload.get("batchIndex") or 0),
                    "analysisSignature": analysis_signature,
                }, ensure_ascii=False), encoding="utf-8"
            )
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
                "progressStage": "reference" if references else "design",
                "completedReferenceFiles": 0,
                "totalReferenceFiles": len(references),
                "progressCompletedUnits": 0,
                "progressTotalUnits": max(1, len(references) + total_pages),
                "progressMessage": "任务已创建，正在准备图元研究",
            }
            with LOCK:
                _atomic_dump(folder / "result.json", initial_result)
            worker = threading.Thread(
                target=_run_analysis_job,
                args=(
                    job_id, folder, target, references, pcfs,
                    payload.get("symbolConfig"), requested_start, requested_end,
                    analysis_signature,
                ),
                name=f"weld-analysis-{job_id[:8]}",
                daemon=True,
            )
            worker.start()
        except Exception:
            _console(f"[智能编号] 任务 {job_id} 失败")
            with LOCK:
                JOB_CANCEL_EVENTS.pop(job_id, None)
                if ACTIVE_ANALYSIS_SIGNATURES.get(analysis_signature) == job_id:
                    ACTIVE_ANALYSIS_SIGNATURES.pop(analysis_signature, None)
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self._json(HTTPStatus.ACCEPTED, initial_result)

    def _cancel_job(self, job_id: str) -> None:
        folder = _job_folder(job_id)
        result_path = folder / "result.json"
        if not result_path.is_file():
            with LOCK:
                cancel_event = JOB_CANCEL_EVENTS.get(job_id)
                if cancel_event:
                    cancel_event.set()
                    self._json(HTTPStatus.OK, {"cancelled": True, "status": "cancelling"})
                    return
            raise ApiError("任务不存在", HTTPStatus.NOT_FOUND)
        with LOCK:
            current = json.loads(result_path.read_text(encoding="utf-8"))
            if current.get("status") in {"complete", "failed", "cancelled"}:
                self._json(HTTPStatus.OK, {"cancelled": current.get("status") == "cancelled", "status": current.get("status")})
                return
            cancel_event = JOB_CANCEL_EVENTS.get(job_id)
            if cancel_event:
                cancel_event.set()
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
        })
        output = write_annotated_pdf(folder / meta["targetFile"], folder / "annotated.pdf", candidates, embedded_result)
        self._json(HTTPStatus.OK, {"downloadUrl": f"/api/jobs/{job_id}/annotated.pdf", "size": output.stat().st_size})

    def _training_sample(self, job_id: str) -> None:
        folder = _job_folder(job_id)
        _require_complete_job(folder)
        output, candidate_count = _write_training_sample(job_id, folder, confirmed_by_human=True)
        self._json(HTTPStatus.OK, {"downloadUrl": f"/api/jobs/{job_id}/training-sample.zip", "candidateCount": candidate_count, "size": output.stat().st_size})


def main() -> None:
    parser = argparse.ArgumentParser(description="图纸拓扑标识系统 Python API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    reconciled = _reconcile_interrupted_jobs()
    removed = _cleanup_expired_jobs()
    if reconciled:
        _console(f"已将 {reconciled} 个服务重启前未完成的任务标记为失败")
    if removed:
        _console(f"已清理 {removed} 个超过 {JOB_RETENTION_DAYS} 天的历史任务")
    server = ThreadingHTTPServer((args.host, args.port), ApiHandler)
    _console(f"图纸拓扑标识 API 已启动：http://{args.host}:{args.port}")
    _console(f"MinerU 服务地址：{MINERU_BASE_URL}")
    server.serve_forever()


if __name__ == "__main__":
    main()
