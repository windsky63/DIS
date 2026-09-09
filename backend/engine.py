"""Topology-first weld recognition with backend-local recognition modules."""

from __future__ import annotations

import json
from itertools import combinations
import math
from pathlib import Path
import re
from typing import Any, Callable

import fitz

try:
    from .ep3d_components import extract_ep3d_component_callouts
    from .design_components import extract_design_component_symbols
    from .iso_weld_matcher.dual_pdf_topology import (
        PdfWeldCallout,
        extract_isometric_drawing_number,
        extract_design_weld_callouts,
        extract_ep3d_weld_callouts,
        extract_process_skeleton,
        extract_stable_landmarks,
        match_weld_callout_topology,
    )
    from .iso_weld_matcher.idf_topology import analyze_pcf
    from .iso_weld_matcher.pdf_vectors import analyze_page, augment_weld_anchor_candidates
except ImportError:  # Direct ``python backend/server.py`` execution.
    from ep3d_components import extract_ep3d_component_callouts
    from design_components import extract_design_component_symbols
    from iso_weld_matcher.dual_pdf_topology import (
        PdfWeldCallout,
        extract_isometric_drawing_number,
        extract_design_weld_callouts,
        extract_ep3d_weld_callouts,
        extract_process_skeleton,
        extract_stable_landmarks,
        match_weld_callout_topology,
    )
    from iso_weld_matcher.idf_topology import analyze_pcf
    from iso_weld_matcher.pdf_vectors import analyze_page, augment_weld_anchor_candidates


DEFAULT_SYMBOL_CONFIG: dict[str, Any] = {
    "detectionMode": "placement",
    "minimumConfidence": 0.0,
    "fixedSymbolPolicy": "complete-signature",
    "markerPolicy": "strong-process-projection",
    "placementSymbols": {
        "blackCircleEnabled": True,
        "baseVectorShape": "circle",
        "approximateCircleEnabled": False,
        "plainCircleEnabled": True,
        "prefabricatedXEnabled": True,
        "socketThreadBracketEnabled": True,
        "mainPipeOnly": True,
        "includeResearchFallback": False,
        "minimumDiameter": 1.5,
        "maximumDiameter": 14.0,
        "darkThreshold": 0.25,
        "mainPipeTolerance": 3.5,
        "excludeDashedInPlacement": True,
    },
    "comparisonSymbols": {
        "redFrameEnabled": True,
        "redLabelPattern": r"(?:F|FS|RP|S)\d+",
        "minimumConfidence": 0.75,
    },
}


def _effective_symbol_config(value: dict[str, Any] | None) -> dict[str, Any]:
    supplied = value if isinstance(value, dict) else {}
    result = {
        **DEFAULT_SYMBOL_CONFIG,
        **supplied,
        "placementSymbols": {
            **DEFAULT_SYMBOL_CONFIG["placementSymbols"],
            **(supplied.get("placementSymbols") if isinstance(supplied.get("placementSymbols"), dict) else {}),
        },
        "comparisonSymbols": {
            **DEFAULT_SYMBOL_CONFIG["comparisonSymbols"],
            **(supplied.get("comparisonSymbols") if isinstance(supplied.get("comparisonSymbols"), dict) else {}),
        },
    }
    mode = str(result.get("detectionMode") or "placement").casefold()
    result["detectionMode"] = mode if mode in {"placement", "comparison"} else "placement"
    return result


def _reference_display_name(path: Path) -> str:
    # Uploaded files are stored as ``reference-001__<original name>``.  Split
    # only that storage prefix: ``__`` is also meaningful in several original
    # ISO filenames (for example NOZ...__LG1-01).
    return re.sub(r"^(?:reference|target|pcf)-\d{3}__", "", path.name, flags=re.IGNORECASE)


def _normalize_isometric_identity(value: str | None) -> str:
    """Canonicalize a title-block / filename ISO number for page pairing."""

    identity = str(value or "").strip().upper()
    identity = re.sub(r"\.PDF$", "", identity, flags=re.IGNORECASE)
    identity = re.sub(
        r"(?:[_\s-]*(?:SHEET|SHT|SH)[_\s-]*\d+)$",
        "",
        identity,
        flags=re.IGNORECASE,
    )
    identity = identity.replace("__", "/")
    return re.sub(r"\s+", "", identity)


def _reference_filename_identity(path: Path) -> str:
    return _normalize_isometric_identity(_reference_display_name(path))


def _point(candidate: dict[str, Any]) -> tuple[float, float] | None:
    value = candidate.get("process_coordinate") or candidate.get("center")
    if isinstance(value, dict):
        value = (value.get("x"), value.get("y"))
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _display_point(page: fitz.Page, value: fitz.Point) -> tuple[float, float]:
    point = value * page.rotation_matrix
    return float(point.x), float(point.y)


def _display_bbox(page: fitz.Page, value: fitz.Rect) -> tuple[float, float, float, float]:
    corners = [
        _display_point(page, fitz.Point(value.x0, value.y0)),
        _display_point(page, fitz.Point(value.x1, value.y0)),
        _display_point(page, fitz.Point(value.x0, value.y1)),
        _display_point(page, fitz.Point(value.x1, value.y1)),
    ]
    xs, ys = [point[0] for point in corners], [point[1] for point in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _layout_obstacles(page: fitz.Page, features: dict[str, Any]) -> dict[str, Any]:
    """Expose compact research geometry for collision-aware label layout."""
    text_rects = []
    for word in page.get_text("words"):
        if len(word) < 5 or not str(word[4]).strip():
            continue
        box = _display_bbox(
            page,
            fitz.Rect(float(word[0]), float(word[1]), float(word[2]), float(word[3])),
        )
        text_rects.append([round(value, 3) for value in box])

    process_segments = []
    for segment in features.get("strong_process_segments", []):
        start, end = segment.get("start"), segment.get("end")
        if not isinstance(start, (list, tuple)) or not isinstance(end, (list, tuple)):
            continue
        if len(start) < 2 or len(end) < 2:
            continue
        process_segments.append({
            "start": [round(float(start[0]), 3), round(float(start[1]), 3)],
            "end": [round(float(end[0]), 3), round(float(end[1]), 3)],
            "width": round(float(segment.get("stroke_width") or 1.0), 3),
        })
    return {"textRects": text_rects, "processSegments": process_segments}


def _dark_color(value: Any, threshold: float) -> bool:
    return bool(
        value is not None
        and len(value) >= 3
        and max(float(channel) for channel in value[:3]) <= threshold
    )


def _drawing_is_explicitly_dashed(drawing: dict[str, Any]) -> bool:
    """Classify PDF stroke style without inferring semantics from a label.

    PyMuPDF reports solid strokes as an empty value or ``[] 0``. Any
    non-empty dash array is a visual dashed-path property. Filled contours
    remain solid even when their external reference uses an FS-like identity.
    """

    pattern = str(drawing.get("dashes") or "").strip()
    if not pattern:
        return False
    return re.fullmatch(r"\[\s*\]\s*[+-]?(?:0+(?:\.0*)?|\.0+)", pattern) is None


def _source_stroke_pattern(drawings: list[dict[str, Any]]) -> str:
    return "dashed" if any(_drawing_is_explicitly_dashed(item) for item in drawings) else "solid"


def _segment_projection(
    point: tuple[float, float], segment: dict[str, Any]
) -> tuple[float, tuple[float, float]]:
    start = tuple(float(value) for value in segment["start"])
    end = tuple(float(value) for value in segment["end"])
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-12:
        return math.dist(point, start), start
    fraction = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared),
    )
    projected = start[0] + fraction * dx, start[1] + fraction * dy
    return math.dist(point, projected), projected


def _axial_angle_distance(left: float, right: float) -> float:
    difference = abs(left - right) % 180.0
    return min(difference, 180.0 - difference)


def _dark_line_primitives(
    page: fitz.Page, drawings: list[dict[str, Any]], threshold: float
) -> list[dict[str, Any]]:
    lines = []
    for drawing_index, drawing in enumerate(drawings):
        if not _dark_color(drawing.get("color"), threshold):
            continue
        for item_index, item in enumerate(drawing.get("items", [])):
            if not item or item[0] != "l":
                continue
            start, end = _display_point(page, item[1]), _display_point(page, item[2])
            length = math.dist(start, end)
            if length <= 1e-6:
                continue
            lines.append({
                "drawing_index": drawing_index,
                "item_index": item_index,
                "start": start,
                "end": end,
                "midpoint": ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0),
                "length": length,
                "angle": math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 180.0,
            })
    return lines


def _circle_path_encoding(
    page: fitz.Page,
    drawing: dict[str, Any],
    *,
    scale: float,
    allow_approximate: bool,
) -> str | None:
    """Classify a closed PDF path by circular geometry, not a fixed edge count.

    PDF has no native circle operator. CAD exporters normally serialize a
    circle either as four cubic Bezier arcs or as a dense closed polyline. Both
    are primary circle encodings when their sampled radii fit one circle. A
    low-edge polygon is accepted only through the explicitly enabled
    approximate-circle supplement.
    """

    rect = drawing.get("rect")
    if rect is None:
        return None
    bbox = _display_bbox(page, rect)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    aspect_ratio = width / max(height, 1e-9)
    if (
        width <= 1e-9
        or height <= 1e-9
        or not (
            0.90 <= aspect_ratio <= 1.10
            or allow_approximate and 0.75 <= aspect_ratio <= 1.34
        )
    ):
        return None
    center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    items = [item for item in drawing.get("items", []) if item]
    line_items = [item for item in items if item[0] == "l"]
    curve_items = [item for item in items if item[0] == "c"]

    def radial_deviation(points: list[tuple[float, float]]) -> float:
        radii = [math.dist(point, center) for point in points]
        mean_radius = sum(radii) / max(len(radii), 1)
        if mean_radius <= 1e-9:
            return math.inf
        return max(abs(radius - mean_radius) for radius in radii) / mean_radius

    if len(curve_items) == 4 and len(line_items) <= 2:
        samples: list[tuple[float, float]] = []
        for item in curve_items:
            control = [_display_point(page, item[index]) for index in range(1, 5)]
            for step in range(8):
                t = step / 8.0
                u = 1.0 - t
                samples.append((
                    u ** 3 * control[0][0]
                    + 3.0 * u * u * t * control[1][0]
                    + 3.0 * u * t * t * control[2][0]
                    + t ** 3 * control[3][0],
                    u ** 3 * control[0][1]
                    + 3.0 * u * u * t * control[1][1]
                    + 3.0 * u * t * t * control[2][1]
                    + t ** 3 * control[3][1],
                ))
        if radial_deviation(samples) <= 0.035:
            return "bezier-circle"

    if line_items and len(line_items) == len(items):
        points: list[tuple[float, float]] = []
        continuous = True
        for item in line_items:
            start, end = _display_point(page, item[1]), _display_point(page, item[2])
            if points and math.dist(points[-1], start) > 0.18 * scale:
                continuous = False
                break
            if not points:
                points.append(start)
            points.append(end)
        closed = continuous and len(points) >= 2 and math.dist(points[0], points[-1]) <= 0.18 * scale
        if closed and len(line_items) >= 16 and radial_deviation(points[:-1]) <= 0.06:
            return "dense-polyline-circle"
        if (
            allow_approximate
            and closed
            and 5 <= len(line_items) <= 15
            and radial_deviation(points[:-1]) <= 0.18
        ):
            return "low-edge-approximate-circle"
    return None


