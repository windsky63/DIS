"""Execute one durable analysis job outside the HTTP service process."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading
import time
import traceback
from typing import Any, Callable
import uuid
import zipfile

try:
    from .engine import analyze_documents, dump_result, render_page
    from .job_store import JobStore
    from .label_layout import optimize_result_label_positions
    from .progress import analysis_progress_percent
except ImportError:  # Direct ``python backend/worker.py`` execution.
    from engine import analyze_documents, dump_result, render_page
    from job_store import JobStore
    from label_layout import optimize_result_label_positions
    from progress import analysis_progress_percent


class JobCancelled(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


def atomic_dump(path: Path, payload: dict[str, Any]) -> None:
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
            pass


def update_result(
    folder: Path,
    update: dict[str, Any],
    *,
    store: JobStore | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    result_path = folder / "result.json"
    current = json.loads(result_path.read_text(encoding="utf-8"))
    current.update(update)
    atomic_dump(result_path, current)
    if store is not None and job_id is not None:
        store.update_queue_summary(job_id, current)
    return current


def write_training_sample(job_id: str, folder: Path) -> tuple[Path, int]:
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    meta = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    candidates = [
        {**candidate, "confirmedByHuman": False}
        for page in result.get("pages", [])
        for candidate in page.get("candidates", [])
        if candidate.get("included", True)
    ]
    image_paths = []
    for page_number in sorted({int(item.get("page") or 1) for item in candidates}):
        image = folder / f"training-page-{page_number}.png"
        if not image.exists():
            render_page(folder / meta["targetFile"], page_number, image, scale=2.0)
        image_paths.append((page_number, image))
    manifest = {
        "schema": "weld-marker.training.v1",
        "jobId": job_id,
        "createdAt": datetime.now().isoformat(),
        "exportMode": "post-analysis-auto",
        "candidateCount": len(candidates),
        "candidates": candidates,
    }
    output = folder / "training-sample.zip"
    temporary = folder / f".training-sample.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for page_number, image in image_paths:
                archive.write(image, f"pages/page-{page_number}.png")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output, len(candidates)


def _resolve_sources(folder: Path, meta: dict[str, Any]) -> tuple[Path, list[Path], list[Path]]:
    target = folder / Path(str(meta["targetFile"])).name
    references = [folder / Path(str(name)).name for name in (meta.get("referenceFiles") or [])]
    pcfs = []
    for item in meta.get("pcfSources") or []:
        if not isinstance(item, dict):
            continue
        if item.get("scope") == "job":
            pcfs.append(folder / Path(str(item.get("path") or "")).name)
        elif item.get("scope") == "absolute":
            pcfs.append(Path(str(item.get("path") or "")))
    return target, references, pcfs


def run_job(
    store: JobStore,
    row: dict[str, Any],
    worker_id: str,
    *,
    lease_seconds: int = 60,
    log: Callable[[str], None] = print,
) -> str:
    """Run a claimed job and publish its terminal state durably."""

    job_id = str(row["job_id"])
    folder = Path(str(row["job_folder"]))
    existing_result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    if existing_result.get("status") == "complete":
        store.update_queue_summary(job_id, existing_result)
        store.finish(job_id, "complete", worker_id=worker_id)
        log(f"[智能编号] 任务 {job_id} 的结果已完成，已修复队列终态")
        return "complete"
    meta = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    target, references, pcfs = _resolve_sources(folder, meta)
    heartbeat_stop = threading.Event()
    lease_lost = threading.Event()

    def heartbeat_loop() -> None:
        interval = max(2.0, lease_seconds / 3)
        while not heartbeat_stop.wait(interval):
            try:
                store.touch_worker(worker_id, job_id)
                if not store.heartbeat(job_id, worker_id, lease_seconds):
                    lease_lost.set()
                    return
            except Exception:
                traceback.print_exc()

    heartbeat = threading.Thread(target=heartbeat_loop, name=f"job-heartbeat-{job_id[:8]}", daemon=True)
    store.touch_worker(worker_id, job_id)
    heartbeat.start()

    def ensure_not_cancelled() -> None:
        if lease_lost.is_set():
            raise LeaseLost("Worker已失去任务租约")
        if store.cancellation_requested(job_id):
            raise JobCancelled("用户取消了解析任务")

    try:
        def publish_result(update: dict[str, Any]) -> dict[str, Any]:
            return update_result(folder, update, store=store, job_id=job_id)

        ensure_not_cancelled()
        initial_state = publish_result({
            "status": "processing",
            "progressStage": "starting",
            "progressPercent": 15,
            "progressMessage": "Worker已领取任务，正在启动解析",
        })
        store.clear_job_pages(job_id)
        total_pages = max(0, int(initial_state.get("totalPages") or 0))
        total_reference_files = len(references)
        progress_total_units = max(1, total_reference_files + total_pages)
        completed_reference_files = 0
        log(f"[智能编号] Worker {worker_id} 开始任务 {job_id}：{target.name}")

        def report_progress(message: str) -> None:
            nonlocal completed_reference_files
            ensure_not_cancelled()
            update: dict[str, Any] = {"progressMessage": message}
            reference_page_match = re.search(r"解析对照 PDF (\d+)/(\d+).*?页面 (\d+)/(\d+)", message)
            reference_match = re.search(r"对照 PDF (\d+)/(\d+) 解析完成", message)
            if reference_page_match:
                reference_index = max(1, int(reference_page_match.group(1)))
                reference_page = max(0, int(reference_page_match.group(3)))
                reference_page_total = max(1, int(reference_page_match.group(4)))
                completed_reference_files = max(completed_reference_files, reference_index - 1)
                update.update({
                    "progressStage": "reference",
                    "completedReferenceFiles": completed_reference_files,
                    "totalReferenceFiles": total_reference_files,
                    "progressCompletedUnits": round(reference_index - 1 + min(1.0, reference_page / reference_page_total), 4),
                    "progressTotalUnits": progress_total_units,
                })
            elif reference_match:
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
                    "completedReferenceFiles": total_reference_files,
                    "totalReferenceFiles": total_reference_files,
                    "progressCompletedUnits": total_reference_files,
                    "progressTotalUnits": progress_total_units,
                })
            if "progressCompletedUnits" in update:
                update["progressPercent"] = analysis_progress_percent(
                    update["progressCompletedUnits"], progress_total_units
                )
            publish_result(update)

        def publish_page(page: dict[str, Any]) -> None:
            ensure_not_cancelled()
            page.setdefault("reviewRevision", 0)
            completed_pages = store.publish_analysis_page(job_id, page)
            publish_result({
                "completedPages": completed_pages,
                "progressStage": "design",
                "completedReferenceFiles": total_reference_files,
                "totalReferenceFiles": total_reference_files,
                "progressCompletedUnits": total_reference_files + completed_pages,
                "progressTotalUnits": progress_total_units,
                "progressPercent": analysis_progress_percent(
                    total_reference_files + completed_pages, progress_total_units
                ),
                "progressMessage": f"第 {page.get('page')} 页分析完成，已同步到前端",
            })

        result = analyze_documents(
            target,
            reference_pdfs=references,
            pcf_files=pcfs,
            symbol_config=meta.get("symbolConfig"),
            start_page=int(meta.get("startPage") or 1),
            end_page=int(meta["endPage"]) if meta.get("endPage") else None,
            progress_callback=report_progress,
            page_callback=publish_page,
        )
        ensure_not_cancelled()
        publish_result({
            "progressStage": "optimizing-layout",
            "progressPercent": 95,
            "layoutCompletedPages": 0,
            "layoutTotalPages": len(result.get("pages") or []),
            "progressMessage": "页面解析完成，正在优化全部页面的标识位置",
        })
        def report_layout_progress(completed: int, total: int) -> None:
            ensure_not_cancelled()
            safe_total = max(1, total)
            publish_result({
                "progressStage": "optimizing-layout",
                "progressPercent": round(95 + 4 * min(1.0, completed / safe_total), 2),
                "layoutCompletedPages": completed,
                "layoutTotalPages": total,
                "progressMessage": f"正在优化标识位置：{completed} / {total} 页",
            })

        layout_totals = optimize_result_label_positions(
            result, progress_callback=report_layout_progress
        )
        log(
            f"[智能编号] 任务 {job_id} 已优化全部页面标识："
            f"放置 {layout_totals['placed']} 个，移动 {layout_totals['moved']} 个"
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
            "progressPercent": 99,
            "progressMessage": "页面解析完成，正在自动生成 AI 训练样本",
            "revision": 0,
            "project": meta.get("project") or initial_state.get("project") or {},
            "numberingConfig": initial_state.get("numberingConfig") or {},
            "createdBy": meta.get("createdBy") or initial_state.get("createdBy"),
        })
        for page in result.get("pages") or []:
            page.setdefault("reviewRevision", 0)
        atomic_dump(folder / "result.json", result)
        store.update_queue_summary(job_id, result)
        training_error = ""
        try:
            training_output, training_count = write_training_sample(job_id, folder)
            result["autoTrainingSample"] = {
                "status": "complete",
                "file": training_output.name,
                "candidateCount": training_count,
                "size": training_output.stat().st_size,
            }
        except Exception as exc:
            training_error = str(exc)
            result["autoTrainingSample"] = {"status": "failed", "error": training_error}
            log(f"[智能编号] 自动训练样本生成失败：{exc}")
        ensure_not_cancelled()
        result.update({
            "status": "complete",
            "progressStage": "complete",
            "progressPercent": 100,
            "progressMessage": "全部页面分析与训练样本导出完成" if not training_error else "页面分析完成，训练样本导出失败",
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        })
        store.replace_job_pages(job_id, list(result.get("pages") or []))
        result_metadata = dict(result)
        result_metadata.pop("pages", None)
        atomic_dump(folder / "result.json", result_metadata)
        store.update_queue_summary(job_id, result_metadata)
        if not store.finish(job_id, "complete", worker_id=worker_id):
            if store.cancellation_requested(job_id):
                raise JobCancelled("用户在任务完成提交前取消了解析任务")
            raise LeaseLost("完成任务时发现Worker已失去租约")
        log(f"[智能编号] Worker {worker_id} 完成任务 {job_id}")
        return "complete"
    except JobCancelled:
        publish_result({"status": "cancelled", "progressStage": "cancelled", "progressMessage": "解析任务已取消"})
        store.finish(job_id, "cancelled", worker_id=worker_id)
        log(f"[智能编号] Worker {worker_id} 已取消任务 {job_id}")
        return "cancelled"
    except LeaseLost as exc:
        log(f"[智能编号] Worker {worker_id} 停止写入任务 {job_id}：{exc}")
        return "lease-lost"
    except Exception as exc:
        traceback.print_exc()
        try:
            publish_result({"status": "failed", "error": str(exc), "progressStage": "failed", "progressMessage": "分析失败"})
        finally:
            store.finish(job_id, "failed", str(exc), worker_id=worker_id)
        log(f"[智能编号] Worker {worker_id} 任务 {job_id} 失败：{exc}")
        return "failed"
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=2)
