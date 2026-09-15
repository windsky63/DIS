"""Canonical progress calculation shared by job and queue APIs."""

from __future__ import annotations

import math
from typing import Any


UPLOAD_PERCENT = 15.0
ANALYSIS_PERCENT = 80.0
FINALIZATION_START_PERCENT = UPLOAD_PERCENT + ANALYSIS_PERCENT


def analysis_progress_percent(completed_units: float, total_units: float) -> float:
    total = max(1.0, float(total_units or 0))
    ratio = max(0.0, min(1.0, float(completed_units or 0) / total))
    return round(UPLOAD_PERCENT + ratio * ANALYSIS_PERCENT, 2)


def result_progress_percent(result: dict[str, Any]) -> float:
    if str(result.get("status") or "") == "complete" or result.get("progressStage") == "complete":
        return 100.0
    explicit = result.get("progressPercent")
    try:
        explicit_number = float(explicit)
    except (TypeError, ValueError):
        explicit_number = math.nan
    if math.isfinite(explicit_number):
        return max(0.0, min(100.0, explicit_number))
    if result.get("progressStage") == "training-sample":
        return FINALIZATION_START_PERCENT
    return analysis_progress_percent(
        float(result.get("progressCompletedUnits") or 0),
        float(result.get("progressTotalUnits") or 1),
    )
