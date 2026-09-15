"""Page-scoped review merging and optimistic revision checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable


class PageRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("该页已被更新，请重新载入后再修改")
        self.current_revision = current_revision


def merge_review_page(
    current: dict[str, Any],
    page_number: int,
    incoming_page: dict[str, Any],
    base_revision: int,
    user: dict[str, object],
    *,
    now: Callable[[], datetime] = datetime.now,
) -> dict[str, Any]:
    pages = list(current.get("pages") or [])
    index = next((i for i, page in enumerate(pages) if int(page.get("page") or 0) == int(page_number)), None)
    if index is None:
        raise KeyError("页面结果不存在")
    existing = pages[index]
    current_revision = int(existing.get("reviewRevision") or 0)
    if int(base_revision) != current_revision:
        raise PageRevisionConflict(current_revision)
    if int(incoming_page.get("page") or 0) != int(page_number):
        raise ValueError("保存内容的页码不一致")
    merged = {**existing, **incoming_page}
    if incoming_page.get("detailsLoaded") is False:
        for key in ("layoutObstacles",):
            if key not in incoming_page and key in existing:
                merged[key] = existing[key]
    merged.pop("detailsLoaded", None)
    merged["reviewRevision"] = current_revision + 1
    merged["reviewedBy"] = {"userId": user["userId"], "username": user["username"]}
    merged["reviewedAt"] = now().isoformat(timespec="seconds")
    pages[index] = merged
    current["pages"] = pages
    current["updatedAt"] = merged["reviewedAt"]
    return merged
