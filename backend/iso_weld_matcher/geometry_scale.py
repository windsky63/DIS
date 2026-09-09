"""Robust page-level scale estimates for vector drawing predicates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import statistics

import fitz


REFERENCE_WIDTH = 1191.0
REFERENCE_HEIGHT = 842.0
REFERENCE_TEXT_HEIGHT = 8.5
REFERENCE_PROCESS_STROKE = 0.50


def _clamp(value: float, low: float = 0.35, high: float = 4.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(frozen=True)
class VectorScaleProfile:
    page_scale: float
    process_stroke_scale: float | None
    text_height_scale: float | None
    threshold_scale: float
    dominant_process_stroke: float | None
    median_text_height: float | None

    def scaled(self, value: float) -> float:
        return float(value) * self.threshold_scale

    def to_dict(self) -> dict[str, float | None]:
        return asdict(self)


def estimate_vector_scale(page: fitz.Page) -> VectorScaleProfile:
    """Estimate scale without assuming text or plotted pen widths exist."""

    width, height = float(page.rect.width), float(page.rect.height)
    page_scale = _clamp(math.sqrt((width * height) / (REFERENCE_WIDTH * REFERENCE_HEIGHT)))

    text_heights = [
        float(word[3]) - float(word[1])
        for word in page.get_text("words")
        if 2.0 <= float(word[3]) - float(word[1]) <= min(width, height) * 0.05
    ]
    median_text_height = statistics.median(text_heights) if text_heights else None
    text_scale = _clamp(median_text_height / REFERENCE_TEXT_HEIGHT) if median_text_height else None

    stroke_widths: list[float] = []
    minimum_long_side = max(width, height) * 0.025
    for drawing in page.get_drawings():
        stroke = float(drawing.get("width") or 0.0)
        rect = drawing.get("rect")
        if stroke <= 0.0 or rect is None:
            continue
        if max(float(rect.width), float(rect.height)) < minimum_long_side:
            continue
        if stroke <= 8.0 * page_scale:
            stroke_widths.append(stroke)
    dominant_stroke = statistics.median(stroke_widths) if stroke_widths else None
    stroke_scale = _clamp(dominant_stroke / REFERENCE_PROCESS_STROKE) if dominant_stroke else None

    # Page geometry is the universal cue. Text and pen widths refine it only
    # if they agree within one octave; outline fonts and heavy pens otherwise
    # must not distort every geometric predicate on the page.
    agreeing = [page_scale]
    for candidate in (stroke_scale, text_scale):
        if candidate is not None and 0.5 * page_scale <= candidate <= 2.0 * page_scale:
            agreeing.append(candidate)
    # Legacy predicates were deliberately tolerant on smaller sheets. Keep
    # their absolute floor and scale upward for A0 / high-resolution exports.
    threshold_scale = max(1.0, _clamp(statistics.median(agreeing)))
    return VectorScaleProfile(
        page_scale=round(page_scale, 6),
        process_stroke_scale=round(stroke_scale, 6) if stroke_scale is not None else None,
        text_height_scale=round(text_scale, 6) if text_scale is not None else None,
        threshold_scale=round(threshold_scale, 6),
        dominant_process_stroke=round(dominant_stroke, 6) if dominant_stroke is not None else None,
        median_text_height=round(median_text_height, 6) if median_text_height is not None else None,
    )