def _x_modifier_indices(
    center: tuple[float, float], diameter: float, lines: list[dict[str, Any]]
) -> set[int]:
    nearby = [
        line for line in lines
        if diameter * 0.30 <= line["length"] <= diameter * 1.55
        and math.dist(center, line["midpoint"]) <= diameter * 0.38
    ]
    for index, left in enumerate(nearby):
        for right in nearby[index + 1:]:
            angle = _axial_angle_distance(float(left["angle"]), float(right["angle"]))
            if 55.0 <= angle <= 125.0:
                return {int(left["drawing_index"]), int(right["drawing_index"])}
    return set()


def _bracket_modifier_indices(
    center: tuple[float, float], diameter: float, lines: list[dict[str, Any]]
) -> set[int]:
    nearby = [
        line for line in lines
        if diameter * 0.22 <= line["length"] <= diameter * 1.65
        and math.dist(center, line["midpoint"]) <= diameter * 1.25
    ]
    # A square-bracket pair normally contributes two longer parallel stems and
    # at least two short perpendicular caps.  Work by angle rather than page
    # axes so a rotated weld glyph is classified the same way.
    for stem_angle in [float(line["angle"]) for line in nearby]:
        stems = [
            line for line in nearby
            if line["length"] >= diameter * 0.55
            and _axial_angle_distance(float(line["angle"]), stem_angle) <= 12.0
        ]
        caps = [
            line for line in nearby
            if _axial_angle_distance(float(line["angle"]), stem_angle) >= 68.0
        ]
        if len(stems) >= 2 and len(caps) >= 2:
            return {
                int(line["drawing_index"])
                for line in stems + caps
            }
    return set()


def _placement_symbol_anchors(
    page: fitz.Page,
    features: dict[str, Any],
    symbol_config: dict[str, Any],
) -> list[dict[str, Any]]:
    config = symbol_config["placementSymbols"]
    if not bool(config.get("blackCircleEnabled", True)):
        return []
    scale = max(0.35, float(features.get("vector_scale_profile", {}).get("threshold_scale") or 1.0))
    minimum_diameter = max(0.4, float(config.get("minimumDiameter") or 1.5)) * scale
    maximum_diameter = max(minimum_diameter, float(config.get("maximumDiameter") or 14.0) * scale)
    dark_threshold = max(0.0, min(1.0, float(config.get("darkThreshold") or 0.25)))
    tolerance = max(0.2, float(config.get("mainPipeTolerance") or 3.5)) * scale
    drawings = page.get_drawings()
    lines = _dark_line_primitives(page, drawings, dark_threshold)
    # A complete weld glyph can sit on a short terminal or a thin branch that
    # does not survive the strongest-stroke process filter.  Search both the
    # strong route and the remaining credible linework.  The complete glyph
    # signature remains the primary guard, so this does not promote arbitrary
    # compact markers from drawing frames or text.
    process_segments = list(features.get("strong_process_segments") or [])
    seen_segments = {
        (segment.get("drawing_index"), segment.get("item_index"))
        for segment in process_segments
    }
    process_segments.extend(
        segment for segment in features.get("segments", [])
        if float(segment.get("length") or 0.0) >= 6.0 * scale
        and (segment.get("drawing_index"), segment.get("item_index")) not in seen_segments
    )
    result = []

    def circle_match(
        source_indices: list[int],
    ) -> tuple[tuple[float, float, float, float], list[int], str] | None:
        allow_approximate = bool(config.get("approximateCircleEnabled", False))
        matched: list[tuple[int, dict[str, Any], str]] = []
        for index in source_indices:
            if not 0 <= index < len(drawings):
                continue
            drawing = drawings[index]
            if drawing.get("rect") is None:
                continue
            if not (
                _dark_color(drawing.get("fill"), dark_threshold)
                or _dark_color(drawing.get("color"), dark_threshold)
            ):
                continue
            encoding = _circle_path_encoding(
                page,
                drawing,
                scale=scale,
                allow_approximate=allow_approximate,
            )
            if encoding:
                matched.append((index, drawing, encoding))
        if not matched or not any(drawing.get("fill") is not None for _, drawing, _ in matched):
            return None
        boxes = [_display_bbox(page, drawing["rect"]) for _, drawing, _ in matched]
        bbox = (
            min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes),
        )
        encodings = {encoding for _, _, encoding in matched}
        encoding = (
            "bezier-circle" if "bezier-circle" in encodings
            else "dense-polyline-circle" if "dense-polyline-circle" in encodings
            else "low-edge-approximate-circle"
        )
        return bbox, [index for index, _, _ in matched], encoding

    def add_circle(
        source_indices: list[int],
        bbox: tuple[float, float, float, float],
        circle_encoding: str,
        center: tuple[float, float],
        process_coordinate: tuple[float, float],
        nearest_distance: float,
    ) -> None:
        width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        diameter = (width + height) / 2.0
        if not (
            minimum_diameter <= min(width, height)
            and max(width, height) <= maximum_diameter
            and 0.72 <= width / max(height, 1e-9) <= 1.28
        ):
            return
        if center[0] > float(page.rect.width) * 0.78 or center[1] > float(page.rect.height) * 0.82:
            return
        if bool(config.get("mainPipeOnly", True)) and nearest_distance > tolerance:
            return
        modifier_lines = [line for line in lines if int(line["drawing_index"]) not in source_indices]
        x_modifier_indices = _x_modifier_indices(center, diameter, modifier_lines)
        bracket_modifier_indices = _bracket_modifier_indices(center, diameter, modifier_lines)
        has_x = bool(x_modifier_indices)
        has_bracket = bool(bracket_modifier_indices)
        if has_bracket:
            symbol_shape, weld_class = "black-circle-bracket", "socket-threaded"
            enabled = bool(config.get("socketThreadBracketEnabled", True))
        elif has_x:
            symbol_shape, weld_class = "black-circle-x", "prefabricated"
            enabled = bool(config.get("prefabricatedXEnabled", True))
        else:
            symbol_shape, weld_class = "black-circle", "ordinary"
            enabled = bool(config.get("plainCircleEnabled", True))
        if not enabled:
            return
        modifiers = (["x-prefabricated"] if has_x else []) + (["bracket-socket-threaded"] if has_bracket else [])
        source_drawings = [drawings[index] for index in source_indices if 0 <= index < len(drawings)]
        stroke_pattern = _source_stroke_pattern(source_drawings)
        if any(math.dist(process_coordinate, _point(item) or process_coordinate) <= 2.5 * scale for item in result):
            return
        result.append({
            "drawing_index": source_indices[0],
            "source_drawing_indices": source_indices,
            "modifier_drawing_indices": sorted(x_modifier_indices | bracket_modifier_indices),
            "bbox": [round(value, 3) for value in bbox],
            "center": [round(value, 3) for value in center],
            "process_coordinate": [round(value, 3) for value in process_coordinate],
            "distance_to_strong_process_line": round(float(nearest_distance), 3),
            "confidence": 0.99 if modifiers else 0.97,
            "glyph_validated": True,
            "component_kind": "weld-symbol",
            "component_semantic_types": [weld_class],
            "symbol_shape": symbol_shape,
            "symbol_modifiers": modifiers,
            "weld_class": weld_class,
            "stroke_pattern": stroke_pattern,
            "comparison_only_visual": stroke_pattern == "dashed",
            "circle_encoding": circle_encoding,
            "evidence": f"{symbol_shape}-{circle_encoding}-on-main-process-line",
        })

    used_indices: set[int] = set()
    for anchor in features.get("weld_anchor_candidates", []):
        source_indices = sorted({int(value) for value in anchor.get("source_drawing_indices", [])})
        match = circle_match(source_indices)
        center = _point({"process_coordinate": anchor.get("center")})
        process_coordinate = _point(anchor)
        if match is None or center is None or process_coordinate is None:
            continue
        bbox, circle_indices, circle_encoding = match
        distance = float(anchor.get("distance_to_strong_process_line") or math.dist(center, process_coordinate))
        add_circle(circle_indices, bbox, circle_encoding, center, process_coordinate, distance)
        used_indices.update(circle_indices)

    for drawing_index, drawing in enumerate(drawings):
        if drawing_index in used_indices:
            continue
        match = circle_match([drawing_index])
        if match is None:
            continue
        bbox, circle_indices, circle_encoding = match
        center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        nearest_distance, projected = min(
            (_segment_projection(center, segment) for segment in process_segments),
            default=(math.inf, center),
            key=lambda value: value[0],
        )
        add_circle(circle_indices, bbox, circle_encoding, center, projected, nearest_distance)
    if bool(config.get("includeResearchFallback", False)):
        for anchor in features.get("weld_anchor_candidates", []):
            point = _point(anchor)
            if point is None or any(math.dist(point, _point(item) or point) <= 2.5 * scale for item in result):
                continue
            result.append(dict(anchor) | {
                "symbol_shape": "research-fallback",
                "symbol_modifiers": [],
                "weld_class": "unclassified",
                "evidence": f"research-fallback:{anchor.get('evidence') or 'vector-anchor'}",
            })
    return result


