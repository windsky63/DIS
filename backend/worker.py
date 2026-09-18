"""Durable analysis worker process."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import threading
import time
import traceback
import uuid

try:
    from .job_runner import atomic_dump, run_job
    from .job_store import JobStore
except ImportError:  # Direct ``python backend/worker.py`` execution.
    from job_runner import atomic_dump, run_job
    from job_store import JobStore


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "jobs"
DATABASE_PATH = DATA_ROOT / ".queue" / "jobs.db"


def _console(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def _sync_recovered_results(store: JobStore, recovered: dict[str, list[str]]) -> None:
    updates = {
        "requeued": {"status": "processing", "progressStage": "queued", "progressMessage": "Worker中断，任务已自动重新排队"},
        "failed": {"status": "failed", "progressStage": "failed", "progressMessage": "Worker多次中断，任务已停止重试"},
        "cancelled": {"status": "cancelled", "progressStage": "cancelled", "progressMessage": "任务已取消"},
    }
    for group, job_ids in recovered.items():
        for job_id in job_ids:
            row = store.get(job_id)
            result_path = Path(str(row["job_folder"])) / "result.json" if row else None
            if not result_path or not result_path.is_file():
                continue
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result.update(updates[group])
                atomic_dump(result_path, result)
            except (OSError, json.JSONDecodeError):
                _console(f"无法同步恢复任务结果：{job_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="图纸标识识别系统分析 Worker")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--worker-id", default="")
    args = parser.parse_args()
    worker_id = args.worker_id or f"{os.environ.get('HOSTNAME', 'worker')}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    store = JobStore(DATABASE_PATH)
    store.initialize()

    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopping.set()
        _console("收到停止信号；当前任务结束后退出，不再领取新任务")

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    _console(f"Worker已启动：{worker_id}")

    try:
        while not stopping.is_set():
            store.touch_worker(worker_id)
            recovered = store.requeue_expired()
            if any(recovered.values()):
                _sync_recovered_results(store, recovered)
                _console(
                    f"租约恢复：重新排队 {len(recovered['requeued'])}，"
                    f"失败 {len(recovered['failed'])}，取消 {len(recovered['cancelled'])}"
                )
            row = store.claim_next(worker_id, max(15, args.lease_seconds))
            if row:
                try:
                    run_job(store, row, worker_id, lease_seconds=max(15, args.lease_seconds), log=_console)
                except Exception as exc:
                    traceback.print_exc()
                    store.finish(str(row["job_id"]), "failed", str(exc), worker_id=worker_id)
                    _console(f"任务 {row['job_id']} 的文件或元数据损坏：{exc}")
                continue
            stopping.wait(max(0.1, args.poll_seconds))
    finally:
        store.remove_worker(worker_id)
        _console("Worker已停止")


if __name__ == "__main__":
    main()
