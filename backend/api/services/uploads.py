"""Upload staging, validation, and legacy Base64 compatibility services."""

from __future__ import annotations

import base64
from datetime import datetime
from http import HTTPStatus
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable
from urllib.parse import unquote
import uuid

from ..exceptions import ApiError


JOB_ID = re.compile(r"^[a-f0-9]{32}$")


def safe_name(value: str, fallback: str) -> str:
    cleaned = Path(str(value or "")).name.strip()
    return cleaned or fallback


def decode_file(payload: dict[str, Any] | None, folder: Path, fallback: str) -> Path | None:
    if not payload:
        return None
    encoded = payload.get("dataBase64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError(f"{fallback} 缺少文件数据")
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError(f"{fallback} 不是有效的 Base64 文件") from exc
    name = safe_name(payload.get("name"), fallback)
    suffix = Path(name).suffix.lower() or Path(fallback).suffix
    path = folder / f"{Path(fallback).stem}{suffix}"
    path.write_bytes(data)
    return path


def decode_files(payloads: Any, folder: Path, prefix: str, default_suffix: str = ".pdf") -> list[Path]:
    if payloads is None:
        return []
    if not isinstance(payloads, list):
        raise ValueError(f"{prefix}文件列表格式无效")
    result = []
    for index, payload in enumerate(payloads, start=1):
        path = decode_file(payload, folder, f"{prefix}-{index:03d}{default_suffix}")
        if path:
            original_name = safe_name(payload.get("name") if isinstance(payload, dict) else "", path.name)
            destination = folder / f"{prefix}-{index:03d}__{original_name}"
            path.replace(destination)
            result.append(destination)
    return result


def upload_record(upload_root: Path, upload_id: str) -> tuple[Path, dict[str, Any]]:
    if not JOB_ID.fullmatch(str(upload_id or "")):
        raise ApiError("上传编号无效")
    folder = (upload_root / upload_id).resolve()
    if folder.parent != upload_root.resolve():
        raise ApiError("上传路径无效")
    payload_path, meta_path = folder / "payload.bin", folder / "upload.json"
    if not payload_path.is_file() or not meta_path.is_file():
        raise ApiError("临时上传不存在或已过期", HTTPStatus.GONE)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError("临时上传记录损坏", HTTPStatus.GONE) from exc
    return payload_path, meta


def store_upload(
    source: Any,
    length: int,
    original_name: str,
    *,
    upload_root: Path,
    max_upload_bytes: int,
    min_free_disk_bytes: int,
    dump_json: Callable[[Path, dict[str, Any]], None],
) -> dict[str, Any]:
    if length <= 0:
        raise ApiError("上传文件为空")
    if length > max_upload_bytes:
        raise ApiError(
            f"单个上传文件超过 {max_upload_bytes // 1024 // 1024}MB 限制",
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )
    upload_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(upload_root).free - length < min_free_disk_bytes:
        raise ApiError("服务器可用磁盘空间不足，已拒绝上传", HTTPStatus.INSUFFICIENT_STORAGE)
    upload_id = uuid.uuid4().hex
    folder = upload_root / upload_id
    folder.mkdir()
    payload_path = folder / "payload.bin"
    digest, remaining = hashlib.sha256(), length
    try:
        with payload_path.open("wb") as target:
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ApiError("上传连接提前中断")
                target.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        meta = {
            "uploadId": upload_id,
            "name": safe_name(unquote(original_name), "upload.bin"),
            "size": length,
            "sha256": digest.hexdigest(),
            "createdAt": datetime.now().isoformat(timespec="seconds"),
        }
        dump_json(folder / "upload.json", meta)
        return meta
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def consume_upload(upload_root: Path, payload: Any, folder: Path, fallback: str) -> Path | None:
    if not payload:
        return None
    if not isinstance(payload, dict):
        raise ApiError(f"{fallback} 上传引用无效")
    source, meta = upload_record(upload_root, str(payload.get("uploadId") or ""))
    name = safe_name(payload.get("name") or meta.get("name"), fallback)
    suffix = Path(name).suffix.lower() or Path(fallback).suffix
    destination = folder / f"{Path(fallback).stem}{suffix}"
    shutil.move(str(source), destination)
    shutil.rmtree(source.parent, ignore_errors=True)
    return destination


def consume_uploads(
    upload_root: Path,
    payloads: Any,
    folder: Path,
    prefix: str,
    default_suffix: str = ".pdf",
) -> list[Path]:
    if payloads is None:
        return []
    if not isinstance(payloads, list):
        raise ApiError(f"{prefix}上传列表格式无效")
    result = []
    for index, payload in enumerate(payloads, start=1):
        if not isinstance(payload, dict):
            raise ApiError(f"{prefix}上传引用无效")
        source, meta = upload_record(upload_root, str(payload.get("uploadId") or ""))
        original_name = safe_name(payload.get("name") or meta.get("name"), f"{prefix}-{index:03d}{default_suffix}")
        destination = folder / f"{prefix}-{index:03d}__{original_name}"
        shutil.move(str(source), destination)
        shutil.rmtree(source.parent, ignore_errors=True)
        result.append(destination)
    return result