def _constrained_composite_anchor_pool(
    page: fitz.Page,
    features: dict[str, Any],
    symbol_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build a global, geometry-gated pool of complete and split weld glyphs.

    AutoCAD can emit the same visual weld as one straight compound contour,
    a triangle plus a Bezier-filled centre, or a curve-unioned socket contour.
    This pool accepts those structural families without using page numbers,
    labels, or fixed coordinates.  The dark body itself must be centred on the
    process route: merely projecting an arrow tip onto the route is not enough.
    Lower-specificity component boundaries are included only as topology
    recovery candidates and are never selected on their own when no reference
    topology is available.
    """

    config = symbol_config["placementSymbols"]
    scale = max(0.35, float(features.get("vector_scale_profile", {}).get("threshold_scale") or 1.0))
    dark_threshold = max(0.0, min(1.0, float(config.get("darkThreshold") or 0.25)))
    drawings = page.get_drawings()
    result = list(_placement_symbol_anchors(page, features, symbol_config))
    if not bool(config.get("approximateCircleEnabled", False)):
        return result

    def append(candidate: dict[str, Any]) -> None:
        point = _point(candidate)
        if point is None:
            return
        if any(math.dist(point, _point(found) or point) <= 2.2 * scale for found in result):
            return
        source_indices = [
            int(value) for value in candidate.get("source_drawing_indices", [])
            if 0 <= int(value) < len(drawings)
        ]
        source_drawings = [drawings[index] for index in source_indices]
        stroke_pattern = str(candidate.get("stroke_pattern") or _source_stroke_pattern(source_drawings))
        result.append(dict(candidate) | {
            "stroke_pattern": stroke_pattern,
            "comparison_only_visual": stroke_pattern == "dashed",
        })

    for anchor in features.get("weld_anchor_candidates", []):
        source_indices = sorted({
            int(value) for value in anchor.get("source_drawing_indices", [])
            if 0 <= int(value) < len(drawings)
        })
        source_drawings = [drawings[index] for index in source_indices]
        if not source_drawings or any(drawing.get("rect") is None for drawing in source_drawings):
            continue
        if not all(
            _dark_color(drawing.get("fill"), dark_threshold)
            or _dark_color(drawing.get("color"), dark_threshold)
            for drawing in source_drawings
        ):
            continue
        items = [item for drawing in source_drawings for item in drawing.get("items", []) if item]
        line_count = sum(item[0] == "l" for item in items)
        curve_count = sum(item[0] == "c" for item in items)
        boxes = [_display_bbox(page, drawing["rect"]) for drawing in source_drawings]
        bbox = (
            min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes),
        )
        width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        short_span, long_span = min(width, height), max(width, height)
        if not (0.8 * scale <= short_span and long_span <= 16.0 * scale):
            continue

        # Flow / annotation arrows have exactly the failure shape seen in this
        # project: a compact dark triangle whose *tip* projects onto a pipe,
        # while the filled body remains several points away.  The comparison
        # side similarly requires its solid weld dot to coincide with the red
        # leader end, so placement-side compound glyphs require their physical
        # centre to coincide with the process route instead of trusting the
        # projected tip alone.
        per_path_counts = [
            (
                sum(bool(item and item[0] == "l") for item in drawing.get("items", [])),
                sum(bool(item and item[0] == "c") for item in drawing.get("items", [])),
            )
            for drawing in source_drawings
        ]
        attached_complete = any(36 <= lines <= 50 and curves == 0 for lines, curves in per_path_counts)
        split_triangle_bezier = (
            len(source_drawings) == 2
            and sorted(per_path_counts) == [(2, 4), (3, 0)]
            and long_span / max(short_span, 1e-9) <= 2.4
        )
        curve_unioned_socket = (
            len(source_drawings) == 1
            and 40 <= line_count <= 55
            and 3 <= curve_count <= 10
            and long_span / max(short_span, 1e-9) <= 1.75
        )
        if not (attached_complete or split_triangle_bezier or curve_unioned_socket):
            continue

        center = _point({"center": anchor.get("center")})
        process_coordinate = _point(anchor)
        if center is None or process_coordinate is None:
            continue
        centre_to_route = math.dist(center, process_coordinate)
        if centre_to_route > 1.25 * scale:
            strong_endpoints = [
                tuple(float(value) for value in segment[side][:2])
                for segment in features.get("strong_process_segments", [])
                for side in ("start", "end")
                if isinstance(segment.get(side), (list, tuple)) and len(segment[side]) >= 2
            ]
            terminal_distance = min(
                (math.dist(process_coordinate, endpoint) for endpoint in strong_endpoints),
                default=math.inf,
            )
            # A split marker at a genuine process terminal can represent an
            # otherwise merged endpoint weld.  It remains weak evidence and
            # must still be selected by the comparison topology; the same
            # offset marker in the middle of a pipe remains an arrow.
            if not (split_triangle_bezier and terminal_distance <= 4.0 * scale):
                continue
            append(dict(anchor) | {
                "bbox": [round(value, 3) for value in bbox],
                "confidence": 0.80,
                "glyph_validated": False,
                "component_kind": "terminal-marker",
                "component_semantic_types": ["terminal-topology-recovery"],
                "symbol_shape": "topology-constrained-terminal-split-marker",
                "symbol_modifiers": ["terminal", "reference-topology-only"],
                "weld_class": "topology-recovery",
                "evidence": "topology-recovery:terminal-split-marker-at-process-endpoint",
                "glyph_signature": {
                    "path_count": len(source_drawings),
                    "line_count": line_count,
                    "curve_count": curve_count,
                    "centre_to_route": round(centre_to_route, 3),
                    "terminal_distance": round(terminal_distance, 3),
                },
            })
            continue

        if attached_complete:
            signature = "attached-complete-contour"
            confidence = 0.98
        elif curve_unioned_socket:
            signature = "curve-unioned-socket-contour"
            confidence = 0.96
        else:
            signature = "split-triangle-bezier-contour"
            confidence = 0.93
        append(dict(anchor) | {
            "bbox": [round(value, 3) for value in bbox],
            "confidence": confidence,
            "glyph_validated": True,
            "component_kind": "weld-symbol",
            "component_semantic_types": ["constrained-composite"],
            "symbol_shape": f"black-circle-{signature}",
            "symbol_modifiers": [signature],
            "weld_class": "constrained-composite",
            "evidence": f"constrained-composite:{signature}-on-process-route",
            "glyph_signature": {
                "path_count": len(source_drawings),
                "line_count": line_count,
                "curve_count": curve_count,
                "short_span": round(short_span, 3),
                "long_span": round(long_span, 3),
                "centre_to_route": round(centre_to_route, 3),
            },
        })

    # These are topology-only recovery points.  They enter the pool so a
    # supplied reference graph can recover a weld whose black centre was
    # merged into adjacent pipework.  They are deliberately lower confidence
    # than every complete visual signature.
    allowed_evidence = {
        "dark-transverse-component-boundary-on-process-route",
        "thin-main-axis-dark-transverse-component-boundary-on-process-route",
        "supported-long-inline-component-corridor-end",
        "grouped-transverse-boundary-in-supported-long-inline-component-corridor",
        "paired-collinear-process-gap-component-boundary",
        "branch-axis-to-main-run-root-weld",
        "continuation-text-near-multiline-process-terminal",
    }
    for boundary in features.get("component_boundary_candidates", []):
        evidence = str(boundary.get("evidence") or "")
        if evidence not in allowed_evidence:
            continue
        point = _point(boundary)
        if point is None:
            continue
        if point[0] > float(page.rect.width) * 0.78 or point[1] > float(page.rect.height) * 0.82:
            continue
        append(dict(boundary) | {
            "confidence": min(0.82, float(boundary.get("confidence") or 0.78)),
            "glyph_validated": False,
            "component_kind": str(boundary.get("component_kind") or "topology-boundary"),
            "component_semantic_types": ["topology-recovery"],
            "symbol_shape": "topology-constrained-boundary",
            "symbol_modifiers": ["reference-topology-only"],
            "weld_class": "topology-recovery",
            "evidence": f"topology-recovery:{evidence}",
        })
    return result


def _comparison_callouts(
    page: fitz.Page, symbol_config: dict[str, Any]
) -> list[PdfWeldCallout]:
    config = symbol_config["comparisonSymbols"]
    if not bool(config.get("redFrameEnabled", True)):
        return []
    pattern_text = str(config.get("redLabelPattern") or DEFAULT_SYMBOL_CONFIG["comparisonSymbols"]["redLabelPattern"])
    if len(pattern_text) > 120:
        raise ValueError("红框编号表达式不能超过 120 个字符")
    try:
        pattern = re.compile(pattern_text, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"红框编号表达式无效：{exc}") from exc
    minimum_confidence = max(0.0, min(1.0, float(config.get("minimumConfidence") or 0.0)))
    return [
        item for item in extract_design_weld_callouts(page, label_pattern=pattern)
        if float(item.extraction_confidence) >= minimum_confidence
    ]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    return str(value)


def _parse_pcf_inventory(path: Path) -> dict[str, Any]:
    """Read the useful PCF weld identity/coordinate subset without an external viewer parser."""

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pipeline = path.stem
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        if not raw[0].isspace():
            parts = stripped.split(maxsplit=1)
            key = parts[0].upper()
            if key == "PIPELINE-REFERENCE" and len(parts) > 1:
                pipeline = parts[1].strip()
                current = None
                continue
            current = {"type": key, "fields": {}}
            records.append(current)
            if len(parts) > 1:
                current["value"] = parts[1].strip()
            continue
        if current is None:
            continue
        parts = stripped.split(maxsplit=1)
        key = parts[0].upper()
        value = parts[1].strip() if len(parts) > 1 else ""
        current["fields"].setdefault(key, []).append(value)

    def coordinates(fields: dict[str, list[str]]) -> list[list[float]]:
        result = []
        for key in ("END-POINT", "BRANCH1-POINT", "BRANCH2-POINT", "CO-ORDS"):
            for raw_value in fields.get(key) or []:
                values = raw_value.replace(",", " ").split()
                if len(values) < 3:
                    continue
                try:
                    result.append([float(values[0]), float(values[1]), float(values[2])])
                except ValueError:
                    continue
        return result

    def coordinate_key(value: list[float] | None) -> tuple[float, float, float] | None:
        if value is None:
            return None
        return tuple(round(float(item), 2) for item in value[:3])

    def component_type(record_type: str, skey: str) -> str:
        value = record_type.upper()
        if value in {"TEE", "CROSS"} or "OLET" in value or skey.upper().startswith(("TE", "OL")):
            return "branch"
        if "FLANGE" in value:
            return "flange"
        if value in {"ELBOW", "BEND"}:
            return "elbow"
        if "VALVE" in value:
            return "valve"
        if value.startswith("PIPE"):
            return "pipe"
        if value in {"GASKET", "BOLT", "SUPPORT"}:
            return value.lower()
        return "fitting"

    components: list[dict[str, Any]] = []
    component_by_id: dict[str, dict[str, Any]] = {}
    components_at_coordinate: dict[tuple[float, float, float], list[dict[str, Any]]] = {}
    for record in records:
        if record["type"] == "WELD":
            continue
        fields = record["fields"]
        component_id = (fields.get("COMPONENT-IDENTIFIER") or [""])[0].strip()
        if not component_id:
            continue
        skey = (fields.get("SKEY") or [""])[0].strip()
        component = {
            "id": component_id,
            "type": component_type(record["type"], skey),
            "record_type": record["type"],
            "skey": skey,
            "coordinates": coordinates(fields),
        }
        components.append(component)
        component_by_id[component_id] = component
        for value in component["coordinates"]:
            components_at_coordinate.setdefault(coordinate_key(value), []).append(component)

    welds = []
    for index, record in enumerate(item for item in records if item["type"] == "WELD"):
        fields = record["fields"]
        coordinate_values = coordinates(fields)
        coordinate = coordinate_values[0] if coordinate_values else None
        label = ""
        for key in ("WELD-REMARK-NUMBER", "WELD-NUMBER", "WELD-IDENTIFIER", "IDENTIFIER"):
            label = (fields.get(key) or [""])[0].strip()
            if label:
                break
        master_id = (fields.get("MASTER-COMPONENT-IDENTIFIER") or [""])[0].strip()
        adjacent = list(components_at_coordinate.get(coordinate_key(coordinate), []))
        master = component_by_id.get(master_id)
        if master is not None and all(item["id"] != master_id for item in adjacent):
            adjacent.append(master)
        adjacent_ids = sorted({item["id"] for item in adjacent})
        adjacent_types = sorted({item["type"] for item in adjacent})
        fingerprints = sorted({
            f"{item['type']}:{item['record_type']}:{item['skey'] or '-'}"
            for item in adjacent
        })
        site_text = (fields.get("WELD-ATTRIBUTE3") or [""])[0]
        welds.append({
            "weld_key": label or f"PCF-{index + 1}",
            "display_number": label or str(index + 1),
            "engineering_coordinate": coordinate,
            "adjacent_component_types": adjacent_types,
            "adjacent_component_fingerprints": fingerprints,
            "adjacent_component_ids": adjacent_ids,
            "master_component_id": master_id,
            "source_weld_id": (
                fields.get("UNIQUE-COMPONENT-IDENTIFIER") or fields.get("UCI") or [""]
            )[0],
            "skey": (fields.get("SKEY") or [""])[0],
            "site_class": "field" if "field" in site_text.casefold() else "shop" if "shop" in site_text.casefold() else "unknown",
        })

    component_to_welds: dict[str, list[int]] = {}
    for weld_index, weld in enumerate(welds):
        for component_id in weld["adjacent_component_ids"]:
            component_to_welds.setdefault(component_id, []).append(weld_index)
    edges = {
        tuple(sorted((left, right)))
        for weld_indices in component_to_welds.values()
        for left_offset, left in enumerate(weld_indices)
        for right in weld_indices[left_offset + 1:]
        if left != right
    }
    neighbours = {index: set() for index in range(len(welds))}
    for left, right in edges:
        neighbours[left].add(right)
        neighbours[right].add(left)
    unseen = set(neighbours)
    connected_component_count = 0
    while unseen:
        connected_component_count += 1
        stack = [unseen.pop()]
        while stack:
            node = stack.pop()
            newly_seen = neighbours[node] & unseen
            unseen.difference_update(newly_seen)
            stack.extend(newly_seen)
    node_count = len(welds)
    is_tree = bool(node_count) and connected_component_count == 1 and len(edges) == node_count - 1
    is_simple_chain = is_tree and all(len(values) <= 2 for values in neighbours.values())
    return {
        "pipeline": pipeline,
        "weld_count": len(welds),
        "weld_site_count": len(welds),
        "field_weld_count": sum(weld["site_class"] == "field" for weld in welds),
        "component_count": len(components),
        "welds": welds,
        "weld_graph": {
            "node_count": node_count,
            "edge_count": len(edges),
            "connected_component_count": connected_component_count,
            "is_tree": is_tree,
            "is_simple_chain": is_simple_chain,
        },
        "parser": "internal-pcf-semantic-topology",
    }


def _candidate_payload(
    candidate: dict[str, Any], page_number: int, width: float, height: float, tier: str, index: int
) -> dict[str, Any] | None:
    point = _point(candidate)
    if point is None:
        return None
    x, y = point
    confidence = float(candidate.get("confidence") or 0.0)
    return {
        "id": f"p{page_number}-{tier}-{index}",
        "page": page_number,
        "x": x,
        "y": y,
        "xNorm": max(0.0, min(1.0, x / max(width, 1.0))),
        "yNorm": max(0.0, min(1.0, y / max(height, 1.0))),
        "labelX": max(0.0, min(width, x + 24.0)),
        "labelY": max(0.0, min(height, y - 18.0)),
        "labelXNorm": max(0.0, min(1.0, (x + 24.0) / max(width, 1.0))),
        "labelYNorm": max(0.0, min(1.0, (y - 18.0) / max(height, 1.0))),
        "confidence": confidence,
        "tier": tier,
        "glyphValidated": bool(candidate.get("glyph_validated")),
        "evidence": str(candidate.get("evidence") or "vector-weld-candidate"),
        "componentKind": str(candidate.get("component_kind") or "weld-symbol"),
        "symbolShape": str(candidate.get("symbol_shape") or "research-selected"),
        "circleEncoding": str(candidate.get("circle_encoding") or ""),
        "symbolModifiers": _json_safe(candidate.get("symbol_modifiers") or []),
        "weldClass": str(candidate.get("weld_class") or "unclassified"),
        "strokePattern": str(candidate.get("stroke_pattern") or "solid"),
        "comparisonOnlyVisual": bool(candidate.get("comparison_only_visual")),
        "bbox": _json_safe(candidate.get("bbox")),
        "sourceDrawingIndices": _json_safe(candidate.get("source_drawing_indices") or []),
        "modifierDrawingIndices": _json_safe(candidate.get("modifier_drawing_indices") or []),
        "included": True,
        "number": "",
        "referenceLabel": "",
        "referenceMatched": False,
    }


def _comparison_candidates(
    callouts: list[PdfWeldCallout], page_number: int, width: float, height: float
) -> list[dict[str, Any]]:
    result = []
    for index, callout in enumerate(callouts):
        x, y = (float(value) for value in callout.weld_point)
        result.append({
            "id": f"p{page_number}-red-frame-{index}",
            "page": page_number,
            "x": x,
            "y": y,
            "xNorm": max(0.0, min(1.0, x / max(width, 1.0))),
            "yNorm": max(0.0, min(1.0, y / max(height, 1.0))),
            "labelX": float(callout.label_center[0]),
            "labelY": float(callout.label_center[1]),
            "labelXNorm": max(0.0, min(1.0, float(callout.label_center[0]) / max(width, 1.0))),
            "labelYNorm": max(0.0, min(1.0, float(callout.label_center[1]) / max(height, 1.0))),
            "confidence": float(callout.extraction_confidence),
            "tier": "red-frame-callout",
            "glyphValidated": True,
            "evidence": str(callout.extraction_method),
            "componentKind": "red-frame-weld-callout",
            "symbolShape": "red-frame",
            "symbolModifiers": ["red-label", "red-leader"],
            "weldClass": "comparison-callout",
            "bbox": _json_safe(callout.label_bbox),
            "included": True,
            "number": "",
            "referenceLabel": str(callout.label),
            "referenceMatched": True,
            "matchConfidence": "direct",
        })
    return result


def _page_candidates(
    features: dict[str, Any], page_number: int, symbol_config: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    config = symbol_config or {}
    width, height = (float(value) for value in features["display_size"])
    minimum_confidence = max(0.0, min(1.0, float(config.get("minimumConfidence") or 0.0)))
    result: list[dict[str, Any]] = []
    # Preserve the research package's selected anchor set. Reintroducing raw
    # compact markers here would bypass its PCF cardinality and semantic gates.
    for index, item in enumerate(features.get("weld_anchor_candidates", [])):
        tier = "validated" if item.get("glyph_validated") else "research-selected"
        payload = _candidate_payload(item, page_number, width, height, tier, index)
        if not payload or payload["confidence"] < minimum_confidence:
            continue
        duplicate = any(
            abs(payload["x"] - found["x"]) <= 2.2
            and abs(payload["y"] - found["y"]) <= 2.2
            for found in result
        )
        if not duplicate:
            result.append(payload)
    return result


def _normalized_identity(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _page_pcf_analysis(
    page: fitz.Page,
    target_pdf: Path,
    pcf_analyses: list[dict[str, Any]],
) -> dict[str, Any] | None:
    usable = [item for item in pcf_analyses if int(item["analysis"].get("weld_site_count") or 0) > 0]
    if len(usable) == 1:
        return usable[0]
    if not usable:
        return None

    # Prefer the lowest exact ISO identity on the page.  As with reference PDFs,
    # the drawing body may contain several SEE-ISO neighbours; the page's own
    # identity is the occurrence in the title block at the bottom of the sheet.
    spatially_ranked: list[tuple[float, float, int, dict[str, Any]]] = []
    for item in usable:
        identities = {
            str(item["path"].stem).strip(),
            str(item["analysis"].get("pipeline") or "").strip(),
        }
        for identity in identities - {""}:
            rectangles = page.search_for(identity)
            if not rectangles:
                continue
            lowest = max(rectangles, key=lambda rect: (float(rect.y0), float(rect.x0)))
            spatially_ranked.append((float(lowest.y0), float(lowest.x0), len(identity), item))
    if spatially_ranked:
        spatially_ranked.sort(key=lambda entry: entry[:3], reverse=True)
        return spatially_ranked[0][3]

    page_identity = _normalized_identity(page.get_text("text"))
    target_identity = _normalized_identity(target_pdf.stem)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in usable:
        identities = {
            _normalized_identity(item["path"].stem),
            _normalized_identity(item["analysis"].get("pipeline")),
        }
        identities.discard("")
        matches = [
            identity for identity in identities
            if len(identity) >= 6 and (identity in page_identity or identity in target_identity)
        ]
        if matches:
            ranked.append((max(map(len, matches)), item))
    if not ranked:
        return None
    ranked.sort(key=lambda entry: entry[0], reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def _as_callouts(candidates: list[dict[str, Any]]) -> list[PdfWeldCallout]:
    return [
        PdfWeldCallout(
            label=f"C{index + 1}",
            label_bbox=(item["x"], item["y"], item["x"], item["y"]),
            label_center=(item["x"], item["y"]),
            weld_point=(item["x"], item["y"]),
            leader_start=(item["x"], item["y"]),
            leader_end=(item["x"], item["y"]),
            extraction_method="pdf-vector-weld-candidate",
            extraction_confidence=float(item.get("confidence") or 0.0),
        )
        for index, item in enumerate(candidates)
    ]


def _reference_pages(
    path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    result = []
    with fitz.open(path) as document:
        for index, page in enumerate(document):
            if progress_callback:
                progress_callback(index + 1, document.page_count)
            title_identity = extract_isometric_drawing_number(page)
            callouts = extract_ep3d_weld_callouts(page)
            component_callouts = extract_ep3d_component_callouts(page)
            # Page identity and object extraction are separate concerns.  A
            # continuation/blank sheet can still be the authoritative page for
            # an ISO even when it contains no recognized callouts.
            if not callouts and not component_callouts and not title_identity:
                continue
            result.append({
                "file": _reference_display_name(path),
                "page": index + 1,
                "isometric_drawing_no": _normalize_isometric_identity(
                    title_identity or _reference_filename_identity(path)
                ),
                "identity_source": (
                    "reference-title-block" if title_identity else "normalized-reference-filename"
                ),
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "callouts": callouts,
                "component_callouts": component_callouts,
                "skeleton": extract_process_skeleton(page),
                "landmarks": extract_stable_landmarks(page),
            })
    return result


def _ep3d_component_inventory(references: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a JSON-safe EP3D-only inventory without mapping it to the design PDF."""

    documents: dict[str, list[dict[str, Any]]] = {}
    counts = {"valve": 0, "flange": 0, "support": 0}
    verified_count = 0
    for reference in references:
        components = list(reference.get("component_callouts") or [])
        if not components:
            continue
        serialized = [_json_safe(item.to_dict()) for item in components]
        documents.setdefault(str(reference.get("file") or ""), []).append({
            "page": int(reference.get("page") or 0),
            "componentCount": len(serialized),
            "components": serialized,
        })
        for item in components:
            component_type = str(item.component_type)
            counts[component_type] = counts.get(component_type, 0) + 1
            verified_count += bool(item.geometry_verified)
    document_items = [
        {
            "file": file_name,
            "componentCount": sum(page["componentCount"] for page in pages),
            "pages": sorted(pages, key=lambda page: page["page"]),
        }
        for file_name, pages in sorted(documents.items())
    ]
    return {
        "schema": "drawing-topology.ep3d-components.v1",
        "scope": "ep3d-reference-only",
        "componentCount": sum(counts.values()),
        "geometryVerifiedCount": verified_count,
        "counts": counts,
        "documents": document_items,
    }


def _ep3d_reference_inventory(references: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose clickable EP3D weld/component positions for the review UI."""

    documents: dict[str, list[dict[str, Any]]] = {}
    for reference in references:
        welds = [{
            "label": str(item.label),
            "type": "weld",
            "point": [round(float(value), 3) for value in item.weld_point],
            "confidence": round(float(item.extraction_confidence), 3),
        } for item in reference.get("callouts") or []]
        components = [{
            "label": str(item.label),
            "type": str(item.component_type),
            "point": [round(float(value), 3) for value in item.component_point],
            "confidence": round(float(item.extraction_confidence), 3),
            "geometryVerified": bool(item.geometry_verified),
        } for item in reference.get("component_callouts") or []]
        documents.setdefault(str(reference.get("file") or ""), []).append({
            "page": int(reference.get("page") or 0),
            "width": float(reference.get("width") or 1.0),
            "height": float(reference.get("height") or 1.0),
            "counts": {
                "weld": len(welds),
                "valve": sum(item["type"] == "valve" for item in components),
                "flange": sum(item["type"] == "flange" for item in components),
                "support": sum(item["type"] == "support" for item in components),
            },
            "items": welds + components,
        })
    return {
        "schema": "drawing-topology.ep3d-reference-hints.v1",
        "documents": [
            {"file": file_name, "pages": sorted(pages, key=lambda item: item["page"])}
            for file_name, pages in sorted(documents.items())
        ],
    }


def _design_component_candidates(
    page: fitz.Page,
    features: dict[str, Any],
    page_number: int,
    width: float,
    height: float,
) -> list[dict[str, Any]]:
    """Expose placement-side component shapes as editable numbering targets."""

    prefixes = {"valve": "V", "flange": "FL", "support": "SP"}
    result = []
    for index, symbol in enumerate(extract_design_component_symbols(page, features), start=1):
        item = _json_safe(symbol.to_dict())
        x, y = (float(value) for value in symbol.center)
        component_type = str(symbol.component_type)
        result.append(item | {
            "id": f"p{page_number}-design-component-{index}",
            "page": page_number,
            "x": round(x, 3),
            "y": round(y, 3),
            "xNorm": x / width if width else 0.0,
            "yNorm": y / height if height else 0.0,
            "included": True,
            "origin": "recognized",
            "componentKind": "design-component",
            "autoNumberPrefix": prefixes.get(component_type, "C"),
            "glyphValidated": True,
            "referenceMatched": False,
            "defaultMarkerStyle": {
                "shape": "rectangle",
                "color": "#1769d2",
            },
        })
    return result


def _design_component_inventory(pages: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"valve": 0, "flange": 0, "support": 0}
    page_items = []
    for page in pages:
        components = list(page.get("designComponents") or [])
        if not components:
            continue
        for item in components:
            component_type = str(item.get("componentType") or "")
            counts[component_type] = counts.get(component_type, 0) + 1
        page_items.append({
            "page": int(page.get("page") or 0),
            "componentCount": len(components),
            "components": components,
        })
    return {
        "schema": "drawing-topology.design-components.v1",
        "scope": "design-placement-shape-only",
        "componentCount": sum(counts.values()),
        "counts": counts,
        "pages": page_items,
    }


def _linear_chain_order(
    points: list[tuple[float, float]],
    anchor: tuple[float, float],
) -> tuple[list[int], float] | None:
    """Order a nearly straight component chain away from a weld anchor."""

    if len(points) < 2:
        return None
    endpoint_pair = max(
        combinations(range(len(points)), 2),
        key=lambda pair: math.dist(points[pair[0]], points[pair[1]]),
    )
    start, end = points[endpoint_pair[0]], points[endpoint_pair[1]]
    span = math.dist(start, end)
    if span <= 1e-6:
        return None
    axis = ((end[0] - start[0]) / span, (end[1] - start[1]) / span)
    projections = [
        (point[0] - start[0]) * axis[0] + (point[1] - start[1]) * axis[1]
        for point in points
    ]
    residuals = [
        abs(-(point[0] - start[0]) * axis[1] + (point[1] - start[1]) * axis[0])
        for point in points
    ]
    # Branching support groups must stay with the general topology matcher.
    if max(residuals, default=0.0) > max(4.0, span * 0.12):
        return None
    low, high = min(projections), max(projections)
    anchor_projection = (
        (anchor[0] - start[0]) * axis[0] + (anchor[1] - start[1]) * axis[1]
    )
    if low < anchor_projection < high:
        inside_endpoint_distance = min(anchor_projection - low, high - anchor_projection)
        if inside_endpoint_distance > span * 0.25:
            return None
    from_low_end = abs(anchor_projection - low) <= abs(anchor_projection - high)
    order = sorted(
        range(len(points)),
        key=lambda index: projections[index],
        reverse=not from_low_end,
    )
    endpoint_score = min(
        math.dist(anchor, points[order[0]]),
        math.dist(anchor, points[order[-1]]),
    ) / span
    return order, endpoint_score


def _weld_anchored_support_chain_pairs(
    design_group: list[dict[str, Any]],
    ep3d_group: list[Any],
    design_welds: dict[str, tuple[float, float]],
    reference_welds: dict[str, tuple[float, float]],
    common_labels: list[str],
) -> tuple[list[tuple[int, int, float]], str] | None:
    """Match a straight support run monotonically from the same weld end."""

    if len(design_group) != len(ep3d_group) or len(design_group) < 2 or not common_labels:
        return None
    design_points = [(float(item["x"]), float(item["y"])) for item in design_group]
    reference_points = [tuple(float(value) for value in item.component_point) for item in ep3d_group]
    anchors = []
    for label in common_labels:
        design_order = _linear_chain_order(design_points, design_welds[label])
        reference_order = _linear_chain_order(reference_points, reference_welds[label])
        if design_order is None or reference_order is None:
            continue
        anchors.append((max(design_order[1], reference_order[1]), label, design_order[0], reference_order[0]))
    if not anchors:
        return None
    _, anchor_label, design_order, reference_order = min(anchors, key=lambda item: (item[0], item[1]))
    pairs = [
        (design_index, reference_index, 0.0)
        for design_index, reference_index in zip(design_order, reference_order)
    ]
    return pairs, anchor_label


def _match_design_components_to_reference(
    components: list[dict[str, Any]],
    weld_candidates: list[dict[str, Any]],
    reference: dict[str, Any] | None,
) -> int:
    """Assign EP3D identities from weld-relative topology, never page order."""

    if not components or not reference:
        return 0
    ep3d_components = list(reference.get("component_callouts") or [])
    reference_welds = {
        str(callout.label): tuple(float(value) for value in callout.weld_point)
        for callout in reference.get("callouts") or []
    }
    design_welds = {
        str(item.get("referenceLabel")): (float(item["x"]), float(item["y"]))
        for item in weld_candidates
        if item.get("referenceMatched")
        and (
            not item.get("referenceFile")
            or (
                str(item.get("referenceFile") or "") == str(reference.get("file") or "")
                and int(item.get("referencePage") or 0) == int(reference.get("page") or 0)
            )
        )
        and str(item.get("referenceLabel") or "") in reference_welds
    }
    common_labels = sorted(design_welds)
    scale_samples = []
    for left_index, left_label in enumerate(common_labels):
        for right_label in common_labels[left_index + 1:]:
            design_distance = math.dist(design_welds[left_label], design_welds[right_label])
            if design_distance > 1e-6:
                scale_samples.append(
                    math.dist(reference_welds[left_label], reference_welds[right_label]) / design_distance
                )
    scale = sorted(scale_samples)[len(scale_samples) // 2] if scale_samples else 1.0
    reference_diagonal = math.hypot(
        max((point[0] for point in reference_welds.values()), default=1.0)
        - min((point[0] for point in reference_welds.values()), default=0.0),
        max((point[1] for point in reference_welds.values()), default=1.0)
        - min((point[1] for point in reference_welds.values()), default=0.0),
    ) or 1.0

    matched_count = 0
    for component_type in ("valve", "flange", "support"):
        design_group = [
            item for item in components
            if item.get("componentType") == component_type and not item.get("referenceMatched")
        ]
        ep3d_group = [item for item in ep3d_components if item.component_type == component_type]
        if not design_group or not ep3d_group:
            continue
        used_design: set[int] = set()
        used_ep3d: set[int] = set()
        if component_type == "support":
            chain_match = _weld_anchored_support_chain_pairs(
                design_group,
                ep3d_group,
                design_welds,
                reference_welds,
                common_labels,
            )
            if chain_match is not None:
                chain_pairs, anchor_label = chain_match
                for design_index, ep3d_index, cost in chain_pairs:
                    design = design_group[design_index]
                    callout = ep3d_group[ep3d_index]
                    design.update({
                        "referenceLabel": callout.label,
                        "referenceMatched": True,
                        "referenceFile": reference.get("file"),
                        "referencePage": reference.get("page"),
                        "matchConfidence": "high",
                        "componentMatchMethod": "weld-anchored-support-chain-one-to-one",
                        "componentMatchAnchor": anchor_label,
                        "componentMatchCost": round(cost, 4),
                    })
                    used_design.add(design_index)
                    used_ep3d.add(ep3d_index)
                    matched_count += 1
        ranked_pairs = []
        for design_index, design in enumerate(design_group):
            design_point = float(design["x"]), float(design["y"])
            for ep3d_index, callout in enumerate(ep3d_group):
                reference_point = tuple(float(value) for value in callout.component_point)
                if common_labels:
                    residuals = [
                        abs(
                            math.dist(reference_point, reference_welds[label])
                            - scale * math.dist(design_point, design_welds[label])
                        ) / reference_diagonal
                        for label in common_labels
                    ]
                    distance_cost = sum(residuals) / len(residuals)
                    design_order = sorted(common_labels, key=lambda label: math.dist(design_point, design_welds[label]))
                    reference_order = sorted(common_labels, key=lambda label: math.dist(reference_point, reference_welds[label]))
                    rank_cost = sum(
                        abs(design_order.index(label) - reference_order.index(label))
                        for label in common_labels
                    ) / max(1, len(common_labels) ** 2)
                    cost = distance_cost + rank_cost * 0.35
                else:
                    cost = 0.0 if len(design_group) == len(ep3d_group) == 1 else float("inf")
                ranked_pairs.append((cost, design_index, ep3d_index))
        for cost, design_index, ep3d_index in sorted(ranked_pairs):
            if not math.isfinite(cost) or cost > 0.55:
                continue
            if design_index in used_design or ep3d_index in used_ep3d:
                continue
            design = design_group[design_index]
            callout = ep3d_group[ep3d_index]
            design.update({
                "referenceLabel": callout.label,
                "referenceMatched": True,
                "referenceFile": reference.get("file"),
                "referencePage": reference.get("page"),
                "matchConfidence": "high" if cost <= 0.18 else "medium",
                "componentMatchMethod": "weld-relative-topology-one-to-one",
                "componentMatchCost": round(cost, 4),
            })
            used_design.add(design_index)
            used_ep3d.add(ep3d_index)
            matched_count += 1
    return matched_count


def _match_design_components_to_references(
    components: list[dict[str, Any]],
    weld_candidates: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> int:
    """Map components only within the authoritative 1:N ISO page set."""

    ranked = sorted(
        references,
        key=lambda reference: sum(
            bool(candidate.get("referenceMatched"))
            and str(candidate.get("referenceFile") or "") == str(reference.get("file") or "")
            and int(candidate.get("referencePage") or 0) == int(reference.get("page") or 0)
            for candidate in weld_candidates
        ),
        reverse=True,
    )
    return sum(
        _match_design_components_to_reference(components, weld_candidates, reference)
        for reference in ranked
    )


def _page_reference_pairing(
    page: fitz.Page,
    references: list[dict[str, Any]],
    *,
    allow_single_reference_fallback: bool = True,
) -> dict[str, Any]:
    """Build the title-number index used by the reference pilot.

    A design page is allowed to match only EP3D pages carrying the identical
    lower-right Isometric drawing number.  This supports 1:N sheets without
    allowing unrelated files in the upload folder to compete for a candidate.
    """

    design_identity = _normalize_isometric_identity(
        extract_isometric_drawing_number(page)
    )
    eligible = [
        reference for reference in references
        if design_identity
        and _normalize_isometric_identity(reference.get("isometric_drawing_no")) == design_identity
    ]
    method = "title-block-exact"
    if not eligible and allow_single_reference_fallback and len(references) == 1:
        # A single-reference upload is unambiguous and preserves support for
        # older drawings whose title block is rasterized rather than text.
        eligible = list(references)
        method = "single-reference-fallback"
    if eligible:
        cardinality = "1:1" if len(eligible) == 1 else f"1:{len(eligible)}"
        status = f"exact-title-{cardinality}" if method == "title-block-exact" else method
    else:
        status = "no-authoritative-page-pair"
    return {
        "designIsometricDrawingNo": design_identity or None,
        "referencePairingStatus": status,
        "referencePairingMethod": method if eligible else "none",
        "eligibleReferences": eligible,
        "eligibleReferencePages": [
            {
                "file": str(reference.get("file") or ""),
                "page": int(reference.get("page") or 0),
                "isometricDrawingNo": reference.get("isometric_drawing_no"),
                "identitySource": reference.get("identity_source"),
            }
            for reference in eligible
        ],
    }


def _primary_page_reference(
    page: fitz.Page, references: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return an unambiguous reference selected by exact title-block identity."""

    eligible = _page_reference_pairing(page, references)["eligibleReferences"]
    return eligible[0] if len(eligible) == 1 else None


def _placement_reference_page(reference_page: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Preserve every reference identity; prefixes are not visual semantics.

    F / FS / S-like values are project identifiers. They remain available to
    the topology matcher in both modes. Placement exclusion is applied later
    from the candidate's PDF stroke geometry, never from this prefix. The
    returned labels are audit-only identities whose prefix must not be
    interpreted as a dashed symbol.
    """

    identity_only_labels = [
        str(callout.label)
        for callout in reference_page.get("callouts") or []
        if re.fullmatch(r"FS\d+", str(callout.label or ""), re.IGNORECASE)
    ]
    return dict(reference_page) | {
        "callouts": list(reference_page.get("callouts") or [])
    }, identity_only_labels


def _reference_constrained_candidates(
    page: fitz.Page,
    candidates: list[dict[str, Any]],
    reference_page: dict[str, Any] | list[dict[str, Any]],
    *,
    design_skeleton: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select complete physical glyphs plus explicitly matched topology gaps.

    Reference cardinality is an audit and an upper bound for weak recovery; it
    is never permission to fill a shortfall with arbitrary arrows or component
    boundaries.  Conversely, an obvious complete glyph is retained even if a
    graph alignment did not assign it a reference label.
    """

    reference_pages = reference_page if isinstance(reference_page, list) else [reference_page]
    reference = _match_reference(
        page,
        candidates,
        reference_pages,
        design_skeleton=design_skeleton,
    )
    unfiltered_expected = sum(len(item.get("callouts") or []) for item in reference_pages)
    visually_dashed = [
        candidate for candidate in candidates
        if bool(candidate.get("comparisonOnlyVisual"))
        or str(candidate.get("strokePattern") or "").casefold() == "dashed"
    ]
    dashed_ids = {id(candidate) for candidate in visually_dashed}
    visually_dashed_labels = sorted({
        str(candidate.get("referenceLabel") or "")
        for candidate in visually_dashed
        if str(candidate.get("referenceLabel") or "")
    })
    # A matched visual dashed point is intentionally absent from placement,
    # so it does not create a cardinality shortfall that could refill itself
    # through another unmatched marker.
    expected = max(0, unfiltered_expected - len(visually_dashed_labels))
    eligible_candidates = [candidate for candidate in candidates if id(candidate) not in dashed_ids]
    physical = [
        candidate for candidate in eligible_candidates
        if bool(candidate.get("glyphValidated"))
        and not str(candidate.get("evidence") or "").startswith("topology-recovery:")
    ]
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    matched_physical = sorted(
        (candidate for candidate in physical if candidate.get("referenceMatched")),
        key=lambda candidate: (
            confidence_rank.get(str(candidate.get("matchConfidence") or "").casefold(), 0),
            float(candidate.get("confidence") or 0.0),
        ),
        reverse=True,
    )
    matched_recovery = sorted(
        (
            candidate for candidate in eligible_candidates
            if candidate.get("referenceMatched")
            and str(candidate.get("evidence") or "").startswith("topology-recovery:")
        ),
        key=lambda candidate: (
            confidence_rank.get(str(candidate.get("matchConfidence") or "").casefold(), 0),
            float(candidate.get("confidence") or 0.0),
        ),
        reverse=True,
    )
    selected = (matched_physical + matched_recovery)[:expected]
    selected_ids = {id(candidate) for candidate in selected}
    residual = max(0, expected - len(selected))
    # A graph alignment can miss an otherwise complete physical circle at a
    # crowded junction.  Only complete physical evidence may fill that small
    # residual; unmatched topology boundaries and arrow-shaped paths cannot.
    physical_fallback = sorted(
        (candidate for candidate in physical if id(candidate) not in selected_ids),
        key=lambda candidate: float(candidate.get("confidence") or 0.0),
        reverse=True,
    )[:residual]
    selected.extend(physical_fallback)

    # A plain pipe boundary is not a weld signature.  In these drawings flow
    # arrow tips, arrow-adjacent pipe gaps and uninterrupted transverse strokes
    # all enter the same generic topology-boundary pool.  Reference proximity
    # alone must not promote them.  Keep only the narrowly defined terminal
    # split marker, which has its own dark compound path at a genuine process
    # endpoint; reject every other weak topology recovery without refilling the
    # resulting gap.
    rejected_arrow_risk = [
        candidate for candidate in selected
        if str(candidate.get("evidence") or "").startswith("topology-recovery:")
        and str(candidate.get("evidence") or "")
        != "topology-recovery:terminal-split-marker-at-process-endpoint"
    ]
    rejected_ids = {id(candidate) for candidate in rejected_arrow_risk}
    selected = [candidate for candidate in selected if id(candidate) not in rejected_ids]
    unresolved = max(0, expected - len(selected))
    reference = dict(reference) | {
        "expectedCalloutCount": expected,
        "unfilteredExpectedCalloutCount": unfiltered_expected,
        "excludedVisualDashedCandidateCount": len(visually_dashed),
        "excludedVisualDashedReferenceLabels": visually_dashed_labels,
        "referencePrefixPolicy": "identity-only-no-symbol-semantics",
        "detectedPhysicalSignatureCount": len(physical),
        "physicalSignatureCount": sum(bool(candidate.get("glyphValidated")) for candidate in selected),
        "topologyRecoveredCount": sum(
            str(candidate.get("evidence") or "").startswith("topology-recovery:")
            for candidate in selected
        ),
        "unmatchedPhysicalFallbackCount": len(physical_fallback),
        "rejectedArrowRiskRecoveryCount": len(rejected_arrow_risk),
        "rejectedArrowRiskLabels": [
            str(candidate.get("referenceLabel") or "") for candidate in rejected_arrow_risk
        ],
        "rejectedArrowRiskEvidence": [
            str(candidate.get("evidence") or "") for candidate in rejected_arrow_risk
        ],
        "unresolvedCalloutGap": unresolved,
        "signatureCompletedCount": 0,
        "selectedCandidateCount": len(selected),
        "selectionPolicy": "physical-multi-evidence-plus-explicit-topology-recovery",
    }
    if unresolved:
        reference["status"] = "partial-no-cardinality-fill"
    return selected, reference


def _match_reference(
    target_page: fitz.Page,
    candidates: list[dict[str, Any]],
    references: list[dict[str, Any]],
    design_skeleton: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not candidates or not references:
        return {"status": "not-available", "acceptedMatchCount": 0}
    design_callouts = _as_callouts(candidates)
    attempts: list[tuple[tuple[int, int], dict[str, Any], dict[str, Any]]] = []
    for reference in references:
        if not reference.get("callouts"):
            continue
        try:
            matched = match_weld_callout_topology(
                design_callouts,
                reference["callouts"],
                design_skeleton=design_skeleton or extract_process_skeleton(target_page),
                ep3d_skeleton=reference["skeleton"],
                design_landmarks=extract_stable_landmarks(target_page),
                ep3d_landmarks=reference["landmarks"],
                design_page=target_page,
            )
        except Exception as exc:  # research diagnostics must not destroy the scan
            matched = {"status": "match-error", "matches": [], "error": str(exc), "summary": {}}
        summary = matched.get("summary") or {}
        score = (len(matched.get("matches") or []), int(summary.get("high_or_medium_count") or 0))
        attempts.append((score, reference, matched))
    if not attempts:
        return {"status": "no-reference-callouts", "acceptedMatchCount": 0}
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    selected: dict[int, tuple[tuple[int, int, int, int], dict[str, Any], dict[str, Any]]] = {}
    for score, reference, matched in attempts:
        for item in matched.get("matches") or []:
            index = int(item.get("design_index", -1))
            if not 0 <= index < len(candidates):
                continue
            rank = (
                confidence_rank.get(str(item.get("confidence") or "").casefold(), 0),
                score[1],
                score[0],
                len(reference["callouts"]),
            )
            if index not in selected or rank > selected[index][0]:
                selected[index] = rank, reference, item
    used_references: dict[tuple[str, int], dict[str, Any]] = {}
    for index, (_, reference, item) in selected.items():
        candidates[index]["referenceLabel"] = str(item.get("ep3d_label") or "")
        candidates[index]["referenceMatched"] = True
        candidates[index]["referenceFile"] = reference["file"]
        candidates[index]["referencePage"] = reference["page"]
        candidates[index]["matchConfidence"] = str(item.get("confidence") or "")
        used_references[(reference["file"], reference["page"])] = reference
    best_attempt = max(attempts, key=lambda attempt: attempt[0])
    _, best_reference, best_match = best_attempt
    return {
        "status": "matched" if selected else best_match.get("status") or "no-match",
        "referenceFile": best_reference["file"],
        "referencePage": best_reference["page"],
        "referenceFiles": sorted({reference["file"] for reference in used_references.values()}),
        "referencePages": [
            {"file": file_name, "page": page_number}
            for file_name, page_number in sorted(used_references)
        ],
        "referenceCalloutCount": sum(len(reference["callouts"]) for reference in used_references.values()),
        "acceptedMatchCount": len(selected),
        "summary": _json_safe(best_match.get("summary") or {}),
        "error": best_match.get("error"),
    }


def analyze_documents(
    target_pdf: Path,
    *,
    reference_pdf: Path | None = None,
    reference_pdfs: list[Path] | None = None,
    pcf_file: Path | None = None,
    pcf_files: list[Path] | None = None,
    symbol_config: dict[str, Any] | None = None,
    start_page: int = 1,
    end_page: int | None = None,
    progress_callback: Callable[[str], None] | None = None,
    page_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    def progress(message: str) -> None:
        if progress_callback:
            progress_callback(message)

    reference_paths = list(reference_pdfs or [])
    if reference_pdf and reference_pdf not in reference_paths:
        reference_paths.append(reference_pdf)
    references = []
    for index, path in enumerate(reference_paths, start=1):
        progress(f"解析对照 PDF {index}/{len(reference_paths)}：{_reference_display_name(path)}")
        reference_pages = _reference_pages(
            path,
            lambda page, total, current=index: progress(
                f"解析对照 PDF {current}/{len(reference_paths)}：页面 {page}/{total}"
            ),
        )
        references.extend(reference_pages)
        component_count = sum(len(page.get("component_callouts") or []) for page in reference_pages)
        progress(
            f"对照 PDF {index}/{len(reference_paths)} 解析完成："
            f"发现 {len(reference_pages)} 个可匹配页面，EP3D 管件 {component_count} 个"
        )
    ep3d_component_inventory = _ep3d_component_inventory(references)
    ep3d_reference_inventory = _ep3d_reference_inventory(references)
    pcf_paths = list(pcf_files or [])
    if pcf_file and pcf_file not in pcf_paths:
        pcf_paths.append(pcf_file)
    effective_config = _effective_symbol_config(symbol_config)
    detection_mode = str(effective_config["detectionMode"])
    progress(
        f"PDF 图元研究配置：模式={'落图' if detection_mode == 'placement' else '对照'}，"
        f"固定符号={effective_config.get('fixedSymbolPolicy') or 'complete-signature'}，"
        f"填充标记={effective_config.get('markerPolicy') or 'strong-process-projection'}，"
        f"最低置信度={float(effective_config.get('minimumConfidence') or 0.0):.2f}"
    )
    pcf_analyses: list[dict[str, Any]] = []
    pcf_results = []
    all_welds = []
    pcf_warnings = []
    for path in pcf_paths:
        progress(f"解析 PCF：{_reference_display_name(path)}")
        warning = ""
        try:
            pcf = analyze_pcf(path)
        except Exception as exc:
            pcf = _parse_pcf_inventory(path)
            warning = f"完整 PCF 查看器解析器不可用，已使用内置焊口清单解析：{exc}"
        pcf_analyses.append({"path": path, "analysis": pcf, "warning": warning})
        welds = pcf.get("welds") or []
        graph = pcf.get("weld_graph") or {}
        all_welds.extend(welds)
        if warning:
            pcf_warnings.append(f"{_reference_display_name(path)}：{warning}")
        pcf_results.append({
            "file": _reference_display_name(path),
            "pipeline": pcf.get("pipeline"),
            "weldCount": len(welds),
            "parser": pcf.get("parser") or pcf.get("reference_parser"),
            "degraded": bool(warning),
            "graph": _json_safe({
                "nodeCount": graph.get("node_count"),
                "edgeCount": graph.get("edge_count"),
                "connectedComponentCount": graph.get("connected_component_count"),
                "isTree": graph.get("is_tree"),
                "isSimpleChain": graph.get("is_simple_chain"),
            }),
            "warning": warning,
        })
        progress(f"PCF 解析完成：{_reference_display_name(path)}，焊口 {len(welds)} 个")
    pcf_labels = {
        str(value).strip().casefold()
        for weld in all_welds
        for value in (weld.get("weld_key"), weld.get("display_number"))
        if str(value or "").strip()
    }
    pages: list[dict[str, Any]] = []
    with fitz.open(target_pdf) as document:
        first = max(1, int(start_page))
        last = min(document.page_count, int(end_page or document.page_count))
        if first > last:
            raise ValueError(f"页码范围无效：{first}—{last}，PDF 共 {document.page_count} 页")
        total_pages = last - first + 1
        for page_number in range(first, last + 1):
            page_progress = page_number - first + 1
            progress(f"研究设计图页面 {page_progress}/{total_pages}：PDF 第 {page_number} 页")
            page = document[page_number - 1]
            features = analyze_page(page, source=target_pdf.name, page_number=page_number)
            width, height = (float(value) for value in features["display_size"])
            page_pcf = None
            page_references: list[dict[str, Any]] = []
            pairing_audit: dict[str, Any] = {
                "designIsometricDrawingNo": None,
                "referencePairingStatus": "not-applicable",
                "referencePairingMethod": "none",
                "eligibleReferencePages": [],
            }
            if detection_mode == "comparison":
                red_callouts = _comparison_callouts(page, effective_config)
                candidates = _comparison_candidates(red_callouts, page_number, width, height)
                reference = {
                    "status": "direct-red-frame-callouts" if candidates else "no-red-frame-callouts",
                    "acceptedMatchCount": len(candidates),
                    "referenceCalloutCount": len(candidates),
                    "referenceFiles": [],
                    "referencePages": [],
                }
                features["anchor_source_strategy"] = "red-frame-attached-red-leader"
            else:
                page_pcf = _page_pcf_analysis(page, target_pdf, pcf_analyses)
                try:
                    features = augment_weld_anchor_candidates(
                        features,
                        page_pcf["analysis"] if page_pcf else None,
                        fixed_symbol_policy=str(effective_config.get("fixedSymbolPolicy") or "complete-signature"),
                        marker_policy=str(effective_config.get("markerPolicy") or "strong-process-projection"),
                    )
                except Exception as exc:
                    progress(f"设计图第 {page_number} 页 PCF 研究增强失败，回退到研究原始候选：{exc}")
                    features = augment_weld_anchor_candidates(
                        features,
                        fixed_symbol_policy=str(effective_config.get("fixedSymbolPolicy") or "complete-signature"),
                        marker_policy=str(effective_config.get("markerPolicy") or "strong-process-projection"),
                    )
                    page_pcf = None
                if page_pcf:
                    progress(
                        f"设计图第 {page_number} 页已应用 PCF 研究约束："
                        f"{_reference_display_name(page_pcf['path'])}"
                    )
                elif pcf_analyses:
                    progress(f"设计图第 {page_number} 页未找到唯一对应 PCF，按研究原始图元候选处理")
                composite_pool = _constrained_composite_anchor_pool(page, features, effective_config)
                strong_segments = list(features.get("strong_process_segments") or [])
                design_skeleton = (
                    {"segments": strong_segments, "segment_count": len(strong_segments), "source": "formal-strong-process-segments"}
                    if strong_segments else None
                )
                pairing = _page_reference_pairing(
                    page,
                    references,
                    allow_single_reference_fallback=len(reference_paths) == 1,
                )
                page_references = list(pairing.pop("eligibleReferences"))
                pairing_audit = pairing
                if page_references:
                    placement_references = []
                    identity_only_labels = []
                    for page_reference in page_references:
                        placement_reference, page_identity_only = _placement_reference_page(page_reference)
                        placement_references.append(placement_reference)
                        identity_only_labels.extend(page_identity_only)
                    pool_features = features | {
                        "weld_anchor_candidates": composite_pool,
                        "anchor_source_strategy": "reference-constrained-composite-signatures",
                    }
                    candidate_pool = _page_candidates(pool_features, page_number, effective_config)
                    candidates, reference = _reference_constrained_candidates(
                        page,
                        candidate_pool,
                        placement_references,
                        design_skeleton=design_skeleton,
                    )
                    reference = dict(reference) | {
                        "referenceIdentityOnlyPrefixCount": len(identity_only_labels),
                        "referenceIdentityOnlyPrefixLabels": identity_only_labels,
                        "unfilteredReferenceCalloutCount": sum(
                            len(item.get("callouts") or []) for item in page_references
                        ),
                    }
                    features = pool_features | {
                        "weld_anchor_candidates": composite_pool,
                        "anchor_source_strategy": "reference-constrained-composite-signatures",
                    }
                else:
                    # Without an authoritative title-number pair, topology-only
                    # recovery and cross-file competition are unsafe.  Keep
                    # only visual signatures and deliberately leave them
                    # unmatched instead of borrowing labels from another ISO.
                    visual_anchors = [
                        anchor for anchor in composite_pool
                        if not str(anchor.get("evidence") or "").startswith("topology-recovery:")
                    ]
                    features = features | {
                        "weld_anchor_candidates": visual_anchors,
                        "anchor_source_strategy": "constrained-composite-signatures",
                    }
                    candidates = _page_candidates(features, page_number, effective_config)
                    reference = {
                        "status": "no-authoritative-page-pair",
                        "acceptedMatchCount": 0,
                        "referenceCalloutCount": 0,
                        "referenceFiles": [],
                        "referencePages": [],
                    }
                reference = dict(reference) | pairing_audit
            weld_candidates = candidates
            component_features = features | {
                "recognized_weld_occluders": [
                    {
                        "id": str(candidate.get("id") or ""),
                        "center": [float(candidate["x"]), float(candidate["y"])],
                        "bbox": list(candidate.get("bbox") or []),
                        "source_drawing_indices": list(candidate.get("sourceDrawingIndices") or []),
                        "modifier_drawing_indices": list(candidate.get("modifierDrawingIndices") or []),
                    }
                    for candidate in weld_candidates
                    if candidate.get("componentKind") == "weld-symbol"
                    and bool(candidate.get("glyphValidated"))
                    and bool(candidate.get("included", True))
                    and candidate.get("x") is not None
                    and candidate.get("y") is not None
                ],
            }
            design_components = (
                _design_component_candidates(page, component_features, page_number, width, height)
                if detection_mode == "placement" else []
            )
            component_match_count = _match_design_components_to_references(
                design_components, weld_candidates, page_references
            )
            candidates = weld_candidates + design_components
            progress(
                f"设计图页面 {page_progress}/{total_pages} 完成："
                f"候选焊口 {len(weld_candidates)} 个，设计图管件 {len(design_components)} 个，"
                f"{'红框直接编号' if detection_mode == 'comparison' else '对照匹配'} {reference.get('acceptedMatchCount', 0)} 个"
            )
            page_result = {
                "page": page_number,
                "width": width,
                "height": height,
                "candidateCount": len(candidates),
                "weldCandidateCount": len(weld_candidates),
                "designComponentCount": len(design_components),
                "componentReferenceMatchedCount": component_match_count,
                "validatedCount": sum(item["glyphValidated"] for item in candidates),
                "referenceMatchedCount": sum(item["referenceMatched"] for item in candidates),
                "drawingCount": int(features.get("drawing_count") or 0),
                "strongProcessSegmentCount": len(features.get("strong_process_segments") or []),
                "anchorSourceStrategy": features.get("anchor_source_strategy"),
                "pcfResearchFile": _reference_display_name(page_pcf["path"]) if page_pcf else None,
                "warnings": _json_safe(features.get("warnings") or []),
                "layoutObstacles": _layout_obstacles(page, features),
                "reference": reference,
                "designComponents": design_components,
                "candidates": candidates,
            }
            if pcf_paths:
                for candidate in page_result["candidates"]:
                    reference_label = str(candidate.get("referenceLabel") or "").strip()
                    candidate["pcfMatched"] = bool(
                        reference_label and reference_label.casefold() in pcf_labels
                    )
                    if candidate["pcfMatched"]:
                        candidate["pcfLabel"] = reference_label
                page_result["pcfValidatedCount"] = sum(
                    item.get("pcfMatched", False) for item in page_result["candidates"]
                )
            pages.append(page_result)
            if page_callback:
                page_callback(_json_safe(page_result))
        result: dict[str, Any] = {
            "schema": "weld-marker.topology.v1",
            "productName": "图纸标识识别系统",
            "productPositioning": "面向图纸拓扑关系的焊口与 EP3D 管件识别、匹配和标识",
            "targetFile": target_pdf.name,
            "pageCount": document.page_count,
            "totalPages": total_pages,
            "analyzedRange": [first, last],
            "referenceMode": (
                ("reference-pdf-folder" if len(reference_paths) > 1 else "reference-pdf") + ("+pcf" if pcf_paths else "")
                if reference_paths else "pcf" if pcf_paths else "vector-only"
            ),
            "referenceFiles": [_reference_display_name(path) for path in reference_paths],
            "detectionMode": detection_mode,
            "symbolConfig": _json_safe(effective_config),
            "ep3dComponentInventory": ep3d_component_inventory,
            "ep3dReferenceInventory": ep3d_reference_inventory,
            "designComponentInventory": _design_component_inventory(pages),
            "pages": pages,
        }
    if pcf_paths:
        result["pcf"] = {
            "file": pcf_results[0]["file"] if len(pcf_results) == 1 else None,
            "files": pcf_results,
            "pipeline": pcf_results[0]["pipeline"] if len(pcf_results) == 1 else None,
            "weldCount": len(all_welds),
            "welds": _json_safe(all_welds),
            "warning": "\n".join(pcf_warnings),
            "degraded": bool(pcf_warnings),
            "parsers": [item.get("parser") for item in pcf_results],
        }
        result["pcf"]["validatedReferenceCount"] = sum(
            int(page.get("pcfValidatedCount") or 0) for page in result["pages"]
        )
    return result


def render_page(pdf_path: Path, page_number: int, output_path: Path, scale: float = 1.6) -> Path:
    with fitz.open(pdf_path) as document:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError("页码超出范围")
        pixmap = document[page_number - 1].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pixmap.save(output_path)
    return output_path


def write_annotated_pdf(
    pdf_path: Path,
    output_path: Path,
    candidates: list[dict[str, Any]],
    embedded_result: dict[str, Any] | None = None,
) -> Path:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in candidates:
        if item.get("included", True):
            grouped.setdefault(int(item["page"]), []).append(item)
    with fitz.open(pdf_path) as document:
        label_font = fitz.Font(fontname="hebo")
        for page_number, items in grouped.items():
            if not (1 <= page_number <= document.page_count):
                continue
            page = document[page_number - 1]
            for item in items:
                point = fitz.Point(float(item["x"]), float(item["y"]))
                number = str(item.get("number") or item.get("referenceLabel") or "?")
                style = item.get("markerStyle") if isinstance(item.get("markerStyle"), dict) else {}
                shape = str(style.get("shape") or "rectangle")
                if shape not in {"circle", "diamond", "rectangle"}:
                    shape = "rectangle"
                frame_size = max(18.0, min(64.0, float(style.get("frameSize") or 28.0)))
                font_size = max(7.0, min(24.0, float(style.get("fontSize") or 10.0)))
                line_width = max(0.5, min(4.0, float(style.get("lineWidth") or 1.35)))
                fill_opacity = max(0.0, min(1.0, float(style.get("fillOpacity", 0.58))))
                color_text = str(style.get("color") or "#d4143c")
                if not re.fullmatch(r"#[0-9a-fA-F]{6}", color_text):
                    color_text = "#d4143c"
                color = tuple(int(color_text[offset:offset + 2], 16) / 255.0 for offset in (1, 3, 5))
                label_point = fitz.Point(
                    float(item.get("labelX", point.x + 24.0)),
                    float(item.get("labelY", point.y - 18.0)),
                )
                box_width = frame_size if shape != "rectangle" else max(frame_size * 1.35, 10.0 + len(number) * font_size * 0.68)
                fitted_font_size = min(font_size, max(4.0, (box_width - 6.0) / max(len(number) * 0.58, 1.0)))
                label_box = fitz.Rect(
                    label_point.x - box_width / 2,
                    label_point.y - frame_size / 2,
                    label_point.x + box_width / 2,
                    label_point.y + frame_size / 2,
                )
                dx, dy = label_point.x - point.x, label_point.y - point.y
                distance = math.hypot(dx, dy)
                if distance > 1e-6:
                    ux, uy = dx / distance, dy / distance
                    if shape == "circle":
                        boundary = frame_size / 2
                    elif shape == "diamond":
                        boundary = (frame_size / 2) / max(abs(ux) + abs(uy), 1e-6)
                    else:
                        boundary = min(
                            (box_width / 2) / abs(ux) if abs(ux) > 1e-6 else float("inf"),
                            (frame_size / 2) / abs(uy) if abs(uy) > 1e-6 else float("inf"),
                        )
                    leader_end = fitz.Point(label_point.x - ux * boundary, label_point.y - uy * boundary)
                    # The research convention starts directly at the weld coordinate:
                    # no endpoint glyph is drawn, so the source weld symbol stays visible.
                    page.draw_line(point, leader_end, color=color, width=max(0.5, line_width * 0.78), stroke_opacity=0.68, overlay=True)
                if shape == "circle":
                    page.draw_circle(label_point, frame_size / 2, color=color, fill=(1, 1, 1), width=line_width, stroke_opacity=0.86, fill_opacity=fill_opacity, overlay=True)
                elif shape == "diamond":
                    radius = frame_size / 2
                    page.draw_polyline([
                        (label_point.x, label_point.y - radius), (label_point.x + radius, label_point.y),
                        (label_point.x, label_point.y + radius), (label_point.x - radius, label_point.y),
                    ], color=color, fill=(1, 1, 1), width=line_width, closePath=True, stroke_opacity=0.86, fill_opacity=fill_opacity, overlay=True)
                else:
                    page.draw_rect(label_box, color=color, fill=(1, 1, 1), width=line_width, stroke_opacity=0.86, fill_opacity=fill_opacity, overlay=True, radius=0.08)
                text_width = label_font.text_length(number, fontsize=fitted_font_size)
                baseline_y = label_point.y + (
                    label_font.ascender + label_font.descender
                ) * fitted_font_size / 2.0
                page.insert_text(
                    fitz.Point(label_point.x - text_width / 2.0, baseline_y),
                    number,
                    fontsize=fitted_font_size,
                    fontname="hebo",
                    color=color,
                    fill_opacity=0.92,
                    overlay=True,
                )
        if embedded_result:
            document.embfile_add(
                "weld-marker-result.json",
                json.dumps(embedded_result, ensure_ascii=False).encode("utf-8"),
                filename="weld-marker-result.json",
                ufilename="焊口标识可编辑数据.json",
                desc="图纸标识识别系统可编辑标识结果",
            )
            document.embfile_add(
                "weld-marker-source.pdf",
                pdf_path.read_bytes(),
                filename="weld-marker-source.pdf",
                ufilename="图纸标识识别系统原始底图.pdf",
                desc="重新编辑标识时使用的无标识原始底图",
            )
        document.save(output_path, garbage=4, deflate=True)
    return output_path


def dump_result(path: Path, result: dict[str, Any]) -> None:
    path.write_text(json.dumps(_json_safe(result), ensure_ascii=False, indent=2), encoding="utf-8")
