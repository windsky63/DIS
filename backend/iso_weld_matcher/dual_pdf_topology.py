"""Topology-oriented weld callout matching between two vector PDF drawings.

The design drawing used by the Indonesia pilot contains red manual ``F``
callouts.  EP3D uses a black diamond callout.  In both cases the label is not
the weld location: the opposite end of the attached leader is the location.
This module extracts those locations without OCR and matches the two point
sets without treating the displayed weld number as identity.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Iterable

import fitz
import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.sparse.csgraph import minimum_spanning_tree

from .geometry_scale import estimate_vector_scale


# Indonesia project semantics:
#   design F / FS / RP = red manual weld identifiers
#   EP3D F / FS / T = framed construction weld identifiers
#   SP = support, FL = flange-management identifier, V = valve identifier
# Non-weld identifiers are intentionally excluded before topology matching.
WELD_LABEL = re.compile(r"(?:F|FS|RP)\d+", re.IGNORECASE)
# The 301800 formal ISO uses red ``S`` callouts for shop welds while EP3D
# renders the corresponding construction identifier as ``FS``.  Keep this
# policy separate from the 301100 default because black ``S`` identifiers in
# the drawing area can also denote supports; the design extractor additionally
# requires the text and leader to be red.
INDONESIA_301800_DESIGN_WELD_LABEL = re.compile(r"(?:F|S)\d+", re.IGNORECASE)
# Indonesia EP3D also uses T-prefixed shop / tack weld identifiers on some
# PW sheets.  Design manual callouts remain F/FS, so keep the two policies
# separate and require the normal framed-leader-solid-dot vector signature.
EP3D_WELD_LABEL = re.compile(r"(?:F|FS|T)\d+", re.IGNORECASE)
ISOMETRIC_DRAWING_NO = re.compile(
    r"\d{2}-\d{2}-\d{4}-\d{2}-(?:[0-9A-Z_/]+-)+\d{2}", re.IGNORECASE
)


@dataclass(frozen=True)
class PdfWeldCallout:
    label: str
    label_bbox: tuple[float, float, float, float]
    label_center: tuple[float, float]
    weld_point: tuple[float, float]
    leader_start: tuple[float, float]
    leader_end: tuple[float, float]
    extraction_method: str
    extraction_confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Ep3dCalloutProfile:
    """Project adapter for EP3D number frames and leader-root semantics."""

    name: str = "indonesia-solid-dot"
    label_pattern: str = r"(?:F|FS|T)\d+"
    container_shapes: tuple[str, ...] = ("diamond", "circle", "ellipse")
    root_strategy: str = "solid-dot-required"
    minimum_leader_length: float = 8.0
    maximum_leader_length: float = 180.0
    minimum_container_extent: float = 8.0
    maximum_container_width: float = 90.0
    maximum_container_height: float = 60.0


INDONESIA_EP3D_CALLOUT_PROFILE = Ep3dCalloutProfile()


def extract_isometric_drawing_number(page: fitz.Page) -> str | None:
    """Read the authoritative lower-right Isometric drawing No field.

    Continuation references inside the process area contain the same style of
    identifier, so accepting any occurrence would pair a page to its neighbor.
    The title block position is therefore a hard part of the signature.
    """

    candidates = []
    for word in page.get_text("words"):
        value = str(word[4]).strip().upper()
        if not ISOMETRIC_DRAWING_NO.fullmatch(value):
            continue
        if float(word[0]) < float(page.rect.width) * 0.60:
            continue
        if float(word[1]) < float(page.rect.height) * 0.82:
            continue
        candidates.append((float(word[1]), float(word[0]), value))
    return max(candidates)[2] if candidates else None


def extract_stable_landmarks(page: fitz.Page) -> list[dict[str, Any]]:
    """Extract unique engineering-coordinate and equipment text landmarks."""

    values: dict[str, list[tuple[float, float]]] = {}
    for word in page.get_text("words"):
        raw = re.sub(r"[^A-Z0-9]", "", str(word[4]).upper())
        is_coordinate = bool(re.fullmatch(r"[79]\d{6}", raw))
        is_equipment = bool(re.fullmatch(r"\d+[A-Z]{2,}\d+[A-Z0-9]*", raw))
        if not (is_coordinate or is_equipment):
            continue
        center = ((float(word[0]) + float(word[2])) / 2.0, (float(word[1]) + float(word[3])) / 2.0)
        values.setdefault(raw, []).append(center)
    return [
        {"key": key, "paper_coordinate": list(points[0])}
        for key, points in sorted(values.items())
        if len(points) == 1
    ]


def stable_landmark_orientation_audit(
    design_landmarks: Iterable[dict[str, Any]], ep3d_landmarks: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    design = {item["key"]: item["paper_coordinate"] for item in design_landmarks}
    ep3d = {item["key"]: item["paper_coordinate"] for item in ep3d_landmarks}
    common = sorted(set(design) & set(ep3d))
    agreements = 0
    comparisons = 0
    for left_index, left in enumerate(common):
        for right in common[left_index + 1 :]:
            for axis in range(2):
                design_delta = float(design[right][axis]) - float(design[left][axis])
                ep3d_delta = float(ep3d[right][axis]) - float(ep3d[left][axis])
                if abs(design_delta) < 8.0 or abs(ep3d_delta) < 8.0:
                    continue
                comparisons += 1
                agreements += (design_delta > 0) == (ep3d_delta > 0)
    return {
        "common_landmark_keys": common,
        "common_landmark_count": len(common),
        "axis_order_comparison_count": comparisons,
        "axis_order_agreement": round(agreements / comparisons, 3) if comparisons else None,
    }


def _point_segment_distance_raw(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-12:
        return math.dist(point, start)
    fraction = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator))
    return math.dist(point, (start[0] + fraction * dx, start[1] + fraction * dy))


def extract_process_skeleton(page: fitz.Page) -> dict[str, Any]:
    """Recover high-confidence coarse process axes from heavy strokes/bands.

    This intentionally returns a coarse skeleton.  Component gaps remain
    visible and are useful evidence; dimension and leader strokes are not
    allowed to create additional process branches.
    """

    width, height = float(page.rect.width), float(page.rect.height)
    segments: list[dict[str, Any]] = []
    for drawing_index, drawing in enumerate(page.get_drawings()):
        color, fill = drawing.get("color"), drawing.get("fill")
        dark_stroke = bool(color is not None and len(color) >= 3 and max(float(v) for v in color[:3]) <= 0.2)
        dark_fill = bool(fill is not None and len(fill) >= 3 and max(float(v) for v in fill[:3]) <= 0.2)
        stroke_width = float(drawing.get("width") or 0.0)
        if dark_stroke and stroke_width >= 1.45:
            for item in drawing.get("items", []):
                if not item or item[0] != "l":
                    continue
                start, end = _display_point(page, item[1]), _display_point(page, item[2])
                length = math.dist(start, end)
                midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
                if length >= 1.2 and midpoint[0] <= width * 0.72 and midpoint[1] <= height * 0.78:
                    segments.append({"start": list(start), "end": list(end), "length": length, "source": "heavy-stroke", "drawing_index": drawing_index})
        if not dark_fill:
            continue
        points: list[tuple[float, float]] = []
        for item in drawing.get("items", []):
            if item and item[0] == "l":
                points.extend((_display_point(page, item[1]), _display_point(page, item[2])))
        if len(points) < 4:
            continue
        array = np.asarray(points, dtype=float)
        center = np.mean(array, axis=0)
        covariance = np.cov((array - center).T)
        values, vectors = np.linalg.eigh(covariance)
        major = vectors[:, int(np.argmax(values))]
        minor = np.asarray((-major[1], major[0]))
        major_projection = (array - center) @ major
        minor_projection = (array - center) @ minor
        length = float(np.ptp(major_projection))
        thickness = float(np.ptp(minor_projection))
        if not (length >= 18.0 and 0.35 <= thickness <= 8.0 and length / max(thickness, 1e-6) >= 5.0):
            continue
        start = center + major * float(np.min(major_projection))
        end = center + major * float(np.max(major_projection))
        midpoint = (start + end) / 2.0
        if midpoint[0] > width * 0.72 or midpoint[1] > height * 0.78:
            continue
        segments.append(
            {
                "start": start.tolist(), "end": end.tolist(), "length": length,
                "thickness": thickness, "source": "filled-band-pca", "drawing_index": drawing_index,
            }
        )
    return {
        "segments": segments,
        "segment_count": len(segments),
        "heavy_stroke_count": sum(item["source"] == "heavy-stroke" for item in segments),
        "filled_band_count": sum(item["source"] == "filled-band-pca" for item in segments),
    }


def _rank_coordinates(points: np.ndarray) -> np.ndarray:
    result = np.zeros_like(points, dtype=float)
    for axis in range(2):
        order = np.argsort(points[:, axis], kind="stable")
        ranks = np.empty(len(points), dtype=float)
        ranks[order] = np.arange(len(points), dtype=float)
        result[:, axis] = ranks / max(1, len(points) - 1)
    return result


def _mst_addresses(points: np.ndarray) -> list[dict[str, Any]]:
    distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    tree = minimum_spanning_tree(distances).toarray()
    adjacency = [set() for _ in range(len(points))]
    for left, right in zip(*np.nonzero(tree)):
        adjacency[int(left)].add(int(right))
        adjacency[int(right)].add(int(left))
    ranks = _rank_coordinates(points)
    return [
        {
            "mst_degree": len(adjacency[index]),
            "mst_neighbours": sorted(adjacency[index]),
            "x_rank": round(float(ranks[index, 0]), 4),
            "y_rank": round(float(ranks[index, 1]), 4),
        }
        for index in range(len(points))
    ]


def _display_point(page: fitz.Page, point: fitz.Point) -> tuple[float, float]:
    mapped = point * page.rotation_matrix
    return float(mapped.x), float(mapped.y)


def _display_bbox(page: fitz.Page, rect: fitz.Rect) -> tuple[float, float, float, float]:
    points = [
        _display_point(page, fitz.Point(rect.x0, rect.y0)),
        _display_point(page, fitz.Point(rect.x1, rect.y0)),
        _display_point(page, fitz.Point(rect.x0, rect.y1)),
        _display_point(page, fitz.Point(rect.x1, rect.y1)),
    ]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _point_rect_distance(point: tuple[float, float], rect: tuple[float, float, float, float]) -> float:
    x, y = point
    x0, y0, x1, y1 = rect
    return math.hypot(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1))


def _line_primitives(page: fitz.Page, *, red_only: bool = False) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for drawing_index, drawing in enumerate(page.get_drawings()):
        color = drawing.get("color")
        if red_only and not (
            color is not None
            and len(color) >= 3
            and float(color[0]) >= 0.8
            and float(color[1]) <= 0.25
            and float(color[2]) <= 0.25
        ):
            continue
        for item_index, item in enumerate(drawing.get("items", [])):
            if not item or item[0] != "l":
                continue
            start = _display_point(page, item[1])
            end = _display_point(page, item[2])
            lines.append(
                {
                    "drawing_index": drawing_index,
                    "item_index": item_index,
                    "start": start,
                    "end": end,
                    "length": math.dist(start, end),
                }
            )
    return lines


def _red_label_spans(
    page: fitz.Page,
    *,
    label_pattern: re.Pattern[str] = WELD_LABEL,
) -> list[tuple[str, tuple[float, float, float, float]]]:
    labels: list[tuple[str, tuple[float, float, float, float]]] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                label = str(span.get("text", "")).strip().upper()
                color = int(span.get("color", 0))
                if label_pattern.fullmatch(label) and color == 0xFF0000:
                    labels.append((label, tuple(float(value) for value in span["bbox"])))
    return labels


def _design_leader_points_outward(
    bbox: tuple[float, float, float, float],
    center: tuple[float, float],
    near: tuple[float, float],
    far: tuple[float, float],
) -> bool:
    """Validate that a red line leaves a weld-number frame toward the weld.

    Most manual leaders are long enough that the far endpoint is plainly
    farther from the label centre.  A short oblique leader can leave a corner
    of a rectangular frame while remaining almost tangent to the frame; its
    centre-distance gain is then smaller than the historical four-point gate
    (Indonesia P17 FS8/FS10).  Such a leader is still unambiguous when one end
    touches the frame and the other lies at least five points outside it.

    The second branch is deliberately restricted to an almost exact frame
    attachment.  It therefore cannot turn a nearby independent red stroke
    into a leader merely because it happens to point away from the label.
    """

    centre_gain = math.dist(center, far) - math.dist(center, near)
    if centre_gain > 4.0:
        return True
    return (
        _point_rect_distance(near, bbox) <= 1.25
        and _point_rect_distance(far, bbox) >= 5.0
    )


def extract_design_weld_callouts(
    page: fitz.Page,
    *,
    label_pattern: re.Pattern[str] = WELD_LABEL,
) -> list[PdfWeldCallout]:
    """Extract red manual callouts and use the far leader end as weld point."""

    lines = _line_primitives(page, red_only=True)
    callouts: list[PdfWeldCallout] = []
    for label, bbox in _red_label_spans(page, label_pattern=label_pattern):
        center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        candidates = []
        for line in lines:
            # A small rectangular callout can use a five-point leader whose
            # far endpoint is already the weld dot (P111 FS1).  Frame sides
            # are emitted as ``re`` primitives and never enter ``lines``, so
            # the exact frame-attachment test below safely admits this case.
            if line["length"] < 5.0:
                continue
            start_distance = _point_rect_distance(line["start"], bbox)
            end_distance = _point_rect_distance(line["end"], bbox)
            near_distance = min(start_distance, end_distance)
            if near_distance > 3.5:
                continue
            near = line["start"] if start_distance <= end_distance else line["end"]
            far = line["end"] if start_distance <= end_distance else line["start"]
            if not _design_leader_points_outward(bbox, center, near, far):
                continue
            candidates.append((near_distance, -line["length"], near, far))
        if not candidates:
            continue
        near_distance, _, near, far = min(candidates)
        callouts.append(
            PdfWeldCallout(
                label=label,
                label_bbox=bbox,
                label_center=center,
                weld_point=far,
                leader_start=near,
                leader_end=far,
                extraction_method="red-text-attached-red-leader",
                extraction_confidence=round(max(0.75, 1.0 - near_distance / 14.0), 3),
            )
        )
    return sorted(callouts, key=_label_sort_key)


def _label_sort_key(item: PdfWeldCallout) -> tuple[str, int]:
    return _label_text_sort_key(item.label)


def _label_text_sort_key(label: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", label)
    return (match.group(1), int(match.group(2))) if match else (label, 0)


def _callout_container_candidates(
    page: fitz.Page,
    profile: Ep3dCalloutProfile = INDONESIA_EP3D_CALLOUT_PROFILE,
) -> list[dict[str, Any]]:
    distance_scale = estimate_vector_scale(page).threshold_scale
    containers: list[dict[str, Any]] = []
    for drawing_index, drawing in enumerate(page.get_drawings()):
        rect = drawing.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = _display_bbox(page, rect)
        center_x, center_y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if center_x > float(page.rect.width) * 0.72 or center_y > float(page.rect.height) * 0.78:
            continue
        if not (
            profile.minimum_container_extent * distance_scale <= x1 - x0 <= profile.maximum_container_width * distance_scale
            and profile.minimum_container_extent * distance_scale <= y1 - y0 <= profile.maximum_container_height * distance_scale
        ):
            continue
        aspect = (x1 - x0) / max(y1 - y0, 1e-6)
        # EP3D weld-number frames may be diamonds, circles or ellipses.  Keep
        # all three styles and reject only clearly elongated material tags
        # such as ``F3 G4 B5``.  The real hard gate is the solid weld dot at
        # the leader root (see ``_ep3d_filled_dot_root`` below).
        if not 0.42 <= aspect <= 2.40:
            continue
        items = list(drawing.get("items", []))
        kinds = [item[0] for item in items if item]
        shape = (
            "circle" if "c" in kinds and 0.75 <= aspect <= 1.33
            else "ellipse" if "c" in kinds
            else "diamond"
        )
        if shape in profile.container_shapes and ("qu" in kinds or "c" in kinds or kinds.count("l") >= 4):
            points: list[tuple[float, float]] = []
            for item in items:
                if item and item[0] == "l":
                    points.extend((_display_point(page, item[1]), _display_point(page, item[2])))
            containers.append(
                {
                    "drawing_index": drawing_index,
                    "bbox": (x0, y0, x1, y1),
                    "line_points": points,
                    "shape": shape,
                }
            )
    return containers


def _ep3d_plot_labels(
    page: fitz.Page,
    profile: Ep3dCalloutProfile = INDONESIA_EP3D_CALLOUT_PROFILE,
    allowed_labels: Iterable[str] | None = None,
) -> list[tuple[str, tuple[float, float, float, float]]]:
    # The right-hand weld list repeats every F number.  Plot labels are in the
    # left process area and are filtered by a diamond/circle/ellipse frame.
    labels = []
    allowed = {str(label).strip().upper() for label in allowed_labels} if allowed_labels is not None else None
    pattern = re.compile(profile.label_pattern, re.IGNORECASE)
    for word in page.get_text("words"):
        label = str(word[4]).strip().upper()
        if (allowed is not None and label not in allowed) or (allowed is None and not pattern.fullmatch(label)):
            continue
        labels.append((label, _display_bbox(page, fitz.Rect(*word[:4]))))
    return labels


def _ep3d_filled_dot_root(
    point: tuple[float, float],
    dark_fills: list[tuple[tuple[float, float], int]],
    distance_scale: float = 1.0,
) -> tuple[tuple[float, float], dict[str, Any]] | None:
    """Validate and centre an Indonesia EP3D solid weld dot.

    The printer emits a nominal circle as many narrow filled vector strips.
    A dense local cluster is consequently much more stable than any one path
    rectangle and is rotation-independent.  Flow arrows cannot enter here on
    their own: this check is applied only after a valid framed callout leader
    has supplied a root candidate.
    """

    nearby = [item for item in dark_fills if math.dist(point, item[0]) <= 7.0 * distance_scale]
    # Two weld dots can be only 6--7 pt apart.  Treat the printer's adjacent
    # fill strips as connected components instead of averaging the whole
    # search disc, otherwise neighbouring dots pull both roots toward their
    # midpoint.
    components: list[list[tuple[tuple[float, float], int]]] = []
    remaining = set(range(len(nearby)))
    while remaining:
        seed = remaining.pop()
        indices = [seed]
        queue = [seed]
        while queue:
            current = queue.pop()
            attached = [
                index for index in remaining
                if math.dist(nearby[current][0], nearby[index][0]) <= 1.2 * distance_scale
            ]
            for index in attached:
                remaining.remove(index)
                indices.append(index)
                queue.append(index)
        components.append([nearby[index] for index in indices])
    valid = [
        component for component in components
        if len(component) >= 8 and sum(item[1] for item in component) >= 30
    ]
    if not valid:
        return None
    nearby = min(
        valid,
        key=lambda component: math.dist(
            point,
            tuple(
                sum(item[0][axis] * max(1, item[1]) for item in component)
                / sum(max(1, item[1]) for item in component)
                for axis in range(2)
            ),
        ),
    )
    primitive_count = sum(item[1] for item in nearby)
    total_weight = sum(max(1, item[1]) for item in nearby)
    center = tuple(
        sum(item[0][axis] * max(1, item[1]) for item in nearby) / total_weight
        for axis in range(2)
    )
    if math.dist(point, center) > 3.5 * distance_scale:
        # Nearby text/arrow fill dominated the cluster rather than a dot at
        # the leader root.
        return None
    return center, {
        "glyph": "indonesia-solid-weld-dot",
        "filled_path_count": len(nearby),
        "filled_primitive_count": primitive_count,
        "root_snap_distance": round(math.dist(point, center), 3),
    }


def _ep3d_root_candidate(
    point: tuple[float, float],
    *,
    profile: Ep3dCalloutProfile,
    dark_fills: list[tuple[tuple[float, float], int]],
    process_segments: list[dict[str, Any]],
    distance_scale: float,
) -> tuple[tuple[float, float], str, float] | None:
    dot = _ep3d_filled_dot_root(point, dark_fills, distance_scale)
    if dot is not None:
        return dot[0], "solid-weld-dot", 0.94
    if profile.root_strategy == "solid-dot-required":
        return None
    if profile.root_strategy not in {"leader-end-on-process", "symbol-or-process-end"}:
        raise ValueError(f"Unsupported EP3D root strategy: {profile.root_strategy}")
    ranked = sorted(
        [
            (
                _point_segment_distance_raw(
                    point, tuple(segment["start"]), tuple(segment["end"])
                ),
                segment,
            )
            for segment in process_segments
        ],
        key=lambda item: item[0],
    )
    if not ranked or ranked[0][0] > 8.0 * distance_scale:
        return None
    # Retain the leader end itself: component gaps mean the physical weld is
    # often deliberately a few points away from either adjacent process axis.
    return point, "leader-end-near-process-skeleton", max(0.68, 0.88 - ranked[0][0] / (40.0 * distance_scale))


def _parse_nominal_pipe_size(value: str) -> float | None:
    """Return a comparable inch value for an EP3D weld-list size cell."""

    normalized = (
        str(value)
        .strip()
        .replace("\u2033", "")
        .replace('"', "")
        .replace("''", "")
    )
    if not normalized:
        return None
    compact = normalized.replace(" ", "-")
    mixed = re.fullmatch(r"(\d+)-(\d+)/(\d+)", compact)
    if mixed:
        whole, numerator, denominator = (int(item) for item in mixed.groups())
        return whole + numerator / denominator if denominator else None
    fraction = re.fullmatch(r"(\d+)/(\d+)", compact)
    if fraction:
        numerator, denominator = (int(item) for item in fraction.groups())
        return numerator / denominator if denominator else None
    try:
        return float(compact)
    except ValueError:
        return None


def _parse_ep3d_weld_list_lines(lines: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Parse EP3D rows emitted as joint, type, F/S, size and label cells.

    The label only attaches a row to an EP3D callout on the same page.  It is
    never compared with a design-side weld number.
    """

    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    result: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(cleaned):
        label = value.upper()
        if index < 4 or EP3D_WELD_LABEL.fullmatch(label) is None:
            continue
        joint_no, weld_type, fabrication, nominal_size = cleaned[index - 4:index]
        weld_type = weld_type.upper()
        fabrication = fabrication.upper()
        if re.fullmatch(r"[A-Z0-9.]+-[A-Z0-9.]+", joint_no.upper()) is None:
            continue
        if re.fullmatch(r"[A-Z]{2,5}", weld_type) is None:
            continue
        if fabrication not in {"FIELD", "SHOP"}:
            continue
        size_in = _parse_nominal_pipe_size(nominal_size)
        if size_in is None:
            continue
        result[label] = {
            "joint_no": joint_no.upper(),
            "weld_type": weld_type,
            "fabrication": fabrication,
            "nominal_size": nominal_size,
            "nominal_size_in": size_in,
            "source": "ep3d-weld-list-native-text",
        }
    return result


def extract_ep3d_weld_list_semantics(page: fitz.Page) -> dict[str, dict[str, Any]]:
    """Extract weld type, fabrication class and size from the page table."""

    headings = page.search_for("WELD LIST")
    if not headings:
        return {}
    page_rect = page.rect
    heading = min(headings, key=lambda rect: (rect.y0, rect.x0))
    clip = fitz.Rect(
        max(page_rect.x0, heading.x0 - 0.09 * page_rect.width),
        max(page_rect.y0, heading.y0 - 0.06 * page_rect.height),
        page_rect.x1 - 0.02 * page_rect.width,
        min(page_rect.y1, heading.y0 + 0.25 * page_rect.height),
    )
    return _parse_ep3d_weld_list_lines(page.get_text("text", clip=clip).splitlines())


def extract_ep3d_weld_callouts(
    page: fitz.Page,
    *,
    allowed_labels: Iterable[str] | None = None,
    profile: Ep3dCalloutProfile = INDONESIA_EP3D_CALLOUT_PROFILE,
    label_candidates: Iterable[tuple[str, tuple[float, float, float, float]]] | None = None,
) -> list[PdfWeldCallout]:
    """Extract project-configurable EP3D number frames and leader roots.

    ``label_candidates`` is the adapter boundary for outline-font OCR or a
    vector-glyph recognizer.  Its bounding boxes must already be in display
    coordinates.  Native PDF text remains the zero-dependency default.
    """

    distance_scale = estimate_vector_scale(page).threshold_scale
    lines = _line_primitives(page)
    containers = _callout_container_candidates(page, profile)
    process_segments = extract_process_skeleton(page)["segments"]
    dark_fills = []
    for drawing in page.get_drawings():
        rect, fill = drawing.get("rect"), drawing.get("fill")
        if rect is None or fill is None or len(fill) < 3 or max(float(value) for value in fill[:3]) > 0.12:
            continue
        display_rect = _display_bbox(page, rect)
        if display_rect[2] - display_rect[0] > 16.0 * distance_scale or display_rect[3] - display_rect[1] > 16.0 * distance_scale:
            continue
        dark_fills.append(
            (
                ((display_rect[0] + display_rect[2]) / 2.0, (display_rect[1] + display_rect[3]) / 2.0),
                len(drawing.get("items", [])),
            )
        )
    callouts: list[PdfWeldCallout] = []
    seen: set[str] = set()
    labels = list(label_candidates) if label_candidates is not None else _ep3d_plot_labels(page, profile, allowed_labels)
    allowed = {str(label).strip().upper() for label in allowed_labels} if allowed_labels is not None else None
    for raw_label, bbox in labels:
        label = str(raw_label).strip().upper()
        if allowed is not None and label not in allowed:
            continue
        center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        enclosing = [
            container
            for container in containers
            if container["bbox"][0] - 3.0 * distance_scale <= center[0] <= container["bbox"][2] + 3.0 * distance_scale
            and container["bbox"][1] - 3.0 * distance_scale <= center[1] <= container["bbox"][3] + 3.0 * distance_scale
        ]
        if not enclosing or label in seen:
            continue
        container = min(
            enclosing,
            key=lambda item: math.dist(
                center,
                ((item["bbox"][0] + item["bbox"][2]) / 2.0, (item["bbox"][1] + item["bbox"][3]) / 2.0),
            ),
        )
        internal_points = container.get("line_points", [])
        far_internal = max(internal_points, key=lambda point: math.dist(center, point), default=None)
        bbox = container["bbox"]
        label_radius = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) / 2.0
        if far_internal is not None and math.dist(center, far_internal) >= label_radius + 8.0 * distance_scale:
            root = _ep3d_root_candidate(
                far_internal,
                profile=profile,
                dark_fills=dark_fills,
                process_segments=process_segments,
                distance_scale=distance_scale,
            )
            if root is not None:
                root_point, root_method, root_confidence = root
                callouts.append(
                    PdfWeldCallout(
                        label=label,
                        label_bbox=bbox,
                        label_center=center,
                        weld_point=root_point,
                        leader_start=center,
                        leader_end=root_point,
                        extraction_method=f"compound-label-frame-with-integrated-leader-and-{root_method}",
                        extraction_confidence=root_confidence,
                    )
                )
                seen.add(label)
                continue
            # A long frame stroke can look like an integrated leader.  If its
            # far point fails the physical-root gate, fall through and inspect
            # the separately emitted leader paths instead of dropping the
            # already recognized label.
        candidates = []
        for line in lines:
            if not profile.minimum_leader_length * distance_scale <= line["length"] <= profile.maximum_leader_length * distance_scale:
                continue
            start_distance = _point_rect_distance(line["start"], container["bbox"])
            end_distance = _point_rect_distance(line["end"], container["bbox"])
            near_distance = min(start_distance, end_distance)
            if near_distance > 5.0 * distance_scale:
                continue
            near = line["start"] if start_distance <= end_distance else line["end"]
            far = line["end"] if start_distance <= end_distance else line["start"]
            # Compact diamonds/circles can sit only 2--4 pt from the weld
            # dot. The solid-dot / process-root gate below is the reliable
            # discriminator; a 4 pt exclusion discarded legitimate short
            # leaders such as high-end-sulfonation callout #1.
            if _point_rect_distance(far, container["bbox"]) <= 0.8 * distance_scale:
                continue
            # EP3D emits the leader and diamond next to one another in the
            # drawing stream.  This rejects unrelated process/dimension lines
            # crossing the diamond.
            stream_gap = abs(int(line["drawing_index"]) - int(container["drawing_index"]))
            candidates.append((stream_gap, near_distance, line["length"], near, far))
        if not candidates:
            continue
        chosen = None
        for stream_gap, near_distance, line_length, near, far in sorted(candidates):
            root = _ep3d_root_candidate(
                far,
                profile=profile,
                dark_fills=dark_fills,
                process_segments=process_segments,
                distance_scale=distance_scale,
            )
            if root is not None:
                chosen = (stream_gap, near_distance, line_length, near, far, root)
                break
        if chosen is None:
            continue
        stream_gap, near_distance, _, near, far, root = chosen
        root_point, root_method, root_confidence = root
        callouts.append(
            PdfWeldCallout(
                label=label,
                label_bbox=bbox,
                label_center=center,
                weld_point=root_point,
                leader_start=near,
                leader_end=root_point,
                extraction_method=f"framed-text-attached-{profile.name}-{root_method}",
                extraction_confidence=round(
                    min(root_confidence, max(0.65, 1.0 - min(stream_gap, 20) / 80.0 - near_distance / (30.0 * distance_scale))), 3
                ),
            )
        )
        seen.add(label)
    return sorted(callouts, key=_label_sort_key)


def _bbox_transform(source: np.ndarray, target: np.ndarray, swap: bool, mirror_x: bool, mirror_y: bool) -> np.ndarray:
    work = source[:, ::-1] if swap else source.copy()
    source_low = np.quantile(work, 0.04, axis=0)
    source_high = np.quantile(work, 0.96, axis=0)
    target_low = np.quantile(target, 0.04, axis=0)
    target_high = np.quantile(target, 0.96, axis=0)
    scale = (target_high - target_low) / np.maximum(source_high - source_low, 1e-6)
    if mirror_x:
        scale[0] *= -1.0
        source_low[0], source_high[0] = source_high[0], source_low[0]
    if mirror_y:
        scale[1] *= -1.0
        source_low[1], source_high[1] = source_high[1], source_low[1]
    offset = target_low - source_low * scale
    matrix = np.zeros((3, 2), dtype=float)
    if swap:
        matrix[1, 0], matrix[0, 1] = scale[0], scale[1]
    else:
        matrix[0, 0], matrix[1, 1] = scale[0], scale[1]
    matrix[2] = offset
    return matrix


def _apply_affine(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return np.column_stack((points, np.ones(len(points)))) @ matrix


def _fit_affine(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    design = np.column_stack((source, np.ones(len(source))))
    return np.linalg.lstsq(design, target, rcond=None)[0]


def _nearest_topology_score(source: np.ndarray, target: np.ndarray, pairs: list[tuple[int, int]]) -> dict[int, float]:
    if len(pairs) <= 2:
        return {left: 0.0 for left, _ in pairs}
    source_to_target = {left: right for left, right in pairs}
    scores: dict[int, float] = {}
    for left, right in pairs:
        source_neighbours = [
            index for index in np.argsort(np.linalg.norm(source - source[left], axis=1)) if index in source_to_target and index != left
        ][:3]
        target_neighbours = [
            index for index in np.argsort(np.linalg.norm(target - target[right], axis=1)) if index in set(source_to_target.values()) and index != right
        ][:3]
        mapped = {source_to_target[index] for index in source_neighbours}
        scores[left] = len(mapped & set(target_neighbours)) / max(1, min(3, len(source_neighbours)))
    return scores


def _skeleton_binding(
    point: tuple[float, float], skeleton: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Bind a callout leader root to the nearest recovered process segment."""

    segments = (skeleton or {}).get("segments", [])
    if not segments:
        return None
    ranked = []
    for index, segment in enumerate(segments):
        start, end = tuple(segment["start"]), tuple(segment["end"])
        dx, dy = end[0] - start[0], end[1] - start[1]
        denominator = dx * dx + dy * dy
        raw_fraction = 0.0 if denominator <= 1e-12 else (
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator
        )
        fraction = max(0.0, min(1.0, raw_fraction))
        snapped = (start[0] + fraction * dx, start[1] + fraction * dy)
        ranked.append((math.dist(point, snapped), index, raw_fraction, fraction, snapped, segment))
    distance, index, raw_fraction, fraction, snapped, segment = min(ranked)
    return {
        "segment_index": index,
        "distance": round(float(distance), 3),
        "raw_fraction": round(float(raw_fraction), 4),
        "fraction": round(float(fraction), 4),
        "snapped_coordinate": [round(float(value), 3) for value in snapped],
        "segment_source": segment.get("source"),
    }


def _unit_vector(vector: tuple[float, float] | list[float]) -> tuple[float, float] | None:
    length = math.hypot(float(vector[0]), float(vector[1]))
    if length <= 1e-9:
        return None
    return float(vector[0]) / length, float(vector[1]) / length


def _directed_angle_error(left: float, right: float) -> float:
    difference = abs(left - right) % 360.0
    return min(difference, 360.0 - difference)


def _dedupe_port_rays(rays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ray in sorted(rays, key=lambda item: float(item["distance_pt"])):
        if any(
            _directed_angle_error(float(ray["angle_deg"]), float(other["angle_deg"])) <= 8.0
            for other in result
        ):
            continue
        result.append(ray)
    return result


def _nearby_continuation_reference(
    page: fitz.Page | None,
    point: tuple[float, float],
    references: list[tuple[tuple[float, float], str, tuple[float, float, float, float]]] | None = None,
    *,
    maximum_bbox_distance: float = 20.0,
    follow_pointer: bool = False,
    pointer_segments: list[
        tuple[tuple[float, float], tuple[float, float], float]
    ] | None = None,
) -> str | None:
    """Return only a drawing reference directly attached to a process root.

    The previous 150 pt centre-distance rule turned every one-ray weld in a
    compact elbow group into a continuation terminal when a ``SEE ISO`` note
    happened to be printed beside the group.  A continuation reference is now
    evidence only when the root touches the reference word's local attachment
    envelope.  The later compact-component refinement gets first refusal: a
    root bridged to another weld is a physical component port, not a page gap.
    """

    if page is None:
        return None
    if references is None:
        references = []
        for word in page.get_text("words"):
            value = str(word[4]).strip().upper()
            if "1100-26-" not in value or not value.startswith("30-"):
                continue
            center = ((float(word[0]) + float(word[2])) / 2.0, (float(word[1]) + float(word[3])) / 2.0)
            if center[0] >= float(page.rect.width) * 0.60 and center[1] >= float(page.rect.height) * 0.82:
                continue
            references.append((center, value, tuple(float(value) for value in word[:4])))

    def bbox_distance(bbox: tuple[float, float, float, float]) -> float:
        dx = max(float(bbox[0]) - point[0], 0.0, point[0] - float(bbox[2]))
        dy = max(float(bbox[1]) - point[1], 0.0, point[1] - float(bbox[3]))
        return math.hypot(dx, dy)

    ranked = [
        (bbox_distance(bbox), math.dist(point, center), value)
        for center, value, bbox in references
        # A continuation root is sometimes printed just outside the text
        # bounding box because the solid weld dot and its short tick occupy
        # the intervening space.  The old 20 pt cut-off missed these directly
        # adjacent notes (Indonesia P143 FS1 measured 22.63 pt).  Keep this a
        # tight local envelope: it is still far smaller than the retired
        # 150 pt centre-distance heuristic that contaminated elbow groups.
        if bbox_distance(bbox) <= maximum_bbox_distance
    ]
    if ranked:
        return min(ranked)[2]
    if not follow_pointer:
        return None

    # Some design sheets place the SEE ISO block well away from the open pipe
    # end and attach it with a two-segment pointer (P12 F1).  Trace only thin,
    # long annotation strokes whose first endpoint actually touches the weld
    # root and whose last endpoint reaches a drawing-number word.  This avoids
    # reviving the old broad text-proximity heuristic.
    segments = pointer_segments
    if segments is None:
        segments = []
        for drawing in page.get_drawings():
            color = drawing.get("color")
            if color is not None and len(color) >= 3 and (
                float(color[0]) >= 0.8 and float(color[1]) <= 0.25 and float(color[2]) <= 0.25
            ):
                continue
            width = float(drawing.get("width") or 0.0)
            if width > 1.2:
                continue
            for item in drawing.get("items", []):
                if not item or item[0] != "l":
                    continue
                start, end = _display_point(page, item[1]), _display_point(page, item[2])
                length = math.dist(start, end)
                if 18.0 <= length <= 180.0:
                    segments.append((start, end, length))

    def endpoint_bbox_distance(
        endpoint: tuple[float, float], bbox: tuple[float, float, float, float]
    ) -> float:
        dx = max(float(bbox[0]) - endpoint[0], 0.0, endpoint[0] - float(bbox[2]))
        dy = max(float(bbox[1]) - endpoint[1], 0.0, endpoint[1] - float(bbox[3]))
        return math.hypot(dx, dy)

    candidates: list[tuple[int, float, float, str]] = []
    frontier: list[tuple[tuple[float, float], frozenset[int], float, int]] = [
        (point, frozenset(), 0.0, 0)
    ]
    while frontier:
        current, used, total_length, depth = frontier.pop(0)
        if depth >= 3:
            continue
        for index, (start, end, length) in enumerate(segments):
            if index in used:
                continue
            if math.dist(current, start) <= 4.5:
                next_point = end
            elif math.dist(current, end) <= 4.5:
                next_point = start
            else:
                continue
            next_total = total_length + length
            next_depth = depth + 1
            if next_total > 260.0:
                continue
            for center, value, bbox in references:
                if not re.search(r"-(?:\d{2}|\d+/\d+)$", value):
                    # A bare line identifier also appears in spool/component
                    # captions near ordinary welds.  Only an explicit sheet
                    # suffix is a valid remote SEE ISO pointer target.
                    continue
                terminal_distance = endpoint_bbox_distance(next_point, bbox)
                if (
                    terminal_distance <= 12.0
                    and next_total >= 60.0
                    and (
                        next_depth >= 2
                        or length >= 90.0
                        # A single, plainly attached pointer is also
                        # conclusive when its remote end almost touches the
                        # explicit sheet-reference word.  P109 F6 is a 77 pt
                        # one-piece pointer with a 7 pt terminal gap.  Requiring
                        # 90 pt discarded that genuine open-page interface.
                        or (length >= 70.0 and terminal_distance <= 8.0)
                    )
                ):
                    candidates.append(
                        (next_depth, terminal_distance, next_total, value)
                    )
            frontier.append(
                (next_point, used | {index}, next_total, next_depth)
            )
    return min(candidates)[3] if candidates else None


def _transverse_stroke_count(
    page: fitz.Page | None,
    point: tuple[float, float],
    primary_angle: float | None,
    drawing_lines: list[tuple[tuple[float, float], float, float]] | None = None,
) -> int:
    """Count short local strokes transverse to the process axis.

    This is deliberately only soft flange evidence.  Dimension ticks and the
    weld glyph itself can contribute one transverse stroke, while a flange
    face normally contributes a stable pair.
    """

    if page is None or primary_angle is None:
        return 0
    if drawing_lines is None:
        drawing_lines = []
        for drawing in page.get_drawings():
            color = drawing.get("color")
            if color is not None and len(color) >= 3 and (
                float(color[0]) >= 0.8 and float(color[1]) <= 0.25 and float(color[2]) <= 0.25
            ):
                continue
            for item in drawing.get("items", []):
                if not item or item[0] != "l":
                    continue
                start, end = _display_point(page, item[1]), _display_point(page, item[2])
                length = math.dist(start, end)
                midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
                angle = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 180.0
                drawing_lines.append((midpoint, length, angle))
    candidates = []
    for midpoint, length, angle in drawing_lines:
            if not 1.5 <= length <= 24.0 or math.dist(point, midpoint) > 15.0:
                continue
            difference = abs(angle - primary_angle) % 180.0
            difference = min(difference, 180.0 - difference)
            if difference >= 55.0:
                candidates.append(midpoint)
    # Nearby collinear fragments of the same face are one stroke.
    clusters: list[tuple[float, float]] = []
    for midpoint in candidates:
        if not any(math.dist(midpoint, other) <= 2.5 for other in clusters):
            clusters.append(midpoint)
    return len(clusters)


def extract_local_port_signatures(
    callouts: Iterable[PdfWeldCallout],
    skeleton: dict[str, Any] | None,
    *,
    page: fitz.Page | None = None,
) -> list[dict[str, Any]]:
    """Describe local process ports and coarse component semantics at roots.

    Unlike the original IDF-directed matcher, both sides here come from PDF
    geometry.  The descriptor therefore separates hard topology evidence
    (terminal versus through/junction) from soft component-shape evidence.
    """

    segments = list((skeleton or {}).get("segments", []))
    continuation_references: list[
        tuple[tuple[float, float], str, tuple[float, float, float, float]]
    ] = []
    drawing_lines: list[tuple[tuple[float, float], float, float]] = []
    pointer_segments: list[
        tuple[tuple[float, float], tuple[float, float], float]
    ] = []
    if page is not None:
        for word in page.get_text("words"):
            value = str(word[4]).strip().upper()
            if "1100-26-" in value and value.startswith("30-"):
                center = ((float(word[0]) + float(word[2])) / 2.0, (float(word[1]) + float(word[3])) / 2.0)
                if not (
                    center[0] >= float(page.rect.width) * 0.60
                    and center[1] >= float(page.rect.height) * 0.82
                ):
                    continuation_references.append(
                        (center, value, tuple(float(item) for item in word[:4]))
                    )
        for drawing in page.get_drawings():
            color = drawing.get("color")
            if color is not None and len(color) >= 3 and (
                float(color[0]) >= 0.8 and float(color[1]) <= 0.25 and float(color[2]) <= 0.25
            ):
                continue
            for item in drawing.get("items", []):
                if not item or item[0] != "l":
                    continue
                start, end = _display_point(page, item[1]), _display_point(page, item[2])
                length = math.dist(start, end)
                midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
                drawing_lines.append(
                    (
                        midpoint,
                        length,
                        math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 180.0,
                    )
                )
                if float(drawing.get("width") or 0.0) <= 1.2 and 18.0 <= length <= 180.0:
                    pointer_segments.append((start, end, length))
    signatures = []
    for callout in callouts:
        point = tuple(callout.weld_point)
        rays: list[dict[str, Any]] = []
        for segment_index, segment in enumerate(segments):
            start, end = tuple(segment["start"]), tuple(segment["end"])
            dx, dy = end[0] - start[0], end[1] - start[1]
            denominator = dx * dx + dy * dy
            if denominator <= 1e-12:
                continue
            raw_fraction = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator
            fraction = max(0.0, min(1.0, raw_fraction))
            snapped = (start[0] + fraction * dx, start[1] + fraction * dy)
            line_distance = math.dist(point, snapped)
            directions: list[tuple[float, float]] = []
            evidence = ""
            if line_distance <= 4.5 and -0.04 <= raw_fraction <= 1.04:
                # Direction belongs to the process segment, not to the small
                # printer offset between the callout root and that segment.
                # Using ``point`` here creates a spurious oblique ray whenever
                # a root is 1--2 pt off-axis near a segment endpoint, turning
                # an ordinary through port into a false tee/junction.
                if raw_fraction > 0.04:
                    directions.append((start[0] - snapped[0], start[1] - snapped[1]))
                if raw_fraction < 0.96:
                    directions.append((end[0] - snapped[0], end[1] - snapped[1]))
                evidence = "incident-process-segment"
            else:
                start_distance, end_distance = math.dist(point, start), math.dist(point, end)
                if min(start_distance, end_distance) <= 6.5:
                    near, far = (start, end) if start_distance <= end_distance else (end, start)
                    directions.append((far[0] - near[0], far[1] - near[1]))
                    evidence = "near-process-endpoint"
            for direction in directions:
                unit = _unit_vector(direction)
                if unit is None:
                    continue
                rays.append(
                    {
                        "vector": [round(unit[0], 6), round(unit[1], 6)],
                        "angle_deg": round(math.degrees(math.atan2(unit[1], unit[0])) % 360.0, 3),
                        "segment_index": segment_index,
                        "distance_pt": round(line_distance, 3),
                        "evidence": evidence,
                    }
                )
        rays = _dedupe_port_rays(rays)
        ray_count = len(rays)
        primary_angle = float(rays[0]["angle_deg"]) % 180.0 if rays else None
        separation = None
        if ray_count == 2:
            separation = _directed_angle_error(float(rays[0]["angle_deg"]), float(rays[1]["angle_deg"]))
        continuation_reference = _nearby_continuation_reference(
            page,
            point,
            continuation_references,
            # A two-ray through root can sit just outside the note box because
            # its solid dot and short process ticks fill the intervening gap
            # (P143 FS1).  One-ray component terminals retain the strict 20 pt
            # gate: widening those misclassified P107 F23 and shifted two
            # previously correct relations.
            maximum_bbox_distance=32.0 if ray_count == 2 else 20.0,
            follow_pointer=False,
            pointer_segments=pointer_segments,
        )
        continuation_reference_method = (
            "direct-reference-envelope" if continuation_reference else None
        )
        if continuation_reference is None and ray_count == 1:
            continuation_reference = _nearby_continuation_reference(
                page,
                point,
                continuation_references,
                maximum_bbox_distance=20.0,
                follow_pointer=True,
                pointer_segments=pointer_segments,
            )
            if continuation_reference:
                continuation_reference_method = "traced-thin-pointer"
        nearest_reference = None
        nearest_reference_distance = None
        if continuation_references:
            ranked_references = []
            for center, value, bbox in continuation_references:
                dx = max(float(bbox[0]) - point[0], 0.0, point[0] - float(bbox[2]))
                dy = max(float(bbox[1]) - point[1], 0.0, point[1] - float(bbox[3]))
                ranked_references.append((math.hypot(dx, dy), math.dist(point, center), value))
            nearest_reference_distance, _, nearest_reference = min(ranked_references)
        transverse_count = _transverse_stroke_count(page, point, primary_angle, drawing_lines)
        if ray_count >= 3:
            structure_class = "junction"
        elif ray_count == 2 and separation is not None and separation >= 145.0:
            structure_class = "through"
        elif ray_count == 2:
            structure_class = "turn"
        elif (
            ray_count == 1
            and continuation_reference
            and continuation_reference_method == "direct-reference-envelope"
        ):
            structure_class = "continuation-terminal"
        elif ray_count == 1:
            structure_class = "component-terminal"
        else:
            structure_class = "unresolved"
        if transverse_count >= 2 and structure_class not in {"junction", "continuation-terminal"}:
            shape_class = "flange-like"
        elif structure_class == "junction":
            shape_class = "tee-or-olet"
        elif structure_class == "turn":
            shape_class = "elbow-like"
        else:
            shape_class = structure_class
        confidence = 0.0
        if rays:
            confidence = 0.92 if max(float(ray["distance_pt"]) for ray in rays) <= 4.5 else 0.72
        signatures.append(
            {
                "label": callout.label,
                "paper_coordinate": list(point),
                "ray_count": ray_count,
                "rays": rays,
                "structure_class": structure_class,
                "shape_class": shape_class,
                "component_shape_validation": "heuristic-candidate",
                "transverse_stroke_count": transverse_count,
                "continuation_reference": continuation_reference,
                "continuation_interface": bool(continuation_reference),
                "continuation_reference_method": continuation_reference_method,
                # Whole-line orientation evidence only.  Unlike
                # ``continuation_reference`` this does not turn the port into
                # a continuation terminal and therefore cannot perturb the
                # page-local matcher.
                "nearest_continuation_reference_candidate": nearest_reference,
                "nearest_continuation_reference_distance": (
                    None if nearest_reference_distance is None
                    else round(float(nearest_reference_distance), 3)
                ),
                "signature_confidence": round(confidence, 3),
                "skeleton_binding": _skeleton_binding(point, skeleton),
            }
        )
    return signatures


def build_pdf_callout_topology(
    callouts: Iterable[PdfWeldCallout],
    skeleton: dict[str, Any] | None,
    signatures: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a scale-free weld graph directly from process-segment endpoints."""

    items = list(callouts)
    points = [tuple(item.weld_point) for item in items]
    edges: set[tuple[int, int]] = set()
    edge_evidence: dict[tuple[int, int], set[str]] = {}

    def add_edge(left: int, right: int, evidence: str) -> None:
        if left == right:
            return
        edge = tuple(sorted((left, right)))
        edges.add(edge)
        edge_evidence.setdefault(edge, set()).add(evidence)

    for segment in (skeleton or {}).get("segments", []):
        endpoint_matches = []
        for endpoint in (tuple(segment["start"]), tuple(segment["end"])):
            ranked = sorted((math.dist(endpoint, point), index) for index, point in enumerate(points))
            endpoint_matches.append(ranked[0] if ranked and ranked[0][0] <= 18.0 else None)
        if endpoint_matches[0] and endpoint_matches[1]:
            add_edge(endpoint_matches[0][1], endpoint_matches[1][1], "shared-process-segment")

    # Distinct welds around a compact tee, OLET, flange or valve body can be
    # closer than the general skeleton merge tolerance.  Keep them distinct
    # as nodes but record their physical component adjacency.
    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            distance = math.dist(points[left], points[right])
            if distance <= 18.0:
                add_edge(left, right, "compact-component-interface")

    # A design ISO may draw an elbow body several times larger than EP3D.
    # Recover such component pairs from the change between each port's local
    # pipe tangent and the chord joining two neighbouring weld roots.  This is
    # scale-relative and does not mistake an ordinary straight pipe run (near
    # zero tangent/chord error) for a fitting body.
    if len(points) >= 2:
        point_array = np.asarray(points, dtype=float)
        distance_matrix = np.linalg.norm(
            point_array[:, None, :] - point_array[None, :, :], axis=2
        )
        nonzero = np.where(distance_matrix > 1e-9, distance_matrix, np.inf)
        median_nearest = float(np.median(np.min(nonzero, axis=1)))
        maximum_curved_extent = min(80.0, max(24.0, 2.4 * median_nearest))
        for left, neighbours in enumerate(_euclidean_mst_adjacency(point_array)):
            for right in neighbours:
                if right <= left or math.dist(points[left], points[right]) > maximum_curved_extent:
                    continue
                left_signature, right_signature = signatures[left], signatures[right]
                if not (
                    int(left_signature.get("ray_count", 0)) == 2
                    and int(right_signature.get("ray_count", 0)) == 2
                    and left_signature.get("structure_class") == "through"
                    and right_signature.get("structure_class") == "through"
                ):
                    continue
                chord = math.degrees(math.atan2(
                    points[right][1] - points[left][1], points[right][0] - points[left][0]
                )) % 360.0
                reverse_chord = (chord + 180.0) % 360.0
                left_error = min(
                    _directed_angle_error(chord, float(ray["angle_deg"]))
                    for ray in left_signature.get("rays", [])
                )
                right_error = min(
                    _directed_angle_error(reverse_chord, float(ray["angle_deg"]))
                    for ray in right_signature.get("rays", [])
                )
                # In an isometric projection a nominal 90-degree elbow may
                # appear with tangent/chord errors close to 60 degrees.
                if 15.0 <= left_error <= 70.0 and 15.0 <= right_error <= 70.0 and left_error + right_error <= 130.0:
                    add_edge(left, right, "compact-component-interface")
                    add_edge(left, right, "curved-mst-component-interface")

    # Preserve a curved pair after its port rays have been refined from
    # ``through`` to ``turn`` and this graph is rebuilt.
    labels = {item.label: index for index, item in enumerate(items)}
    for left, signature in enumerate(signatures):
        mate_label = signature.get("curved_component_mate_label")
        if mate_label in labels:
            right = labels[mate_label]
            if signatures[right].get("curved_component_mate_label") == items[left].label:
                add_edge(left, right, "compact-component-interface")
                add_edge(left, right, "curved-mst-component-interface")

    adjacency = [set() for _ in items]
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    components = [-1] * len(items)
    component_index = 0
    for start in range(len(items)):
        if components[start] >= 0:
            continue
        stack = [start]
        components[start] = component_index
        while stack:
            node = stack.pop()
            for neighbour in adjacency[node]:
                if components[neighbour] < 0:
                    components[neighbour] = component_index
                    stack.append(neighbour)
        component_index += 1
    addresses = []
    for index, signature in enumerate(signatures):
        neighbour_classes = sorted(signatures[value]["structure_class"] for value in adjacency[index])
        addresses.append(
            {
                "graph_degree": len(adjacency[index]),
                "graph_neighbours": sorted(adjacency[index]),
                "neighbour_structure_classes": neighbour_classes,
                "connected_component": components[index],
                "structure_class": signature["structure_class"],
                "shape_class": signature["shape_class"],
            }
        )
    return {
        "node_count": len(items),
        "edge_count": len(edges),
        "edges": [
            {"nodes": list(edge), "evidence": sorted(edge_evidence[edge])}
            for edge in sorted(edges)
        ],
        "addresses": addresses,
        "connected_component_count": component_index,
    }


def _refine_compact_component_ports(
    callouts: list[PdfWeldCallout],
    signatures: list[dict[str, Any]],
    graph: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recover the fitting-side ray hidden by an EP3D component gap.

    Filled pipe bands stop at an elbow/flange body, so the coarse skeleton
    often contributes only the pipe-side ray.  Two weld roots joined by a
    compact-component edge describe the missing component body.  Its chord is
    safe as a *topological* port direction even though the ISO component is not
    drawn to scale.
    """

    refined = [dict(item, rays=[dict(ray) for ray in item.get("rays", [])]) for item in signatures]
    compact_neighbours: list[set[int]] = [set() for _ in callouts]
    curved_neighbours: list[set[int]] = [set() for _ in callouts]
    for edge in graph.get("edges", []):
        if "compact-component-interface" not in edge.get("evidence", []):
            continue
        left, right = (int(value) for value in edge["nodes"])
        compact_neighbours[left].add(right)
        compact_neighbours[right].add(left)
        if "curved-mst-component-interface" in edge.get("evidence", []):
            curved_neighbours[left].add(right)
            curved_neighbours[right].add(left)

    # A crowded component cluster may give one port two plausible curved-MST
    # neighbours.  Refining every node toward its own nearest candidate can
    # leave a stale, non-reciprocal elbow signature (Indonesia P143: F4->F7
    # while F7->F5).  Only mutual-nearest curved pairs represent a stable
    # two-port component motif.
    curved_choice: dict[int, int] = {}
    for index, neighbours in enumerate(curved_neighbours):
        if neighbours:
            point = tuple(callouts[index].weld_point)
            curved_choice[index] = min(
                neighbours,
                key=lambda value: math.dist(point, tuple(callouts[value].weld_point)),
            )

    recovered = []
    for index, signature in enumerate(refined):
        if int(signature.get("ray_count", 0)) == 2 and index in curved_choice:
            point = tuple(callouts[index].weld_point)
            other = curved_choice[index]
            if curved_choice.get(other) != index:
                other = -1
            if other < 0:
                continue
            other_point = tuple(callouts[other].weld_point)
            unit = _unit_vector((other_point[0] - point[0], other_point[1] - point[1]))
            if unit is not None:
                angle = math.degrees(math.atan2(unit[1], unit[0])) % 360.0
                closest = min(
                    range(len(signature["rays"])),
                    key=lambda value: _directed_angle_error(
                        angle, float(signature["rays"][value]["angle_deg"])
                    ),
                )
                error = _directed_angle_error(angle, float(signature["rays"][closest]["angle_deg"]))
                if 15.0 <= error <= 70.0:
                    signature["rays"][closest] = {
                        "vector": [round(unit[0], 6), round(unit[1], 6)],
                        "angle_deg": round(angle, 3),
                        "segment_index": -1,
                        "distance_pt": round(math.dist(point, other_point), 3),
                        "evidence": "inferred-curved-mst-component-chord",
                    }
                    signature["rays"] = _dedupe_port_rays(signature["rays"])
                    signature["ray_count"] = len(signature["rays"])
                    signature["structure_class"] = "turn"
                    signature["shape_class"] = "elbow-like"
                    signature["component_shape_validation"] = "curved-mst-component-vector-signature"
                    signature["signature_confidence"] = max(
                        float(signature.get("signature_confidence", 0.0)), 0.94
                    )
                    signature["curved_component_mate_label"] = callouts[other].label
                    recovered.append(signature["label"])
                    continue
        if int(signature.get("ray_count", 0)) != 1:
            continue
        point = tuple(callouts[index].weld_point)
        candidates = sorted(
            compact_neighbours[index],
            key=lambda other: math.dist(point, tuple(callouts[other].weld_point)),
        )
        for other in candidates:
            other_point = tuple(callouts[other].weld_point)
            distance = math.dist(point, other_point)
            if distance > 18.0:
                continue
            unit = _unit_vector((other_point[0] - point[0], other_point[1] - point[1]))
            if unit is None:
                continue
            angle = math.degrees(math.atan2(unit[1], unit[0])) % 360.0
            if any(
                _directed_angle_error(angle, float(ray["angle_deg"])) <= 12.0
                for ray in signature["rays"]
            ):
                continue
            signature["rays"].append(
                {
                    "vector": [round(unit[0], 6), round(unit[1], 6)],
                    "angle_deg": round(angle, 3),
                    "segment_index": -1,
                    "distance_pt": round(distance, 3),
                    "evidence": "inferred-compact-component-chord",
                }
            )
            signature["rays"] = _dedupe_port_rays(signature["rays"])
            if len(signature["rays"]) >= 2:
                break
        ray_count = len(signature["rays"])
        if ray_count == int(signature.get("ray_count", 0)):
            continue
        signature["ray_count"] = ray_count
        separation = _directed_angle_error(
            float(signature["rays"][0]["angle_deg"]),
            float(signature["rays"][1]["angle_deg"]),
        )
        if separation >= 155.0:
            signature["structure_class"] = "through"
            signature["shape_class"] = (
                "flange-like" if int(signature.get("transverse_stroke_count", 0)) >= 2 else "through"
            )
        else:
            signature["structure_class"] = "turn"
            signature["shape_class"] = "elbow-like"
        signature["component_shape_validation"] = "compact-component-vector-signature"
        signature["signature_confidence"] = max(float(signature.get("signature_confidence", 0.0)), 0.94)
        recovered.append(signature["label"])
    return refined, {
        "method": "compact-component-chord-port-recovery",
        "recovered_labels": recovered,
        "recovered_count": len(recovered),
    }


def _port_direction_score(
    design_signature: dict[str, Any], ep3d_signature: dict[str, Any], matrix: np.ndarray,
) -> float | None:
    source_rays = design_signature.get("rays", [])
    target_rays = ep3d_signature.get("rays", [])
    if not source_rays or not target_rays:
        return None
    # The project adapter locks north, handedness and sheet axes.  Affine shear
    # is caused by non-metric ISO pipe lengths and must not rotate a physical
    # port direction.  Compare the recovered page directions directly.
    transformed = [float(ray["angle_deg"]) % 360.0 for ray in source_rays]
    target_angles = [float(ray["angle_deg"]) for ray in target_rays]
    if not transformed or not target_angles:
        return None
    angle_cost = np.asarray(
        [[_directed_angle_error(left, right) for right in target_angles] for left in transformed], dtype=float
    )
    rows, columns = linear_sum_assignment(angle_cost)
    mean_error = float(np.mean(angle_cost[rows, columns])) if len(rows) else 180.0
    count_penalty = abs(len(transformed) - len(target_angles)) * 0.18
    return max(0.0, math.exp(-0.5 * (mean_error / 22.0) ** 2) - count_penalty)


def _semantic_pair_evidence(
    design_signature: dict[str, Any],
    ep3d_signature: dict[str, Any],
    matrix: np.ndarray,
    design_address: dict[str, Any],
    ep3d_address: dict[str, Any],
) -> dict[str, Any]:
    left_class = str(design_signature.get("structure_class") or "unresolved")
    right_class = str(ep3d_signature.get("structure_class") or "unresolved")
    left_shape, right_shape = design_signature.get("shape_class"), ep3d_signature.get("shape_class")
    left_rays, right_rays = int(design_signature.get("ray_count", 0)), int(ep3d_signature.get("ray_count", 0))
    hard_reasons = []
    left_confident = float(design_signature.get("signature_confidence", 0.0)) >= 0.70
    right_confident = float(ep3d_signature.get("signature_confidence", 0.0)) >= 0.70
    if left_confident and right_confident:
        physical_classes = {"through", "junction", "turn"}
        left_is_physical = left_class in physical_classes or (
            left_class == "component-terminal" and left_shape == "component-terminal"
        )
        right_is_physical = right_class in physical_classes or (
            right_class == "component-terminal" and right_shape == "component-terminal"
        )
        if (
            left_class == "continuation-terminal" and right_is_physical
        ) or (
            right_class == "continuation-terminal" and left_is_physical
        ):
            if not (
                bool(design_signature.get("continuation_interface"))
                and bool(ep3d_signature.get("continuation_interface"))
            ):
                hard_reasons.append("continuation-terminal-vs-physical-weld")
        # A tee/OLET can be rendered as three visible rays in one export and
        # as a one-sided compact fitting in the other.  That difference is a
        # soft material-shape penalty until both project styles have a proven
        # component glyph classifier; it must not suppress a valid branch weld.
    validated_shape_statuses = {
        "human-confirmed",
        "validated-project-signature",
        "compact-component-vector-signature",
        "curved-mst-component-vector-signature",
    }
    shapes_are_validated = (
        design_signature.get("component_shape_validation") in validated_shape_statuses
        and ep3d_signature.get("component_shape_validation") in validated_shape_statuses
    )
    if shapes_are_validated and {left_shape, right_shape} == {"flange-like", "tee-or-olet"}:
        flange_signature = design_signature if left_shape == "flange-like" else ep3d_signature
        branch_signature = design_signature if left_shape == "tee-or-olet" else ep3d_signature
        if (
            int(flange_signature.get("transverse_stroke_count", 0)) >= 4
            and int(branch_signature.get("ray_count", 0)) >= 3
            and float(flange_signature.get("signature_confidence", 0.0)) >= 0.9
            and float(branch_signature.get("signature_confidence", 0.0)) >= 0.9
        ):
            hard_reasons.append("proven-flange-vs-branch-component")
    generic_shapes = {
        "unresolved", "component-terminal", "continuation-terminal", "through", "turn"
    }
    if left_shape == right_shape and left_shape not in {None, "unresolved"}:
        shape_score = 1.0
    elif left_shape in generic_shapes or right_shape in generic_shapes:
        shape_score = 0.55
    else:
        shape_score = 0.20
    ray_score = 1.0 - min(1.0, abs(left_rays - right_rays) / max(1, left_rays, right_rays))
    direction_score = _port_direction_score(design_signature, ep3d_signature, matrix)
    one_shape_validated = (
        design_signature.get("component_shape_validation") in validated_shape_statuses
        or ep3d_signature.get("component_shape_validation") in validated_shape_statuses
    )
    strong_direction_conflict = bool(
        one_shape_validated
        and left_confident and right_confident
        and left_rays == right_rays == 2
        and {left_class, right_class} == {"through", "turn"}
        and direction_score is not None and float(direction_score) < 0.08
    )
    degree_score = 1.0 - min(
        1.0,
        abs(int(design_address.get("graph_degree", 0)) - int(ep3d_address.get("graph_degree", 0))) / 3.0,
    )
    return {
        "status": "conflict" if hard_reasons else (
            "matched" if left_class == right_class and left_class != "unresolved" else "compatible"
        ),
        "hard_violation_count": len(hard_reasons),
        "hard_violation_reasons": hard_reasons,
        "design_structure_class": left_class,
        "ep3d_structure_class": right_class,
        "design_shape_class": left_shape,
        "ep3d_shape_class": right_shape,
        "component_shape_score": round(shape_score, 3),
        "component_shapes_validated": shapes_are_validated,
        "ray_count_score": round(ray_score, 3),
        "port_direction_score": None if direction_score is None else round(direction_score, 3),
        "strong_direction_conflict": strong_direction_conflict,
        "graph_degree_score": round(degree_score, 3),
    }


def _augmented_assignment(real_cost: np.ndarray, gap_cost: float) -> tuple[list[tuple[int, int]], float]:
    """Solve a one-to-one match where every row and column may be a gap."""

    rows, columns = real_cost.shape
    size = rows + columns
    huge = 1_000.0
    augmented = np.full((size, size), huge, dtype=float)
    augmented[:rows, :columns] = real_cost
    for row in range(rows):
        augmented[row, columns + row] = gap_cost
    for column in range(columns):
        augmented[rows + column, column] = gap_cost
    augmented[rows:, columns:] = 0.0
    left, right = linear_sum_assignment(augmented)
    pairs = [(int(a), int(b)) for a, b in zip(left, right) if a < rows and b < columns]
    return pairs, float(augmented[left, right].sum())


def _dominant_collinear_order(
    points: np.ndarray,
    *,
    minimum_count: int = 6,
) -> tuple[list[int], dict[str, Any]]:
    """Find a long, narrow callout run without assuming ISO length scale."""

    count = len(points)
    if count < minimum_count:
        return [], {"eligible": False, "reason": "too-few-points"}
    diagonal = float(np.linalg.norm(np.ptp(points, axis=0))) or 1.0
    tolerance = max(5.0, min(10.0, diagonal * 0.018))
    best: tuple[int, float, list[int], np.ndarray] | None = None
    for left in range(count):
        for right in range(left + 1, count):
            vector = points[right] - points[left]
            length = float(np.linalg.norm(vector))
            if length < 0.35 * diagonal:
                continue
            axis = vector / length
            perpendicular = np.asarray([-axis[1], axis[0]], dtype=float)
            distances = np.abs((points - points[left]) @ perpendicular)
            inliers = np.flatnonzero(distances <= tolerance).tolist()
            if len(inliers) < minimum_count:
                continue
            positions = (points[inliers] - points[left]) @ axis
            span = float(np.ptp(positions))
            candidate = (len(inliers), span, inliers, axis)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    if best is None:
        return [], {
            "eligible": False,
            "reason": "no-long-narrow-run",
            "tolerance": round(tolerance, 3),
        }
    _, _, seed_inliers, _ = best
    centered = points[seed_inliers] - np.mean(points[seed_inliers], axis=0)
    _, _, vectors = np.linalg.svd(centered, full_matrices=False)
    axis = vectors[0]
    perpendicular = vectors[1]
    center = np.mean(points[seed_inliers], axis=0)
    distances = np.abs((points - center) @ perpendicular)
    inliers = np.flatnonzero(distances <= tolerance).tolist()
    positions = (points[inliers] - center) @ axis
    order = [inliers[index] for index in np.argsort(positions)]
    span = float(np.ptp(positions)) if len(positions) else 0.0
    eligible = len(order) >= minimum_count and span >= 0.45 * diagonal
    return (order if eligible else []), {
        "eligible": eligible,
        "reason": "dominant-collinear-run" if eligible else "insufficient-refined-span",
        "inlier_count": len(order),
        "total_count": count,
        "coverage": round(len(order) / count, 3),
        "span_fraction": round(span / diagonal, 3),
        "tolerance": round(tolerance, 3),
        "axis": [round(float(value), 6) for value in axis],
    }


def _monotonic_sequence_assignment(
    real_cost: np.ndarray,
    design_order: list[int],
    ep3d_order: list[int],
    gap_cost: float,
    *,
    design_gap_costs: dict[int, float] | None = None,
    ep3d_gap_costs: dict[int, float] | None = None,
) -> tuple[list[tuple[int, int]], float]:
    """Needleman-Wunsch assignment over a proven geometric main-chain run."""

    rows, columns = len(design_order), len(ep3d_order)
    scores = np.full((rows + 1, columns + 1), float("inf"), dtype=float)
    move = np.zeros((rows + 1, columns + 1), dtype=np.int8)
    scores[0, 0] = 0.0
    for row in range(1, rows + 1):
        scores[row, 0] = scores[row - 1, 0] + float(
            (design_gap_costs or {}).get(design_order[row - 1], gap_cost)
        )
        move[row, 0] = 1
    for column in range(1, columns + 1):
        scores[0, column] = scores[0, column - 1] + float(
            (ep3d_gap_costs or {}).get(ep3d_order[column - 1], gap_cost)
        )
        move[0, column] = 2
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            pair = scores[row - 1, column - 1] + float(
                real_cost[design_order[row - 1], ep3d_order[column - 1]]
            )
            skip_design = scores[row - 1, column] + float(
                (design_gap_costs or {}).get(design_order[row - 1], gap_cost)
            )
            skip_ep3d = scores[row, column - 1] + float(
                (ep3d_gap_costs or {}).get(ep3d_order[column - 1], gap_cost)
            )
            options = (pair, skip_design, skip_ep3d)
            choice = int(np.argmin(options))
            scores[row, column] = options[choice]
            move[row, column] = choice
    pairs: list[tuple[int, int]] = []
    row, column = rows, columns
    while row or column:
        choice = int(move[row, column])
        if row and column and choice == 0:
            pairs.append((design_order[row - 1], ep3d_order[column - 1]))
            row -= 1
            column -= 1
        elif row and (not column or choice == 1):
            row -= 1
        else:
            column -= 1
    pairs.reverse()
    return pairs, float(scores[rows, columns])


def _dominant_chain_sequence_audit(
    projected_design: np.ndarray,
    ep3d_points: np.ndarray,
    real_cost: np.ndarray,
    gap_cost: float,
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    """Audit a monotonic alternative for a long, nearly straight main run."""

    design_indices, design_audit = _dominant_collinear_order(projected_design)
    ep3d_indices, ep3d_audit = _dominant_collinear_order(ep3d_points)
    if not design_indices or not ep3d_indices:
        return [], {
            "eligible": False,
            "design_run": design_audit,
            "ep3d_run": ep3d_audit,
            "number_used_as_identity": False,
        }
    ep_axis = np.asarray(ep3d_audit["axis"], dtype=float)
    design_order = sorted(
        design_indices, key=lambda index: float(projected_design[index] @ ep_axis)
    )
    ep3d_order = sorted(
        ep3d_indices, key=lambda index: float(ep3d_points[index] @ ep_axis)
    )
    pairs, total = _monotonic_sequence_assignment(
        real_cost, design_order, ep3d_order, gap_cost
    )
    return pairs, {
        "eligible": True,
        "method": "dominant-collinear-gap-aware-monotonic-sequence",
        "design_run": design_audit,
        "ep3d_run": ep3d_audit,
        "design_order": design_order,
        "ep3d_order": ep3d_order,
        "pair_count": len(pairs),
        "total_cost": round(total, 6),
        "number_used_as_identity": False,
    }


def _maximal_mst_paths(points: np.ndarray) -> list[list[int]]:
    """Return branch-to-branch / leaf-to-branch paths of the Euclidean MST."""

    adjacency = _euclidean_mst_adjacency(points)
    paths: list[list[int]] = []
    visited_edges: set[tuple[int, int]] = set()
    starts = [index for index, neighbours in enumerate(adjacency) if len(neighbours) != 2]
    for start in starts:
        for neighbour in sorted(adjacency[start]):
            edge = tuple(sorted((start, neighbour)))
            if edge in visited_edges:
                continue
            path = [start]
            previous, current = start, neighbour
            visited_edges.add(edge)
            while True:
                path.append(current)
                onward = sorted(adjacency[current] - {previous})
                if len(adjacency[current]) != 2 or not onward:
                    break
                following = onward[0]
                visited_edges.add(tuple(sorted((current, following))))
                previous, current = current, following
            paths.append(path)
    return paths


def _weld_list_adjacency_degrees(
    ep3d_signatures: list[dict[str, Any]],
) -> dict[int, int]:
    """Count WELD LIST neighbours sharing a material/component joint node.

    A weld inside a material chain shares one joint node with the weld before
    it and the other node with the weld after it.  Such a degree-two weld is
    poor evidence for an inserted/gap node.  A degree-one weld is a genuine
    chain terminal and is therefore the safer gap candidate when page-local
    cardinalities differ.  Labels without a WELD LIST row remain unknown and
    retain the ordinary sequence gap cost.
    """

    nodes = [_joint_nodes(signature) for signature in ep3d_signatures]
    degrees: dict[int, int] = {}
    for index, own_nodes in enumerate(nodes):
        if not own_nodes:
            continue
        degrees[index] = sum(
            bool(own_nodes & other_nodes)
            for other_index, other_nodes in enumerate(nodes)
            if other_index != index and other_nodes
        )
    return degrees


def _short_mst_gap_path_constraints(
    source: np.ndarray,
    target: np.ndarray,
    real_cost: np.ndarray,
    pair_evidence: dict[tuple[int, int], dict[str, Any]],
    current_pairs: list[tuple[int, int]],
    design_signatures: list[dict[str, Any]],
    ep3d_signatures: list[dict[str, Any]],
    gap_cost: float,
    *,
    protected_design_indices: set[int] | None = None,
    protected_ep3d_indices: set[int] | None = None,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Align a short MST run when one export contains an inserted weld.

    At least four current anchors must already identify the same pair of
    branch-to-branch paths.  Dynamic programming may then localize one or two
    gaps without shifting the remainder.  A continuation mismatch is relaxed
    only for a leaf-to-leaf endpoint with matching direction and graph degree.
    """

    design_paths = [path for path in _maximal_mst_paths(source) if 5 <= len(path) <= 9]
    ep3d_paths = [path for path in _maximal_mst_paths(target) if 5 <= len(path) <= 9]
    current_map = dict(current_pairs)
    protected_design = protected_design_indices or set()
    protected_ep3d = protected_ep3d_indices or set()
    weld_list_degrees = _weld_list_adjacency_degrees(ep3d_signatures)
    ep3d_gap_costs = {
        index: (
            0.78 if degree >= 3
            else 0.72 if degree == 2
            else 0.28 if degree == 1
            else 0.36
        )
        for index, degree in weld_list_degrees.items()
    }
    candidates: list[dict[str, Any]] = []
    for design_path in design_paths:
        design_set = set(design_path)
        for ep3d_path_raw in ep3d_paths:
            ep3d_set = set(ep3d_path_raw)
            anchor_count = sum(
                left in design_set and right in ep3d_set
                for left, right in current_pairs
            )
            if anchor_count < 4:
                continue
            for orientation, ep3d_path in (
                ("forward", ep3d_path_raw),
                ("reverse", list(reversed(ep3d_path_raw))),
            ):
                adjusted = real_cost.copy()
                continuation_relaxations: list[tuple[int, int]] = []
                reopened_sequence_candidates: list[tuple[int, int]] = []
                # Directional MST propagation is deliberately provisional: an
                # inserted weld can make its nearest-neighbour arm shift by
                # one.  Reopen only low-cost, semantically compatible pairs
                # on this proven path.  Explicit component/same-point/page
                # constraints remain protected hard locks.
                for design_index in design_path:
                    for ep3d_index in ep3d_path:
                        if adjusted[design_index, ep3d_index] < 100.0:
                            continue
                        if (
                            design_index in protected_design
                            or ep3d_index in protected_ep3d
                        ):
                            continue
                        evidence = pair_evidence[(design_index, ep3d_index)]
                        semantic = evidence["semantic_gate"]
                        base_cost = float(evidence.get("base_assignment_cost", 100.0))
                        direction_score = semantic.get("port_direction_score")
                        if (
                            int(semantic.get("hard_violation_count", 0)) == 0
                            and base_cost <= 0.32
                            and float(evidence.get("rank_order_score", 0.0)) >= 0.70
                            and (
                                direction_score is None
                                or float(direction_score) >= 0.70
                            )
                            and not bool(evidence.get("reserved_for_cross_page_topology"))
                            and not bool(evidence.get("secondary_component_annotation"))
                            and not bool(evidence.get("secondary_coincident_ep3d_label"))
                        ):
                            adjusted[design_index, ep3d_index] = base_cost
                            reopened_sequence_candidates.append(
                                (design_index, ep3d_index)
                            )
                for design_index in (design_path[0], design_path[-1]):
                    for ep3d_index in (ep3d_path[0], ep3d_path[-1]):
                        semantic = pair_evidence[(design_index, ep3d_index)][
                            "semantic_gate"
                        ]
                        if (
                            semantic.get("hard_violation_reasons")
                            == ["continuation-terminal-vs-physical-weld"]
                            and float(semantic.get("port_direction_score") or 0.0) >= 0.78
                            and float(semantic.get("graph_degree_score") or 0.0) >= 0.99
                            and float(
                                pair_evidence[(design_index, ep3d_index)].get(
                                    "rank_order_score", 0.0
                                )
                            ) >= 0.55
                        ):
                            adjusted[design_index, ep3d_index] = min(
                                0.18,
                                0.10
                                + 0.08 * float(
                                    pair_evidence[(design_index, ep3d_index)].get(
                                        "normalized_residual", 1.0
                                    )
                                ),
                            )
                            continuation_relaxations.append(
                                (design_index, ep3d_index)
                            )
                pairs, total = _monotonic_sequence_assignment(
                    adjusted,
                    design_path,
                    ep3d_path,
                    gap_cost,
                    ep3d_gap_costs=ep3d_gap_costs,
                )
                proposed_map = dict(pairs)
                changed = [
                    (left, current_map.get(left), right)
                    for left, right in pairs
                    if current_map.get(left) != right
                ]
                unmatched_design_before = [
                    index for index in design_path if index not in current_map
                ]
                occupied_targets = set(current_map.values())
                unmatched_ep3d_before = [
                    index for index in ep3d_path if index not in occupied_targets
                ]
                paired_ep3d = set(proposed_map.values())
                skipped_ep3d = [
                    index for index in ep3d_path if index not in paired_ep3d
                ]
                max_cost = max(
                    (float(adjusted[left, right]) for left, right in pairs),
                    default=100.0,
                )
                preserved_anchor_count = sum(
                    current_map.get(left) == right for left, right in pairs
                )
                candidates.append({
                    "design_path": design_path,
                    "ep3d_path": ep3d_path,
                    "orientation": orientation,
                    "pairs": pairs,
                    "total_cost": total,
                    "anchor_count": anchor_count,
                    "preserved_anchor_count": preserved_anchor_count,
                    "changed": changed,
                    "unmatched_design_before": unmatched_design_before,
                    "unmatched_ep3d_before": unmatched_ep3d_before,
                    "skipped_ep3d": skipped_ep3d,
                    "ep3d_gap_costs": {
                        index: ep3d_gap_costs.get(index, gap_cost)
                        for index in ep3d_path
                    },
                    "maximum_pair_cost": max_cost,
                    "continuation_relaxations": continuation_relaxations,
                    "reopened_sequence_candidates": reopened_sequence_candidates,
                })
    eligible = [
        item for item in candidates
        if len(item["pairs"]) >= min(len(item["design_path"]), len(item["ep3d_path"])) - 1
        and item["preserved_anchor_count"] >= 3
        and 2 <= len(item["changed"]) <= 4
        and (
            item["unmatched_design_before"]
            or item["continuation_relaxations"]
        )
        and item["unmatched_ep3d_before"]
        and item["maximum_pair_cost"] <= 0.32
    ]
    candidate_audit = [
        {
            "design_path": item["design_path"],
            "ep3d_path": item["ep3d_path"],
            "orientation": item["orientation"],
            "pair_count": len(item["pairs"]),
            "total_cost": round(float(item["total_cost"]), 6),
            "anchor_count": item["anchor_count"],
            "preserved_anchor_count": item["preserved_anchor_count"],
            "changed_count": len(item["changed"]),
            "unmatched_design_before": item["unmatched_design_before"],
            "unmatched_ep3d_before": item["unmatched_ep3d_before"],
            "skipped_ep3d": item["skipped_ep3d"],
            "ep3d_gap_costs": {
                str(index): round(float(cost), 3)
                for index, cost in item["ep3d_gap_costs"].items()
            },
            "maximum_pair_cost": round(float(item["maximum_pair_cost"]), 6),
            "continuation_relaxations": [
                list(pair) for pair in item["continuation_relaxations"]
            ],
            "reopened_sequence_candidates": [
                list(pair) for pair in item["reopened_sequence_candidates"]
            ],
        }
        for item in candidates
    ]
    eligible.sort(key=lambda item: (item["total_cost"], -item["preserved_anchor_count"]))
    if not eligible:
        return {}, {
            "method": "short-mst-gap-aware-path-sequence",
            "candidate_count": len(candidates),
            "candidates": candidate_audit,
            "applied": False,
            "number_used_as_identity": False,
        }
    best = eligible[0]
    if len(eligible) > 1 and eligible[1]["total_cost"] - best["total_cost"] < 0.08:
        return {}, {
            "method": "short-mst-gap-aware-path-sequence",
            "candidate_count": len(candidates),
            "candidates": candidate_audit,
            "eligible_count": len(eligible),
            "applied": False,
            "reason": "ambiguous-path-pair",
            "number_used_as_identity": False,
        }
    constraints = dict(best["pairs"])
    return constraints, {
        "method": "short-mst-gap-aware-path-sequence",
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "applied": True,
        "design_path": best["design_path"],
        "ep3d_path": best["ep3d_path"],
        "orientation": best["orientation"],
        "pair_count": len(best["pairs"]),
        "changed_count": len(best["changed"]),
        "preserved_anchor_count": best["preserved_anchor_count"],
        "total_cost": round(float(best["total_cost"]), 6),
        "maximum_pair_cost": round(float(best["maximum_pair_cost"]), 6),
        "skipped_ep3d": best["skipped_ep3d"],
        "weld_list_adjacency_degrees": {
            str(index): degree for index, degree in weld_list_degrees.items()
        },
        "ep3d_gap_costs": {
            str(index): round(float(cost), 3)
            for index, cost in best["ep3d_gap_costs"].items()
        },
        "continuation_relaxations": [list(pair) for pair in best["continuation_relaxations"]],
        "reopened_sequence_candidates": [
            list(pair) for pair in best["reopened_sequence_candidates"]
        ],
        "number_used_as_identity": False,
    }


def _euclidean_mst_adjacency(points: np.ndarray) -> list[set[int]]:
    """Return the undirected Euclidean-MST neighbourhood of every weld root."""

    if len(points) == 0:
        return []
    distances = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    tree = minimum_spanning_tree(distances).toarray()
    adjacency = [set() for _ in range(len(points))]
    for left, right in zip(*np.nonzero(tree)):
        adjacency[int(left)].add(int(right))
        adjacency[int(right)].add(int(left))
    return adjacency


def _directional_mst_constraints(
    source: np.ndarray,
    target: np.ndarray,
    matrix: np.ndarray,
    seed_pairs: list[tuple[int, int]],
    pair_evidence: dict[tuple[int, int], dict[str, Any]],
) -> tuple[dict[int, int], dict[str, Any]]:
    """Propagate unique local branch directions through the weld MSTs.

    Schematic pipe lengths are deliberately ignored.  A seed is admitted only
    when both drawings contain a degree-3-or-higher node and the complete set
    of incident directions has a unique low-error alignment.  Once anchored,
    mutually unique neighbour directions may propagate down an arm.  Extra
    terminal nodes therefore remain explicit gaps instead of shifting the
    remainder of the arm by one weld.
    """

    source_adjacency = _euclidean_mst_adjacency(source)
    target_adjacency = _euclidean_mst_adjacency(target)
    def direction(points: np.ndarray, origin: int, neighbour: int, transform: bool) -> float:
        vector = points[neighbour] - points[origin]
        # This Indonesia route is orientation-locked.  The fitted affine may
        # contain strong shear because ISO pipe lengths are schematic; using
        # that shear on port directions corrupts otherwise identical branch
        # angles.  Compare raw page directions after the north/mirror gate.
        return math.degrees(math.atan2(float(vector[1]), float(vector[0]))) % 360.0

    def angular_cost(left: int, left_neighbour: int, right: int, right_neighbour: int) -> float:
        return _directed_angle_error(
            direction(source, left, left_neighbour, True),
            direction(target, right, right_neighbour, False),
        )

    constraints: dict[int, int] = {}
    reverse: dict[int, int] = {}
    queue: list[tuple[int, int]] = []
    seed_audit = []
    for left, right in seed_pairs:
        left_neighbours = sorted(source_adjacency[left])
        right_neighbours = sorted(target_adjacency[right])
        if len(left_neighbours) < 3 or len(left_neighbours) != len(right_neighbours):
            continue
        seed_semantic = pair_evidence[(left, right)]["semantic_gate"]
        if int(seed_semantic["hard_violation_count"]) > 0:
            continue
        # Euclidean-MST degree is only a layout observation.  A run of elbows
        # can also form a degree-3 point cloud although no physical branch is
        # present.  Do not let a pair proven to be two ordinary elbow ports
        # become a branch seed; this previously shifted otherwise-correct
        # equal-cardinality chains on Indonesia P22/P99/P111.  Genuine split
        # anchors such as P112/P128/P145 retain an unvalidated branch-side
        # signature and therefore remain eligible.
        if (
            bool(seed_semantic.get("component_shapes_validated"))
            and seed_semantic.get("design_shape_class") == "elbow-like"
            and seed_semantic.get("ep3d_shape_class") == "elbow-like"
        ):
            continue
        costs = np.asarray([
            [angular_cost(left, a, right, b) for b in right_neighbours]
            for a in left_neighbours
        ], dtype=float)
        rows, columns = linear_sum_assignment(costs)
        errors = [float(costs[a, b]) for a, b in zip(rows, columns)]
        if not errors or max(errors) > 18.0 or float(np.mean(errors)) > 10.0:
            continue
        constraints[left] = right
        reverse[right] = left
        queue.append((left, right))
        seed_audit.append({
            "design_index": left,
            "ep3d_index": right,
            "degree": len(left_neighbours),
            "mean_direction_error_deg": round(float(np.mean(errors)), 3),
            "maximum_direction_error_deg": round(max(errors), 3),
        })

    propagated = []
    while queue:
        left, right = queue.pop(0)
        left_neighbours = [
            value for value in source_adjacency[left]
            if value not in constraints
        ]
        right_neighbours = [
            value for value in target_adjacency[right]
            if value not in reverse
        ]
        if not left_neighbours or not right_neighbours:
            continue
        costs = np.full((len(left_neighbours), len(right_neighbours)), 1_000.0, dtype=float)
        for row, left_neighbour in enumerate(left_neighbours):
            for column, right_neighbour in enumerate(right_neighbours):
                semantic = pair_evidence[(left_neighbour, right_neighbour)]["semantic_gate"]
                if int(semantic["hard_violation_count"]) == 0:
                    costs[row, column] = angular_cost(
                        left, left_neighbour, right, right_neighbour
                    )
        proposals = []
        for row, left_neighbour in enumerate(left_neighbours):
            column = int(np.argmin(costs[row]))
            best = float(costs[row, column])
            if best > 18.0:
                continue
            if int(np.argmin(costs[:, column])) != row:
                continue
            row_values = sorted(float(value) for value in costs[row] if value < 999.0)
            column_values = sorted(float(value) for value in costs[:, column] if value < 999.0)
            row_margin = 180.0 if len(row_values) == 1 else row_values[1] - row_values[0]
            column_margin = 180.0 if len(column_values) == 1 else column_values[1] - column_values[0]
            if min(row_margin, column_margin) < 14.0:
                continue
            proposals.append((left_neighbour, right_neighbours[column], best, row_margin, column_margin))
        for left_neighbour, right_neighbour, error, row_margin, column_margin in proposals:
            if left_neighbour in constraints or right_neighbour in reverse:
                continue
            constraints[left_neighbour] = right_neighbour
            reverse[right_neighbour] = left_neighbour
            queue.append((left_neighbour, right_neighbour))
            propagated.append({
                "design_index": left_neighbour,
                "ep3d_index": right_neighbour,
                "parent_design_index": left,
                "parent_ep3d_index": right,
                "direction_error_deg": round(error, 3),
                "row_margin_deg": round(row_margin, 3),
                "column_margin_deg": round(column_margin, 3),
            })
    return constraints, {
        "method": "unique-direction-euclidean-mst-propagation",
        "seed_count": len(seed_audit),
        "constraint_count": len(constraints),
        "seeds": seed_audit,
        "propagated": propagated,
        "number_used_as_identity": False,
    }


def _isolated_endpoint_recovery_pairs(
    assigned_pairs: list[tuple[int, int]],
    accepted_pairs: list[tuple[int, int]],
    pair_evidence: dict[tuple[int, int], dict[str, Any]],
    real_cost: np.ndarray,
    uniqueness_margin: dict[tuple[int, int], float],
    design_signatures: list[dict[str, Any]],
    ep3d_signatures: list[dict[str, Any]],
    landmark_audit: dict[str, Any],
    determinant: float,
    gap_cost: float,
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    """Recover mutually unique flange endpoints distorted by ISO pipe length.

    This is deliberately a second-stage topology rule.  It never searches for
    a new geometric edge: it can only reinstate an edge already forced by the
    global gap-aware optimum and rejected solely by the spatial residual.
    """

    accepted = set(accepted_pairs)
    unmatched_design = set(range(len(design_signatures))) - {left for left, _ in accepted}
    unmatched_ep3d = set(range(len(ep3d_signatures))) - {right for _, right in accepted}
    audit = {
        "method": "mutually-unique-isolated-flange-endpoint-topology-recovery",
        "eligible_pair_count": 0,
        "recovered_pair_count": 0,
        "recovered_pairs": [],
    }
    if (
        determinant <= 0.0
        or int(landmark_audit.get("common_landmark_count", 0)) < 3
        or float(landmark_audit.get("axis_order_agreement", 0.0)) < 0.85
    ):
        audit["status"] = "disabled-insufficient-page-registration"
        return [], audit

    eligible: list[tuple[int, int]] = []
    for left, right in assigned_pairs:
        if (left, right) in accepted or left not in unmatched_design or right not in unmatched_ep3d:
            continue
        evidence = pair_evidence[(left, right)]
        semantic = evidence["semantic_gate"]
        left_signature, right_signature = design_signatures[left], ep3d_signatures[right]
        direction_score = semantic.get("port_direction_score")
        degree_supported = float(semantic.get("graph_degree_score", 0.0)) >= 0.99
        equipment_supported = (
            direction_score is not None
            and float(direction_score) >= 0.90
            and bool(evidence.get("shared_nearby_equipment_landmarks"))
        )
        globally_isolated_supported = (
            direction_score is not None
            and float(direction_score) >= 0.95
            and float(evidence["rank_order_score"]) >= 0.85
            and uniqueness_margin.get((left, right), 0.0) >= 0.20
        )
        if not (
            int(semantic["hard_violation_count"]) == 0
            and float(real_cost[left, right]) < gap_cost * 2.0
            and float(evidence["normalized_residual"]) > 0.24
            and float(evidence["rank_order_score"]) >= 0.58
            and uniqueness_margin.get((left, right), 0.0) >= 0.025
            and left_signature.get("structure_class") == "component-terminal"
            and right_signature.get("structure_class") == "component-terminal"
            and left_signature.get("shape_class") == "flange-like"
            and right_signature.get("shape_class") == "flange-like"
            and float(left_signature.get("signature_confidence", 0.0)) >= 0.85
            and float(right_signature.get("signature_confidence", 0.0)) >= 0.85
            and float(semantic.get("component_shape_score", 0.0)) >= 0.95
            and float(semantic.get("ray_count_score", 0.0)) >= 0.99
            and (
                degree_supported
                or equipment_supported
                or globally_isolated_supported
            )
            and direction_score is not None
            and float(direction_score) >= 0.20
        ):
            continue
        eligible.append((left, right))

    audit["eligible_pair_count"] = len(eligible)
    left_counts = Counter(left for left, _ in eligible)
    right_counts = Counter(right for _, right in eligible)
    recovered = [
        pair for pair in eligible
        if left_counts[pair[0]] == 1 and right_counts[pair[1]] == 1
    ]
    audit["recovered_pair_count"] = len(recovered)
    audit["recovered_pairs"] = [list(pair) for pair in recovered]
    audit["recovered_pair_evidence"] = [
        {
            "pair": list(pair),
            "shared_nearby_equipment_landmarks": pair_evidence[pair].get(
                "shared_nearby_equipment_landmarks", []
            ),
            "graph_degree_score": pair_evidence[pair]["semantic_gate"].get("graph_degree_score"),
            "port_direction_score": pair_evidence[pair]["semantic_gate"].get("port_direction_score"),
        }
        for pair in recovered
    ]
    audit["status"] = "applied" if recovered else "no-mutually-unique-candidate"
    return recovered, audit


def _topology_over_length_residual_recovery_pairs(
    assigned_pairs: list[tuple[int, int]],
    accepted_pairs: list[tuple[int, int]],
    pair_evidence: dict[tuple[int, int], dict[str, Any]],
    real_cost: np.ndarray,
    uniqueness_margin: dict[tuple[int, int], float],
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    """Recover forced topology pairs rejected only by schematic pipe length.

    The two PDFs can draw the same pipe run at very different lengths.  A
    large spatial residual is therefore not a veto when the global gap-aware
    optimum is forced, page order agrees, component semantics do not conflict,
    and either port direction/degree or a terminal component signature supplies
    independent support.  Displayed weld numbers are intentionally absent.
    """

    accepted = set(accepted_pairs)
    recovered = []
    evidence_rows = []
    for pair in assigned_pairs:
        if pair in accepted:
            continue
        evidence = pair_evidence[pair]
        semantic = evidence["semantic_gate"]
        residual = float(evidence["normalized_residual"])
        rank = float(evidence["rank_order_score"])
        margin = float(uniqueness_margin.get(pair, 0.0))
        direction = semantic.get("port_direction_score")
        direction_degree_support = (
            direction is not None
            and float(direction) >= 0.40
            and float(semantic.get("graph_degree_score", 0.0)) >= 0.30
        )
        matched_component_support = (
            semantic.get("status") == "matched"
            and float(semantic.get("component_shape_score", 0.0)) >= 0.95
        )
        isolated_forced_support = margin >= 0.20 and rank >= 0.98
        if not (
            int(semantic.get("hard_violation_count", 0)) == 0
            and 0.24 < residual <= 0.40
            and float(real_cost[pair]) < 0.35
            and rank >= 0.88
            and margin >= 0.05
            and (
                direction_degree_support
                or matched_component_support
                or isolated_forced_support
            )
        ):
            continue
        recovered.append(pair)
        evidence_rows.append({
            "pair": list(pair),
            "normalized_residual": round(residual, 5),
            "rank_order_score": round(rank, 3),
            "forced_uniqueness_margin": round(margin, 6),
            "port_direction_score": direction,
            "graph_degree_score": semantic.get("graph_degree_score"),
            "semantic_status": semantic.get("status"),
        })
    return recovered, {
        "method": "topology-and-port-evidence-over-length-residual",
        "recovered_pair_count": len(recovered),
        "recovered_pairs": [list(pair) for pair in recovered],
        "evidence": evidence_rows,
        "number_used_as_identity": False,
    }


def _piecewise_topology_projection(
    source: np.ndarray,
    target: np.ndarray,
    global_matrix: np.ndarray,
    anchor_pairs: list[tuple[int, int]],
) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply a smooth local residual field anchored by topology pairs.

    ISO pipe lengths are deliberately non-metric.  A global affine transform
    establishes handedness and coarse placement; nearby forced topology pairs
    then correct each schematic section without allowing a mirror or axis
    swap.  This is intentionally a residual translation field rather than an
    unconstrained local affine warp.
    """

    projected = _apply_affine(source, global_matrix)
    if len(anchor_pairs) < 4:
        return projected, {
            "method": "global-affine-only",
            "anchor_pair_count": len(anchor_pairs),
            "applied": False,
        }
    anchor_left = np.asarray([left for left, _ in anchor_pairs], dtype=int)
    anchor_right = np.asarray([right for _, right in anchor_pairs], dtype=int)
    anchor_source = source[anchor_left]
    residuals = target[anchor_right] - projected[anchor_left]
    lower = np.min(anchor_source, axis=0)
    upper = np.max(anchor_source, axis=0)
    padding = np.maximum((upper - lower) * 0.10, 12.0)
    correction_limit = max(12.0, float(np.linalg.norm(np.ptp(target, axis=0))) * 0.10)
    corrected = projected.copy()
    for index, point in enumerate(source):
        # Do not extrapolate a sparse residual field into another topology
        # section.  This was the cause of P9 upstream nodes being pulled into
        # the final three-elbow group.
        if np.any(point < lower - padding) or np.any(point > upper + padding):
            continue
        distances = np.linalg.norm(anchor_source - point, axis=1)
        # Leave an anchor out when evaluating its own position.  Otherwise a
        # provisional edge would manufacture zero residual for itself and make
        # uniqueness circular instead of independently testable.
        eligible = np.where(distances > 1e-6)[0]
        if not len(eligible):
            continue
        nearest = eligible[np.argsort(distances[eligible])[: min(4, len(eligible))]]
        weights = 1.0 / np.maximum(distances[nearest], 12.0) ** 2
        correction = np.average(residuals[nearest], axis=0, weights=weights)
        magnitude = float(np.linalg.norm(correction))
        if magnitude > correction_limit:
            correction *= correction_limit / magnitude
        corrected[index] += correction
    return corrected, {
        "method": "inverse-distance-topology-residual-field",
        "anchor_pair_count": len(anchor_pairs),
        "applied": True,
        "maximum_anchor_residual": round(float(np.max(np.linalg.norm(residuals, axis=1))), 3),
        "bounded_to_anchor_extent": True,
        "correction_limit": round(correction_limit, 3),
    }


def _compact_component_paths(graph: dict[str, Any]) -> list[list[tuple[int, int]]]:
    """Return simple runs of compact components separated by pipe edges."""

    compact = [
        tuple(int(value) for value in edge["nodes"])
        for edge in graph.get("edges", [])
        if "compact-component-interface" in edge.get("evidence", [])
    ]
    ordinary = [
        tuple(int(value) for value in edge["nodes"])
        for edge in graph.get("edges", [])
        if "compact-component-interface" not in edge.get("evidence", [])
    ]
    meta = [set() for _ in compact]
    for left, first in enumerate(compact):
        for right in range(left + 1, len(compact)):
            second = compact[right]
            if set(first) & set(second):
                continue
            if any(
                (a in first and b in second) or (b in first and a in second)
                for a, b in ordinary
            ):
                meta[left].add(right)
                meta[right].add(left)

    paths: list[list[tuple[int, int]]] = []
    visited_meta_edges: set[tuple[int, int]] = set()
    starts = [index for index, neighbours in enumerate(meta) if len(neighbours) != 2]
    for start in starts:
        for neighbour in sorted(meta[start]):
            first_meta_edge = tuple(sorted((start, neighbour)))
            if first_meta_edge in visited_meta_edges:
                continue
            indices = [start]
            previous, current = start, neighbour
            visited_meta_edges.add(first_meta_edge)
            while True:
                indices.append(current)
                onward = sorted(meta[current] - {previous})
                if len(meta[current]) != 2 or not onward:
                    break
                following = onward[0]
                visited_meta_edges.add(tuple(sorted((current, following))))
                previous, current = current, following
            if len(indices) >= 3:
                paths.append([compact[index] for index in indices])
    return paths


def _polyline_position(point: np.ndarray, polyline: np.ndarray) -> tuple[float, float]:
    """Distance and arclength position of a point on a polyline."""

    best_distance, best_position = float("inf"), 0.0
    traversed = 0.0
    for left, right in zip(polyline[:-1], polyline[1:]):
        vector = right - left
        length = float(np.linalg.norm(vector))
        if length <= 1e-9:
            continue
        fraction = max(0.0, min(1.0, float(np.dot(point - left, vector) / (length * length))))
        projected = left + fraction * vector
        distance = float(np.linalg.norm(point - projected))
        if distance < best_distance:
            best_distance = distance
            best_position = traversed + fraction * length
        traversed += length
    return best_distance, best_position


def _callout_leader_direction(callout: PdfWeldCallout) -> float | None:
    """Direction from the annotation leader start to its physical weld root."""

    vector = np.asarray(callout.weld_point, dtype=float) - np.asarray(
        callout.leader_start, dtype=float
    )
    if float(np.linalg.norm(vector)) <= 1e-6:
        return None
    return math.degrees(math.atan2(float(vector[1]), float(vector[0]))) % 360.0


def _secondary_coincident_ep3d_labels(
    callouts: list[PdfWeldCallout],
    points: np.ndarray,
    signatures: list[dict[str, Any]],
) -> tuple[set[int], dict[str, Any]]:
    """Find inherited compound labels sharing an explicitly led weld dot.

    EP3D occasionally prints a component/material frame whose first token
    looks like a weld identifier.  The extractor deliberately retains that
    token because it can represent a real construction or page-interface
    weld.  When the same solid dot also has an ordinary, explicitly attached
    callout, however, the inherited token is a *secondary* identity of that
    physical port.  It must not consume a normal design-side fitting port;
    doing so displaced the entire end motif on Indonesia P107/P143.

    This classification uses extraction geometry only.  Displayed identifier
    text and numeric order are not inspected.
    """

    secondary: set[int] = set()
    groups: list[dict[str, Any]] = []
    visited: set[int] = set()
    for seed in range(len(callouts)):
        if seed in visited:
            continue
        group = {
            index for index in range(len(callouts))
            if float(np.linalg.norm(points[index] - points[seed])) < 2.0
            and signatures[index].get("structure_class")
            == signatures[seed].get("structure_class")
            and (_port_direction_score(signatures[seed], signatures[index], np.eye(3, 2)) or 0.0) >= 0.99
        }
        visited.update(group)
        if len(group) < 2:
            continue
        explicit = {
            index for index in group
            if "attached" in callouts[index].extraction_method
            and math.dist(
                tuple(callouts[index].leader_start), tuple(callouts[index].label_center)
            ) > 2.0
        }
        inherited = {
            index for index in group
            if "compound-label-frame-with-integrated-leader" in callouts[index].extraction_method
            and math.dist(
                tuple(callouts[index].leader_start), tuple(callouts[index].label_center)
            ) <= 2.0
        }
        if not explicit or not inherited:
            continue
        secondary.update(inherited)
        groups.append({
            "coordinate": [round(float(value), 3) for value in points[seed]],
            "explicit_indices": sorted(explicit),
            "secondary_indices": sorted(inherited),
        })
    return secondary, {
        "method": "coincident-explicit-vs-inherited-physical-port-role",
        "secondary_indices": sorted(secondary),
        "groups": groups,
        "number_used_as_identity": False,
    }


def _clustered_secondary_design_annotations(
    callouts: list[PdfWeldCallout],
    signatures: list[dict[str, Any]],
    graph: dict[str, Any],
) -> tuple[set[int], dict[str, Any]]:
    """Defer RP annotations embedded in another junction's physical root.

    Independent RP-prefixed callouts can be real welds (for example the two
    elbow ports on P66).  A different motif occurs when an RP annotation root
    sits within ten points of an ordinary junction callout and the page graph
    directly connects the two.  That RP is a secondary component annotation,
    not another independently placeable PDF port.
    """

    neighbours = [set() for _ in callouts]
    for edge in graph.get("edges", []):
        left, right = (int(value) for value in edge["nodes"])
        neighbours[left].add(right)
        neighbours[right].add(left)
    deferred: set[int] = set()
    groups: list[dict[str, Any]] = []
    for index, callout in enumerate(callouts):
        if not callout.label.upper().startswith("RP"):
            continue
        candidates = [
            other for other in neighbours[index]
            if not callouts[other].label.upper().startswith("RP")
            and signatures[other].get("structure_class") == "junction"
            and math.dist(callout.weld_point, callouts[other].weld_point) <= 10.0
        ]
        if not candidates:
            continue
        owner = min(
            candidates,
            key=lambda other: math.dist(callout.weld_point, callouts[other].weld_point),
        )
        deferred.add(index)
        groups.append({
            "secondary_index": index,
            "secondary_label": callout.label,
            "owner_index": owner,
            "owner_label": callouts[owner].label,
            "root_distance": round(
                math.dist(callout.weld_point, callouts[owner].weld_point), 3
            ),
        })
    return deferred, {
        "method": "rp-root-embedded-in-adjacent-junction-port",
        "secondary_indices": sorted(deferred),
        "groups": groups,
        "number_used_as_identity": False,
    }


def _joint_nodes(signature: dict[str, Any]) -> set[str]:
    semantics = signature.get("weld_list_semantics") or {}
    joint_no = str(semantics.get("joint_no", "")).upper()
    if re.fullmatch(r"[A-Z0-9.]+-[A-Z0-9.]+", joint_no) is None:
        return set()
    return set(joint_no.split("-"))


def _same_point_joint_path_constraints(
    design_callouts: list[PdfWeldCallout],
    ep3d_callouts: list[PdfWeldCallout],
    design_graph: dict[str, Any],
    preliminary_pairs: list[tuple[int, int]],
    ep3d_signatures: list[dict[str, Any]],
    *,
    excluded_design_indices: set[int] | None = None,
    reserved_design_indices: set[int] | None = None,
    reserved_ep3d_indices: set[int] | None = None,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Repair a disconnected two-port assignment using EP3D's joint graph.

    This is intentionally a narrow gate.  It acts only when an already paired
    compact design edge maps to two WELD LIST rows with *no common joint
    node*, exactly one endpoint owns a co-located EP3D label group, and a
    unique free label in that group restores both joint connectivity and
    nominal-size continuity.  Connected existing assignments are preserved.
    """

    excluded = excluded_design_indices or set()
    reserved_design = reserved_design_indices or set()
    reserved_ep3d = reserved_ep3d_indices or set()
    assigned = {left: right for left, right in preliminary_pairs}
    occupied = {right: left for left, right in preliminary_pairs}
    constraints: dict[int, int] = {}
    events: list[dict[str, Any]] = []
    design_edges = [
        tuple(int(value) for value in edge["nodes"])
        for edge in design_graph.get("edges", [])
    ]
    for design_left, design_right in design_edges:
        if (
            design_left in excluded or design_right in excluded
            or design_left in reserved_design or design_right in reserved_design
            or design_left not in assigned or design_right not in assigned
            or not 4.0 <= math.dist(
                design_callouts[design_left].weld_point,
                design_callouts[design_right].weld_point,
            ) <= 20.0
        ):
            continue
        target_left, target_right = assigned[design_left], assigned[design_right]
        current_nodes_left = _joint_nodes(ep3d_signatures[target_left])
        current_nodes_right = _joint_nodes(ep3d_signatures[target_right])
        if (
            not current_nodes_left or not current_nodes_right
            or current_nodes_left & current_nodes_right
        ):
            continue

        endpoint_options = []
        for design_index, current_target, anchor_target in (
            (design_left, target_left, target_right),
            (design_right, target_right, target_left),
        ):
            same_point = [
                candidate for candidate in range(len(ep3d_callouts))
                if candidate != current_target
                and math.dist(
                    ep3d_callouts[candidate].weld_point,
                    ep3d_callouts[current_target].weld_point,
                ) < 2.0
                and candidate not in reserved_ep3d
                and (
                    candidate not in occupied
                    or occupied[candidate] in excluded
                )
            ]
            if len(same_point) < 2:
                continue
            anchor_semantics = ep3d_signatures[anchor_target].get(
                "weld_list_semantics", {}
            )
            anchor_nodes = _joint_nodes(ep3d_signatures[anchor_target])
            anchor_size = anchor_semantics.get("nominal_size_in")
            eligible = []
            for candidate in same_point:
                candidate_semantics = ep3d_signatures[candidate].get(
                    "weld_list_semantics", {}
                )
                candidate_size = candidate_semantics.get("nominal_size_in")
                if (
                    anchor_size is None or candidate_size is None
                    or abs(float(anchor_size) - float(candidate_size)) > 0.01
                    or not (_joint_nodes(ep3d_signatures[candidate]) & anchor_nodes)
                ):
                    continue
                eligible.append(candidate)
            if len(eligible) == 1:
                endpoint_options.append((design_index, current_target, anchor_target, eligible[0]))
        if len(endpoint_options) != 1:
            continue
        design_index, current_target, anchor_target, replacement = endpoint_options[0]
        if design_index in constraints or replacement in constraints.values():
            continue
        constraints[design_index] = replacement
        events.append({
            "design_index": design_index,
            "design_label": design_callouts[design_index].label,
            "rejected_ep3d_index": current_target,
            "rejected_ep3d_label": ep3d_callouts[current_target].label,
            "anchor_ep3d_index": anchor_target,
            "anchor_ep3d_label": ep3d_callouts[anchor_target].label,
            "selected_ep3d_index": replacement,
            "selected_ep3d_label": ep3d_callouts[replacement].label,
            "reason": "disconnected-current-joints-unique-same-size-connected-alternative",
        })
    return constraints, {
        "method": "same-point-weld-list-joint-path-continuity",
        "constraint_count": len(constraints),
        "events": events,
        "number_used_as_identity": False,
    }


def _unique_continuation_terminal_constraints(
    design_signatures: list[dict[str, Any]],
    ep3d_signatures: list[dict[str, Any]],
) -> tuple[dict[int, int], dict[str, Any]]:
    """Reserve a unique open-page terminal on both representations.

    A continuation callout can be schematically placed at opposite extremes
    of the two page exports, so spatial residual and x/y rank are not reliable
    identities.  If each side contains exactly one *terminal* with an attached
    SEE ISO reference, its graph role is unique and may safely anchor the
    remaining gap-aware alignment.  A printer can split the same open-page
    endpoint into one local ray on one export and two collinear fragments on
    the other.  Therefore a *unique explicit reference* is the identity; ray
    count is retained as semantic evidence but is not an eligibility gate.
    """

    all_design_indices = [
        index for index, signature in enumerate(design_signatures)
        if bool(signature.get("continuation_interface"))
    ]
    all_ep3d_indices = [
        index for index, signature in enumerate(ep3d_signatures)
        if bool(signature.get("continuation_interface"))
    ]
    one_ray_design_indices = [
        index for index in all_design_indices
        if int(design_signatures[index].get("ray_count", 0)) == 1
    ]
    one_ray_ep3d_indices = [
        index for index in all_ep3d_indices
        if int(ep3d_signatures[index].get("ray_count", 0)) == 1
    ]
    # Prefer a unique physical terminal.  Through roots can legitimately
    # carry a nearby reference belonging to an adjacent split point (P12
    # FS15/FS20); allowing those to defeat the one-ray terminal anchor shifted
    # the complete page sequence.  Fall back to all explicit interfaces only
    # when each side has exactly one, which covers a printer-split collinear
    # endpoint such as P17 FS12 -> F5.
    if len(one_ray_design_indices) == len(one_ray_ep3d_indices) == 1:
        design_indices = one_ray_design_indices
        ep3d_indices = one_ray_ep3d_indices
        selection = "unique-one-ray-terminal"
    elif (
        len(all_design_indices) == len(all_ep3d_indices) == 1
        and int(design_signatures[all_design_indices[0]].get("ray_count", 0)) == 2
        and str(design_signatures[all_design_indices[0]].get("structure_class")) == "through"
        and int(ep3d_signatures[all_ep3d_indices[0]].get("ray_count", 0)) == 1
        and str(ep3d_signatures[all_ep3d_indices[0]].get("structure_class"))
        == "continuation-terminal"
    ):
        design_indices = all_design_indices
        ep3d_indices = all_ep3d_indices
        selection = "unique-split-collinear-design-through"
    else:
        design_indices = all_design_indices
        ep3d_indices = all_ep3d_indices
        selection = "ambiguous"
    constraints = (
        {design_indices[0]: ep3d_indices[0]}
        if selection != "ambiguous"
        and len(design_indices) == len(ep3d_indices) == 1
        else {}
    )
    return constraints, {
        "method": "unique-referenced-continuation-terminal-role",
        "selection": selection,
        "all_design_indices": all_design_indices,
        "all_ep3d_indices": all_ep3d_indices,
        "design_indices": design_indices,
        "ep3d_indices": ep3d_indices,
        "constraints": [
            {"design_index": left, "ep3d_index": right}
            for left, right in constraints.items()
        ],
        "number_used_as_identity": False,
    }


def _mutual_continuation_boundary_constraints(
    source: np.ndarray,
    target: np.ndarray,
    matrix: np.ndarray,
    design_signatures: list[dict[str, Any]],
    ep3d_signatures: list[dict[str, Any]],
    design_graph: dict[str, Any],
    ep3d_graph: dict[str, Any],
    diagonal: float,
    *,
    maximum_normalized_residual: float = 0.025,
    maximum_directional_residual: float = 0.065,
    minimum_direction_score: float = 0.78,
    minimum_alternative_margin: float = 0.20,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Recover page-boundary counterparts by mutual geometric uniqueness.

    One PDF may attach ``SEE ISO`` to the weld dot itself while the other
    attaches it to the adjacent fitting or short pipe fragment.  The latter
    is then classified as an ordinary physical endpoint even though both dots
    occupy the same registered location.  This rule is intentionally narrow:

    * at least one side must already have explicit continuation evidence;
    * both nodes must be graph boundaries (degree zero or one);
    * the pair must be the mutual nearest boundary with a wide alternative
      margin on both sides; and
    * the registered residual must be below 2.5% of the target extent.

    Displayed weld labels and numeric order are never inspected.
    """

    if not len(source) or not len(target):
        return {}, {
            "method": "mutual-registered-continuation-boundary",
            "constraints": [],
            "number_used_as_identity": False,
        }
    projected = _apply_affine(source, matrix)
    normalized = np.linalg.norm(
        projected[:, None, :] - target[None, :, :], axis=2
    ) / max(float(diagonal), 1e-9)
    design_degrees = [
        int(item.get("graph_degree", 0))
        for item in design_graph.get("addresses", [])
    ]
    ep3d_degrees = [
        int(item.get("graph_degree", 0))
        for item in ep3d_graph.get("addresses", [])
    ]
    boundary_residuals: dict[tuple[int, int], float] = {}
    directed_boundary_residuals: dict[tuple[int, int], float] = {}
    direction_scores: dict[tuple[int, int], float | None] = {}
    eligible: dict[tuple[int, int], float] = {}
    for left, design_signature in enumerate(design_signatures):
        if left >= len(design_degrees) or design_degrees[left] > 1:
            continue
        if str(design_signature.get("structure_class")) == "junction":
            continue
        for right, ep3d_signature in enumerate(ep3d_signatures):
            if right >= len(ep3d_degrees) or ep3d_degrees[right] > 1:
                continue
            if str(ep3d_signature.get("structure_class")) == "junction":
                continue
            boundary_residuals[(left, right)] = float(normalized[left, right])
            direction_score = _port_direction_score(
                design_signature, ep3d_signature, matrix
            )
            direction_scores[(left, right)] = direction_score
            if (
                direction_score is not None
                and float(direction_score) >= minimum_direction_score
            ):
                directed_boundary_residuals[(left, right)] = float(
                    normalized[left, right]
                )
            if not (
                bool(design_signature.get("continuation_interface"))
                or bool(ep3d_signature.get("continuation_interface"))
            ):
                continue
            eligible[(left, right)] = float(normalized[left, right])

    def off_axis_secondary_has_physical_port(
        signatures: list[dict[str, Any]], index: int
    ) -> bool:
        signature = signatures[index]
        binding = signature.get("skeleton_binding") or {}
        if (
            int(signature.get("ray_count", 0)) != 0
            or float(binding.get("distance", 0.0)) <= 5.0
            or binding.get("segment_index") is None
        ):
            return False
        for other_index, other in enumerate(signatures):
            if other_index == index or int(other.get("ray_count", 0)) < 1:
                continue
            other_binding = other.get("skeleton_binding") or {}
            if (
                other_binding.get("segment_index") != binding.get("segment_index")
                or float(other_binding.get("distance", 99.0)) > 1.5
            ):
                continue
            if math.dist(
                tuple(signature.get("paper_coordinate", ())),
                tuple(other.get("paper_coordinate", ())),
            ) <= 14.5:
                return True
        return False

    constraints: dict[int, int] = {}
    deferred_secondary_design_indices: set[int] = set()
    deferred_secondary_ep3d_indices: set[int] = set()
    audit_pairs: list[dict[str, Any]] = []
    for (left, right), residual in sorted(eligible.items(), key=lambda item: item[1]):
        direction_score = direction_scores.get((left, right))
        design_is_explicit_terminal = (
            bool(design_signatures[left].get("continuation_interface"))
            and str(design_signatures[left].get("structure_class"))
            == "continuation-terminal"
            and int(design_signatures[left].get("ray_count", 0)) == 1
        )
        ep3d_is_explicit_terminal = (
            bool(ep3d_signatures[right].get("continuation_interface"))
            and str(ep3d_signatures[right].get("structure_class"))
            == "continuation-terminal"
            and int(ep3d_signatures[right].get("ray_count", 0)) == 1
        )
        printer_split_counterpart = (
            design_is_explicit_terminal
            and str(ep3d_signatures[right].get("structure_class")) == "through"
        ) or (
            ep3d_is_explicit_terminal
            and str(design_signatures[left].get("structure_class")) == "through"
        )
        directional_recovery = (
            residual > maximum_normalized_residual
            and residual <= maximum_directional_residual
            and printer_split_counterpart
            and direction_score is not None
            and float(direction_score) >= minimum_direction_score
        )
        if residual > maximum_normalized_residual and not directional_recovery:
            continue
        ranking_pool = (
            directed_boundary_residuals if directional_recovery
            else boundary_residuals
        )
        row_ranked = sorted(
            (value, candidate_right)
            for (candidate_left, candidate_right), value in ranking_pool.items()
            if candidate_left == left
        )
        column_ranked = sorted(
            (value, candidate_left)
            for (candidate_left, candidate_right), value in ranking_pool.items()
            if candidate_right == right
        )
        if not row_ranked or not column_ranked:
            continue
        if row_ranked[0][1] != right or column_ranked[0][1] != left:
            continue
        row_margin = (
            row_ranked[1][0] - row_ranked[0][0]
            if len(row_ranked) > 1 else float("inf")
        )
        column_margin = (
            column_ranked[1][0] - column_ranked[0][0]
            if len(column_ranked) > 1 else float("inf")
        )
        if min(row_margin, column_margin) < minimum_alternative_margin:
            continue
        # A callout dot can sit 7--14 pt off the actual process endpoint while
        # a second callout occupies that endpoint and carries the real port
        # rays.  The off-axis, rayless identity is a page/construction
        # secondary, not the physical continuation root (Indonesia P83).  Do
        # not let spatial mutual-nearest evidence override the observed port.
        if off_axis_secondary_has_physical_port(design_signatures, left):
            deferred_secondary_design_indices.add(left)
            continue
        if off_axis_secondary_has_physical_port(ep3d_signatures, right):
            deferred_secondary_ep3d_indices.add(right)
            continue
        if left in constraints or right in constraints.values():
            continue
        constraints[left] = right
        audit_pairs.append({
            "design_index": left,
            "ep3d_index": right,
            "normalized_residual": round(residual, 6),
            "row_alternative_margin": (
                round(row_margin, 6) if math.isfinite(row_margin) else None
            ),
            "column_alternative_margin": (
                round(column_margin, 6) if math.isfinite(column_margin) else None
            ),
            "recovery_mode": (
                "direction-qualified-extended-residual"
                if directional_recovery else "strict-residual"
            ),
            "port_direction_score": (
                round(float(direction_score), 6)
                if direction_score is not None else None
            ),
            "design_had_explicit_interface": bool(
                design_signatures[left].get("continuation_interface")
            ),
            "ep3d_had_explicit_interface": bool(
                ep3d_signatures[right].get("continuation_interface")
            ),
        })
    return constraints, {
        "method": "mutual-registered-continuation-boundary",
        "maximum_normalized_residual": maximum_normalized_residual,
        "maximum_directional_residual": maximum_directional_residual,
        "minimum_direction_score": minimum_direction_score,
        "minimum_alternative_margin": minimum_alternative_margin,
        "deferred_secondary_design_indices": sorted(
            deferred_secondary_design_indices
        ),
        "deferred_secondary_ep3d_indices": sorted(
            deferred_secondary_ep3d_indices
        ),
        "constraints": audit_pairs,
        "number_used_as_identity": False,
    }


def _disruptive_one_sided_continuation_targets(
    assigned_pairs: list[tuple[int, int]],
    pair_evidence: dict[tuple[int, int], dict[str, Any]],
    design_signatures: list[dict[str, Any]],
    ep3d_signatures: list[dict[str, Any]],
    *,
    maximum_direction_score: float = 0.08,
    minimum_residual: float = 0.12,
) -> tuple[set[int], dict[str, Any]]:
    """Hold a one-sided page terminal out when it shifts the local chain.

    A genuine continuation weld can be owned by the neighbouring ISO page.
    If it exists only on EP3D, forcing it into the current page advances the
    one-to-one assignment by one position.  A single poor direction is not
    enough to decide ownership: the terminal match *and* another assigned
    pair must both have poor directed-port agreement and substantial
    residual.  The target then remains an explicit page gap for the later
    whole-line topology stage.
    """

    design_interfaces = {
        index for index, signature in enumerate(design_signatures)
        if bool(signature.get("continuation_interface"))
    }
    ep3d_terminals = {
        index for index, signature in enumerate(ep3d_signatures)
        if bool(signature.get("continuation_interface"))
        and str(signature.get("structure_class")) == "continuation-terminal"
        and int(signature.get("ray_count", 0)) == 1
    }
    audit: dict[str, Any] = {
        "method": "one-sided-continuation-cascade-gap",
        "excluded_ep3d_indices": [],
        "evidence": [],
        "number_used_as_identity": False,
    }
    if design_interfaces or len(ep3d_terminals) != 1:
        return set(), audit

    paired_by_target = {right: left for left, right in assigned_pairs}
    terminal = next(iter(ep3d_terminals))
    if terminal not in paired_by_target:
        return set(), audit
    terminal_pair = (paired_by_target[terminal], terminal)

    def poor_pair(pair: tuple[int, int]) -> bool:
        evidence = pair_evidence[pair]
        direction = evidence["semantic_gate"].get("port_direction_score")
        return bool(
            direction is not None
            and float(direction) <= maximum_direction_score
            and float(evidence.get("normalized_residual", 0.0)) >= minimum_residual
        )

    if not poor_pair(terminal_pair):
        return set(), audit
    corroborating = [
        pair for pair in assigned_pairs
        if pair != terminal_pair and poor_pair(pair)
    ]
    if not corroborating:
        return set(), audit
    audit["excluded_ep3d_indices"] = [terminal]
    audit["evidence"] = [
        {
            "design_index": left,
            "ep3d_index": right,
            "normalized_residual": round(
                float(pair_evidence[(left, right)]["normalized_residual"]), 6
            ),
            "port_direction_score": round(
                float(
                    pair_evidence[(left, right)]["semantic_gate"][
                        "port_direction_score"
                    ]
                ), 6
            ),
            "role": "one-sided-terminal" if right == terminal else "cascade-corroboration",
        }
        for left, right in [terminal_pair, *corroborating]
    ]
    return {terminal}, audit


def _validated_component_pair_constraints(
    design: list[PdfWeldCallout],
    ep3d: list[PdfWeldCallout],
    source: np.ndarray,
    target: np.ndarray,
    matrix: np.ndarray,
    design_graph: dict[str, Any],
    ep3d_graph: dict[str, Any],
    design_signatures: list[dict[str, Any]],
    ep3d_signatures: list[dict[str, Any]],
) -> tuple[dict[int, int], dict[int, set[int]], set[int], dict[str, Any]]:
    """Align a proven two-port fitting as one directed component motif.

    Matching the two weld roots independently can reverse an elbow whenever
    ISO schematic lengths distort the midpoint.  Here the two endpoint ray
    signatures decide the orientation together.  Annotation-leader direction
    is only a final tie-break for EP3D numbers sharing the same physical dot;
    displayed label text is never scored.
    """

    def component_edges(
        graph: dict[str, Any], signatures: list[dict[str, Any]],
        points: np.ndarray, *, require_curved: bool,
    ) -> list[tuple[int, int]]:
        result = []
        for edge in graph.get("edges", []):
            evidence = set(edge.get("evidence", []))
            if require_curved:
                if "curved-mst-component-interface" not in evidence:
                    continue
            elif "compact-component-interface" not in evidence:
                continue
            left, right = (int(value) for value in edge["nodes"])
            if float(np.linalg.norm(points[left] - points[right])) < 2.0:
                # Multiple callouts may intentionally share one weld dot;
                # their zero-length link is not a component body.
                continue
            if not all(
                signatures[index].get("shape_class") == "elbow-like"
                and signatures[index].get("component_shape_validation") in {
                    "compact-component-vector-signature",
                    "curved-mst-component-vector-signature",
                }
                for index in (left, right)
            ):
                continue
            result.append((left, right))
        return result

    design_edges = component_edges(
        design_graph, design_signatures, source, require_curved=True
    )
    ep3d_edges = component_edges(
        ep3d_graph, ep3d_signatures, target, require_curved=False
    )
    audit: dict[str, Any] = {
        "method": "validated-directed-two-port-component-alignment",
        "design_component_count": len(design_edges),
        "ep3d_component_count": len(ep3d_edges),
        "accepted_component_pairs": [],
        "number_used_as_identity": False,
    }
    if not design_edges or not ep3d_edges:
        audit["constraint_count"] = 0
        return {}, {}, set(), audit
    if (
        len(design) > 18 or len(ep3d) > 18
        or len(design_edges) > 3 or len(ep3d_edges) > 3
    ):
        # Dense sheets and repeated component trains require ordered/global
        # topology.  A locally perfect elbow direction is not a unique
        # identity there (Indonesia P94/P140/P146).  Keep the observations in
        # the audit but leave hard reservation to the chain matcher.
        audit["constraint_count"] = 0
        audit["status"] = "deferred-to-global-ordered-component-topology"
        return {}, {}, set(), audit

    projected = _apply_affine(source, matrix)
    diagonal = float(np.linalg.norm(np.ptp(target, axis=0))) or 1.0
    candidates: list[dict[str, Any]] = []
    for design_edge_index, (design_left, design_right) in enumerate(design_edges):
        design_midpoint = (projected[design_left] + projected[design_right]) / 2.0
        for ep3d_edge_index, (ep3d_left, ep3d_right) in enumerate(ep3d_edges):
            ep3d_midpoint = (target[ep3d_left] + target[ep3d_right]) / 2.0
            midpoint_residual = float(
                np.linalg.norm(design_midpoint - ep3d_midpoint) / diagonal
            )
            orientations = []
            for orientation, ordered in (
                ("direct", (ep3d_left, ep3d_right)),
                ("reverse", (ep3d_right, ep3d_left)),
            ):
                port_scores = [
                    _port_direction_score(
                        design_signatures[design_index], ep3d_signatures[ep3d_index], matrix
                    )
                    for design_index, ep3d_index in zip(
                        (design_left, design_right), ordered
                    )
                ]
                if any(score is None for score in port_scores):
                    continue
                leader_scores = []
                for design_index, ep3d_index in zip((design_left, design_right), ordered):
                    design_angle = _callout_leader_direction(design[design_index])
                    ep3d_angle = _callout_leader_direction(ep3d[ep3d_index])
                    leader_scores.append(
                        0.5 if design_angle is None or ep3d_angle is None
                        else 1.0 - _directed_angle_error(design_angle, ep3d_angle) / 180.0
                    )
                mean_port = float(np.mean(port_scores))
                mean_leader = float(np.mean(leader_scores))
                cost = (
                    0.76 * (1.0 - mean_port)
                    + 0.16 * midpoint_residual
                    + 0.08 * (1.0 - mean_leader)
                )
                orientations.append((cost, orientation, ordered, mean_port, mean_leader))
            if len(orientations) != 2:
                continue
            orientations.sort(key=lambda item: item[0])
            best, alternative = orientations
            candidates.append({
                "design_edge_index": design_edge_index,
                "ep3d_edge_index": ep3d_edge_index,
                "design_edge": (design_left, design_right),
                "ep3d_edge": tuple(best[2]),
                "cost": float(best[0]),
                "orientation": best[1],
                "orientation_margin": float(alternative[0] - best[0]),
                "mean_port_direction_score": float(best[3]),
                "mean_leader_direction_score": float(best[4]),
                "midpoint_residual": midpoint_residual,
            })

    constraints: dict[int, int] = {}
    equivalent_target_constraints: dict[int, set[int]] = {}
    physical_equivalent_targets: set[int] = set()
    audit["candidate_pairs"] = [
        {
            "design_edge": list(value["design_edge"]),
            "ep3d_edge": list(value["ep3d_edge"]),
            "cost": round(value["cost"], 6),
            "orientation_margin": round(value["orientation_margin"], 6),
            "mean_port_direction_score": round(value["mean_port_direction_score"], 6),
            "midpoint_residual": round(value["midpoint_residual"], 6),
        }
        for value in sorted(candidates, key=lambda item: item["cost"])
    ]
    used_design_edges: set[int] = set()
    used_ep3d_edges: set[int] = set()
    reserved_design_nodes: set[int] = set()
    reserved_ep3d_nodes: set[int] = set()
    for candidate in sorted(candidates, key=lambda item: item["cost"]):
        if (
            candidate["design_edge_index"] in used_design_edges
            or candidate["ep3d_edge_index"] in used_ep3d_edges
            or candidate["mean_port_direction_score"] < 0.92
            or candidate["orientation_margin"] < 0.08
            or candidate["midpoint_residual"] > 0.45
            or (
                candidate["midpoint_residual"] > 0.28
                and candidate["mean_port_direction_score"] < 0.98
            )
            or candidate["cost"] > 0.14
        ):
            continue
        # Both the design component and the EP3D component must prefer this
        # edge-level candidate; this prevents a crowded cluster from being
        # greedily consumed by a merely nearby fitting.
        row_costs = sorted(
            value["cost"] for value in candidates
            if value["design_edge_index"] == candidate["design_edge_index"]
        )
        column_costs = sorted(
            value["cost"] for value in candidates
            if value["ep3d_edge_index"] == candidate["ep3d_edge_index"]
        )
        if candidate["cost"] > row_costs[0] + 1e-9 or candidate["cost"] > column_costs[0] + 1e-9:
            continue
        design_left, design_right = candidate["design_edge"]
        ep3d_left, ep3d_right = candidate["ep3d_edge"]
        if any(index in reserved_design_nodes for index in (design_left, design_right)):
            continue
        if any(index in reserved_ep3d_nodes for index in (ep3d_left, ep3d_right)):
            continue
        for design_index, ep3d_index in (
            (design_left, ep3d_left), (design_right, ep3d_right)
        ):
            equivalent = {
                other for other in range(len(ep3d))
                if float(np.linalg.norm(target[other] - target[ep3d_index])) < 2.0
                and ep3d_signatures[other].get("structure_class")
                == ep3d_signatures[ep3d_index].get("structure_class")
                and (_port_direction_score(
                    ep3d_signatures[ep3d_index], ep3d_signatures[other], matrix
                ) or 0.0) >= 0.99
            }
            if len(equivalent) > 1:
                physical_equivalent_targets.update(equivalent)
                explicit_attached = {
                    other for other in equivalent
                    if "attached" in ep3d[other].extraction_method
                    and math.dist(
                        tuple(ep3d[other].leader_start), tuple(ep3d[other].label_center)
                    ) > 2.0
                }
                if explicit_attached:
                    equivalent = explicit_attached
            if len(equivalent) > 1:
                equivalent_target_constraints[design_index] = equivalent
                reserved_ep3d_nodes.update(equivalent)
            else:
                selected_ep3d = next(iter(equivalent), ep3d_index)
                constraints[design_index] = selected_ep3d
                reserved_ep3d_nodes.add(selected_ep3d)
            reserved_design_nodes.add(design_index)
        used_design_edges.add(candidate["design_edge_index"])
        used_ep3d_edges.add(candidate["ep3d_edge_index"])
        audit["accepted_component_pairs"].append({
            "design_edge": [design_left, design_right],
            "ep3d_edge": [ep3d_left, ep3d_right],
            "cost": round(candidate["cost"], 6),
            "orientation": candidate["orientation"],
            "orientation_margin": round(candidate["orientation_margin"], 6),
            "mean_port_direction_score": round(
                candidate["mean_port_direction_score"], 6
            ),
            "mean_leader_direction_score": round(
                candidate["mean_leader_direction_score"], 6
            ),
        })
    audit["constraint_count"] = len(constraints)
    audit["equivalent_target_constraint_count"] = len(equivalent_target_constraints)
    audit["equivalent_target_constraints"] = [
        {"design_index": left, "ep3d_indices": sorted(rights)}
        for left, rights in sorted(equivalent_target_constraints.items())
    ]
    audit["physical_equivalent_ep3d_indices"] = sorted(physical_equivalent_targets)
    return (
        constraints, equivalent_target_constraints,
        physical_equivalent_targets, audit,
    )


def _explicit_compact_component_path_constraints(
    source: np.ndarray,
    target: np.ndarray,
    matrix: np.ndarray,
    design_graph: dict[str, Any],
    ep3d_graph: dict[str, Any],
    design_signatures: list[dict[str, Any]],
    ep3d_signatures: list[dict[str, Any]],
) -> tuple[dict[int, int], dict[str, Any]]:
    """Lock a long component path only when its directed ports identify it.

    Some design PDFs expose the actual compact fitting edges, while others
    expose only the complementary pipe gaps handled by
    :func:`_component_pair_constraints`.  When both exports contain an
    explicit run, its alternating component/pipe topology is highly stable.
    Geometry is deliberately secondary: a path is reserved only when four or
    more consecutive component pairs agree in direction and the best ordered
    alignment is clearly separated from every competing shift.
    """

    design_paths = _compact_component_paths(design_graph)
    ep3d_paths = _compact_component_paths(ep3d_graph)
    audit: dict[str, Any] = {
        "method": "explicit-directed-compact-component-path-alignment",
        "design_path_count": len(design_paths),
        "ep3d_path_count": len(ep3d_paths),
        "constraint_count": 0,
        "accepted_paths": [],
        "candidate_count": 0,
    }
    if not design_paths or not ep3d_paths:
        return {}, audit

    projected = _apply_affine(source, matrix)
    target_diagonal = float(np.linalg.norm(np.ptp(target, axis=0))) or 1.0

    def variants(path: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
        direct = [(int(left), int(right)) for left, right in path]
        reverse = [(right, left) for left, right in reversed(direct)]
        return [direct, reverse]

    candidates: list[dict[str, Any]] = []
    for design_path_index, raw_design_path in enumerate(design_paths):
        if len(raw_design_path) < 4:
            continue
        for ep3d_path_index, raw_ep3d_path in enumerate(ep3d_paths):
            if len(raw_ep3d_path) < 4:
                continue
            for design_path in variants(raw_design_path):
                for ep3d_path in variants(raw_ep3d_path):
                    matched_length = min(len(design_path), len(ep3d_path))
                    if matched_length < 4:
                        continue
                    if len(design_path) <= len(ep3d_path):
                        windows = [
                            (design_path, ep3d_path[offset:offset + matched_length], 0, offset)
                            for offset in range(len(ep3d_path) - matched_length + 1)
                        ]
                    else:
                        windows = [
                            (design_path[offset:offset + matched_length], ep3d_path, offset, 0)
                            for offset in range(len(design_path) - matched_length + 1)
                        ]
                    for design_window, ep3d_window, design_offset, ep3d_offset in windows:
                        mapping: dict[int, int] = {}
                        direction_scores: list[float] = []
                        midpoint_residuals: list[float] = []
                        valid = True
                        for (design_left, design_right), (ep3d_left, ep3d_right) in zip(
                            design_window, ep3d_window
                        ):
                            direct_scores = [
                                _port_direction_score(
                                    design_signatures[design_left], ep3d_signatures[ep3d_left], matrix
                                ),
                                _port_direction_score(
                                    design_signatures[design_right], ep3d_signatures[ep3d_right], matrix
                                ),
                            ]
                            reverse_scores = [
                                _port_direction_score(
                                    design_signatures[design_left], ep3d_signatures[ep3d_right], matrix
                                ),
                                _port_direction_score(
                                    design_signatures[design_right], ep3d_signatures[ep3d_left], matrix
                                ),
                            ]
                            if any(value is None for value in direct_scores + reverse_scores):
                                valid = False
                                break
                            direct_score = float(np.mean(direct_scores))
                            reverse_score = float(np.mean(reverse_scores))
                            if direct_score >= reverse_score:
                                edge_mapping = {
                                    design_left: ep3d_left,
                                    design_right: ep3d_right,
                                }
                                direction_scores.append(direct_score)
                            else:
                                edge_mapping = {
                                    design_left: ep3d_right,
                                    design_right: ep3d_left,
                                }
                                direction_scores.append(reverse_score)
                            if (
                                set(mapping).intersection(edge_mapping)
                                or set(mapping.values()).intersection(edge_mapping.values())
                            ):
                                valid = False
                                break
                            mapping.update(edge_mapping)
                            design_midpoint = (
                                projected[design_left] + projected[design_right]
                            ) / 2.0
                            ep3d_midpoint = (
                                target[ep3d_left] + target[ep3d_right]
                            ) / 2.0
                            midpoint_residuals.append(
                                float(np.linalg.norm(design_midpoint - ep3d_midpoint))
                                / target_diagonal
                            )
                        if not valid:
                            continue
                        mean_direction = float(np.mean(direction_scores))
                        minimum_direction = float(min(direction_scores))
                        mean_midpoint = float(np.mean(midpoint_residuals))
                        score = 0.85 * (1.0 - mean_direction) + 0.15 * mean_midpoint
                        candidates.append({
                            "design_path_index": design_path_index,
                            "ep3d_path_index": ep3d_path_index,
                            "design_offset": design_offset,
                            "ep3d_offset": ep3d_offset,
                            "mapping": mapping,
                            "score": score,
                            "mean_direction_score": mean_direction,
                            "minimum_direction_score": minimum_direction,
                            "mean_midpoint_residual": mean_midpoint,
                            "matched_component_count": matched_length,
                        })

    # Reversing both stored path orientations can describe the same physical
    # mapping.  Collapse those duplicates before measuring candidate margin.
    unique_candidates: dict[tuple[tuple[int, int], ...], dict[str, Any]] = {}
    for candidate in candidates:
        key = tuple(sorted(candidate["mapping"].items()))
        previous = unique_candidates.get(key)
        if previous is None or candidate["score"] < previous["score"]:
            unique_candidates[key] = candidate
    candidates = sorted(unique_candidates.values(), key=lambda item: item["score"])
    audit["candidate_count"] = len(candidates)

    constraints: dict[int, int] = {}
    used_design_paths: set[int] = set()
    used_ep3d_paths: set[int] = set()
    for candidate in candidates:
        if (
            candidate["design_path_index"] in used_design_paths
            or candidate["ep3d_path_index"] in used_ep3d_paths
            or candidate["mean_direction_score"] < 0.90
            or candidate["minimum_direction_score"] < 0.78
            or candidate["mean_midpoint_residual"] > 0.20
        ):
            continue
        alternatives = [
            other for other in candidates
            if other is not candidate
            and (
                other["design_path_index"] == candidate["design_path_index"]
                or other["ep3d_path_index"] == candidate["ep3d_path_index"]
            )
        ]
        margin = (
            min(other["score"] for other in alternatives) - candidate["score"]
            if alternatives else float("inf")
        )
        if margin < 0.15:
            continue
        if (
            set(candidate["mapping"]).intersection(constraints)
            or set(candidate["mapping"].values()).intersection(constraints.values())
        ):
            continue
        constraints.update(candidate["mapping"])
        used_design_paths.add(candidate["design_path_index"])
        used_ep3d_paths.add(candidate["ep3d_path_index"])
        audit["accepted_paths"].append({
            "design_path_index": candidate["design_path_index"],
            "ep3d_path_index": candidate["ep3d_path_index"],
            "design_offset": candidate["design_offset"],
            "ep3d_offset": candidate["ep3d_offset"],
            "matched_component_count": candidate["matched_component_count"],
            "mean_direction_score": round(candidate["mean_direction_score"], 6),
            "minimum_direction_score": round(candidate["minimum_direction_score"], 6),
            "mean_midpoint_residual": round(candidate["mean_midpoint_residual"], 6),
            "candidate_margin": (
                round(margin, 6) if math.isfinite(margin) else None
            ),
            "constraints": [
                {"design_index": left, "ep3d_index": right}
                for left, right in sorted(candidate["mapping"].items())
            ],
        })
    audit["constraint_count"] = len(constraints)
    return constraints, audit


def _component_pair_constraints(
    source: np.ndarray,
    target: np.ndarray,
    matrix: np.ndarray,
    design_graph: dict[str, Any],
    ep3d_graph: dict[str, Any],
    design_signatures: list[dict[str, Any]] | None = None,
    ep3d_signatures: list[dict[str, Any]] | None = None,
) -> tuple[dict[int, int], dict[str, Any]]:
    """Align repeated compact component pairs as ordered topology motifs.

    EP3D draws elbow bodies very compactly.  In the design PDF the coarse
    graph usually contains the straight pipe pieces and leaves the expanded
    elbow body as the complementary gap between their endpoints.  Candidate
    design gaps therefore use an adaptive page scale and are solved in target
    path order with explicit gaps.
    """

    target_paths = _compact_component_paths(ep3d_graph)
    projected = _apply_affine(source, matrix)
    source_diagonal = float(np.linalg.norm(np.ptp(source, axis=0))) or 1.0
    target_diagonal = float(np.linalg.norm(np.ptp(target, axis=0))) or 1.0
    maximum_design_edge = min(60.0, max(22.0, source_diagonal * 0.09))
    shared_design_edges: set[tuple[int, int]] = set()
    shared_nodes: set[int] = set()
    for edge in design_graph.get("edges", []):
        left, right = (int(value) for value in edge["nodes"])
        if "shared-process-segment" in edge.get("evidence", []):
            shared_design_edges.add(tuple(sorted((left, right))))
            shared_nodes.update((left, right))

    # On the design PDF the recovered graph edges are normally the straight
    # pipe pieces *between* elbows.  The fitting is the complementary gap from
    # the exit of one piece to the entry of the next.  Using the existing
    # shared edge itself caused a systematic one-port shift on P9.
    design_edges = []
    for left in range(len(source)):
        for right in range(left + 1, len(source)):
            if left not in shared_nodes and right not in shared_nodes:
                continue
            if (left, right) in shared_design_edges:
                continue
            length = float(np.linalg.norm(source[left] - source[right]))
            if 4.0 <= length <= maximum_design_edge:
                design_edges.append((left, right))

    constraints: dict[int, int] = {}
    explicit_path_audit: dict[str, Any] = {
        "method": "explicit-directed-compact-component-path-alignment",
        "constraint_count": 0,
        "accepted_paths": [],
    }
    if design_signatures is not None and ep3d_signatures is not None:
        constraints, explicit_path_audit = _explicit_compact_component_path_constraints(
            source, target, matrix,
            design_graph, ep3d_graph,
            design_signatures, ep3d_signatures,
        )
    path_audit = []

    def orient_edge_sequence(
        edges: list[tuple[int, int]], points: np.ndarray
    ) -> list[tuple[int, int]]:
        oriented = []
        for index, (left, right) in enumerate(edges):
            direct_cost = reverse_cost = 0.0
            if index > 0:
                previous = edges[index - 1]
                direct_cost += min(float(np.linalg.norm(points[left] - points[value])) for value in previous)
                reverse_cost += min(float(np.linalg.norm(points[right] - points[value])) for value in previous)
            if index + 1 < len(edges):
                following = edges[index + 1]
                direct_cost += min(float(np.linalg.norm(points[right] - points[value])) for value in following)
                reverse_cost += min(float(np.linalg.norm(points[left] - points[value])) for value in following)
            oriented.append((left, right) if direct_cost <= reverse_cost else (right, left))
        return oriented

    for target_path in target_paths:
        oriented_target_path = orient_edge_sequence(target_path, target)
        target_midpoints = np.asarray(
            [(target[left] + target[right]) / 2.0 for left, right in target_path], dtype=float
        )
        candidates = []
        for left, right in design_edges:
            if left in constraints or right in constraints:
                continue
            midpoint = (projected[left] + projected[right]) / 2.0
            path_distance, position = _polyline_position(midpoint, target_midpoints)
            if path_distance / target_diagonal > 0.16:
                continue
            candidates.append((position, left, right))
        candidates.sort(key=lambda item: item[0])
        if len(candidates) < 2:
            continue

        rows, columns = len(target_path), len(candidates)
        edge_cost = np.full((rows, columns), 1.0, dtype=float)
        for row, (target_left, target_right) in enumerate(target_path):
            target_midpoint = (target[target_left] + target[target_right]) / 2.0
            for column, (_, design_left, design_right) in enumerate(candidates):
                design_midpoint = (projected[design_left] + projected[design_right]) / 2.0
                midpoint_cost = float(np.linalg.norm(design_midpoint - target_midpoint)) / target_diagonal
                direct = float(np.linalg.norm(projected[design_left] - target[target_left])) + float(
                    np.linalg.norm(projected[design_right] - target[target_right])
                )
                reverse = float(np.linalg.norm(projected[design_left] - target[target_right])) + float(
                    np.linalg.norm(projected[design_right] - target[target_left])
                )
                endpoint_cost = min(direct, reverse) / (2.0 * target_diagonal)
                edge_cost[row, column] = 0.68 * midpoint_cost + 0.32 * endpoint_cost

        # Ordered dynamic alignment: target component gaps cost 0.14 while
        # surplus design edges may be skipped freely.  This localizes missing
        # welds without allowing a later elbow to consume an earlier pair.
        gap_cost = 0.14
        dp = np.full((rows + 1, columns + 1), float("inf"), dtype=float)
        action: dict[tuple[int, int], str] = {}
        dp[0, :] = 0.0
        for row in range(1, rows + 1):
            dp[row, 0] = dp[row - 1, 0] + gap_cost
            action[(row, 0)] = "target-gap"
        for row in range(1, rows + 1):
            for column in range(1, columns + 1):
                options = [
                    (dp[row, column - 1], "skip-design"),
                    (dp[row - 1, column] + gap_cost, "target-gap"),
                    (dp[row - 1, column - 1] + edge_cost[row - 1, column - 1], "match"),
                ]
                dp[row, column], action[(row, column)] = min(options, key=lambda item: item[0])
        matches = []
        row, column = rows, columns
        while row > 0 or column > 0:
            choice = action.get((row, column), "skip-design")
            if choice == "match":
                matches.append((row - 1, column - 1))
                row -= 1
                column -= 1
            elif choice == "target-gap":
                row -= 1
            else:
                column -= 1
        matches.reverse()
        # A short run is too easy to align one component out of phase.  Keep
        # it as soft node evidence; only four or more ordered component pairs
        # may reserve endpoints globally.
        # These pairs become hard endpoint reservations below, so every edge
        # in the run must be a strong geometric/topological observation.  A
        # merely gap-cheaper edge is not enough: on schematically stretched
        # sheets it can shift a whole elbow chain (Indonesia P94/P122).
        hard_constraint_cost = min(gap_cost, 0.08)
        if len(matches) < 4 or any(
            edge_cost[row, column] >= hard_constraint_cost for row, column in matches
        ):
            continue
        matched_design_edges = [
            (candidates[column][1], candidates[column][2]) for _, column in matches
        ]
        oriented_design_edges = orient_edge_sequence(matched_design_edges, source)
        accepted_pairs = []
        for match_index, (row, column) in enumerate(matches):
            target_left, target_right = oriented_target_path[row]
            design_left, design_right = oriented_design_edges[match_index]
            if any(
                key in constraints or value in constraints.values()
                for key, value in ((design_left, target_left), (design_right, target_right))
            ):
                continue
            constraints[design_left] = target_left
            constraints[design_right] = target_right
            accepted_pairs.append(
                {
                    "design_edge": [design_left, design_right],
                    "ep3d_edge": [target_left, target_right],
                    "edge_cost": round(float(edge_cost[row, column]), 6),
                }
            )
        if accepted_pairs:
            path_audit.append(
                {
                    "target_component_count": len(target_path),
                    "candidate_design_edge_count": len(candidates),
                    "accepted_component_pairs": accepted_pairs,
                }
            )
    return constraints, {
        "method": "ordered-compact-component-pair-chain-alignment",
        "constraint_count": len(constraints),
        "explicit_path_alignment": explicit_path_audit,
        "paths": path_audit,
    }


def _match_low_cardinality_callouts(
    design: list[PdfWeldCallout],
    ep3d: list[PdfWeldCallout],
    *,
    design_skeleton: dict[str, Any] | None,
    ep3d_skeleton: dict[str, Any] | None,
    design_landmarks: Iterable[dict[str, Any]],
    ep3d_landmarks: Iterable[dict[str, Any]],
    design_page: fitz.Page | None,
    ep3d_page: fitz.Page | None,
) -> dict[str, Any] | None:
    """Match one or two simple endpoints without requiring a point cloud.

    Three points are needed to estimate a free affine transform.  One/two-weld
    sheets instead use the known common north orientation, semantic port
    signatures and mutually unique endpoint ordering.  Displayed weld numbers
    are never included in the score.
    """

    count = len(design)
    if count not in {1, 2} or len(ep3d) != count:
        return None
    landmark_audit = stable_landmark_orientation_audit(design_landmarks, ep3d_landmarks)
    if (
        int(landmark_audit.get("common_landmark_count", 0)) < 2
        or float(landmark_audit.get("axis_order_agreement") or 0.0) < 0.85
    ):
        return None
    source = np.asarray([item.weld_point for item in design], dtype=float)
    target = np.asarray([item.weld_point for item in ep3d], dtype=float)
    design_signatures = extract_local_port_signatures(design, design_skeleton, page=design_page)
    ep3d_signatures = extract_local_port_signatures(ep3d, ep3d_skeleton, page=ep3d_page)
    ep3d_weld_list_semantics = (
        extract_ep3d_weld_list_semantics(ep3d_page)
        if ep3d_page is not None else {}
    )
    for index, callout in enumerate(ep3d):
        semantics = ep3d_weld_list_semantics.get(callout.label.upper())
        if semantics is not None:
            ep3d_signatures[index]["weld_list_semantics"] = dict(semantics)
    design_graph = build_pdf_callout_topology(design, design_skeleton, design_signatures)
    ep3d_graph = build_pdf_callout_topology(ep3d, ep3d_skeleton, ep3d_signatures)
    permutations = [(0,)] if count == 1 else [(0, 1), (1, 0)]
    ranked = []
    for permutation in permutations:
        paired_target = target[list(permutation)]
        if count == 1:
            linear = np.eye(2, dtype=float)
        else:
            source_delta = source[1] - source[0]
            target_delta = paired_target[1] - paired_target[0]
            common_scale = float(np.linalg.norm(target_delta) / max(np.linalg.norm(source_delta), 1e-6))
            scales = []
            order_penalty = 0.0
            for axis in range(2):
                if abs(source_delta[axis]) >= 5.0 and abs(target_delta[axis]) >= 5.0:
                    if source_delta[axis] * target_delta[axis] < 0.0:
                        order_penalty += 0.65
                    scales.append(abs(float(target_delta[axis] / source_delta[axis])))
                else:
                    scales.append(common_scale)
            linear = np.diag(np.maximum(scales, 1e-4))
        offset = np.mean(paired_target, axis=0) - np.mean(source, axis=0) @ linear
        matrix = np.vstack((linear, offset))
        pair_rows = []
        total = order_penalty if count == 2 else 0.0
        valid = True
        for left, right in enumerate(permutation):
            semantic = _semantic_pair_evidence(
                design_signatures[left], ep3d_signatures[right], matrix,
                design_graph["addresses"][left], ep3d_graph["addresses"][right],
            )
            if int(semantic["hard_violation_count"]) > 0:
                valid = False
                total += 100.0
            direction = semantic.get("port_direction_score")
            shape_score = float(semantic.get("component_shape_score", 0.0))
            ray_score = float(semantic.get("ray_count_score", 0.0))
            if count == 1 and not (
                shape_score >= 0.95
                and ray_score >= 0.99
                and direction is not None
                and float(direction) >= 0.55
            ):
                valid = False
            total += 0.44 * (1.0 - shape_score) + 0.22 * (1.0 - ray_score)
            total += 0.20 * (0.45 if direction is None else 1.0 - float(direction))
            pair_rows.append((left, right, semantic))
        ranked.append((total, valid, permutation, matrix, pair_rows))
    ranked.sort(key=lambda item: item[0])
    best = ranked[0]
    uniqueness_margin = (ranked[1][0] - best[0]) if len(ranked) > 1 else 1.0
    if not best[1] or (count == 2 and uniqueness_margin < 0.08):
        return None
    _, _, permutation, matrix, pair_rows = best
    projected = _apply_affine(source, matrix)
    diagonal = float(np.linalg.norm(np.ptp(target, axis=0))) or 1.0
    matches = []
    for left, right, semantic in pair_rows:
        residual = float(np.linalg.norm(projected[left] - target[right]))
        matches.append({
            "design_index": left,
            "ep3d_index": right,
            "design_label": design[left].label,
            "ep3d_label": ep3d[right].label,
            "design_point": list(design[left].weld_point),
            "ep3d_point": list(ep3d[right].weld_point),
            "projected_design_point": projected[left].tolist(),
            "residual": round(residual, 3),
            "normalized_residual": round(residual / diagonal, 5),
            "global_normalized_residual": round(residual / diagonal, 5),
            "local_topology_score": 1.0,
            "rank_order_score": 1.0,
            "design_topology_address": _mst_addresses(source)[left],
            "ep3d_topology_address": _mst_addresses(target)[right],
            "design_pdf_graph_address": design_graph["addresses"][left],
            "ep3d_pdf_graph_address": ep3d_graph["addresses"][right],
            "design_port_signature": design_signatures[left],
            "ep3d_port_signature": ep3d_signatures[right],
            "semantic_gate": semantic,
            "forced_unique": True,
            "forced_uniqueness_margin": round(float(uniqueness_margin), 6),
            "assignment_cost": round(float(best[0]), 6),
            "confidence": "medium",
            "match_method": "low-cardinality-north-port-semantic-ordering",
            "number_used_as_identity": False,
        })
    return {
        "status": "candidate-mapping",
        "affine_matrix": matrix.tolist(),
        "orientation_lock": {
            "north_direction_same": True, "axis_swap_allowed": False,
            "mirror_allowed": False, "affine_determinant": round(float(np.linalg.det(matrix[:2, :].T)), 6),
        },
        "sequence_alignment": {
            "method": "low-cardinality-north-port-semantic-ordering",
            "forced_full_cardinality": True,
            "forced_unique_acceptance_required": True,
            "permutation_margin": round(float(uniqueness_margin), 6),
        },
        "transform_source": "low-cardinality-positive-axis-transform",
        "stable_landmark_audit": landmark_audit,
        "design_port_signatures": design_signatures,
        "ep3d_port_signatures": ep3d_signatures,
        "ep3d_weld_list_semantics": ep3d_weld_list_semantics,
        "design_callout_graph": design_graph,
        "ep3d_callout_graph": ep3d_graph,
        "unmatched_gap_classification": {"design": {}, "ep3d": {}},
        "matches": sorted(matches, key=lambda item: _label_text_sort_key(item["design_label"])),
        "unmatched_design": [],
        "unmatched_ep3d": [],
        "summary": {
            "design_callout_count": count,
            "ep3d_callout_count": count,
            "accepted_match_count": count,
            "high_or_medium_count": count,
            "semantic_conflict_rejection_count": 0,
            "forced_unique_match_count": count,
            "low_cardinality_match_count": count,
        },
    }


def match_weld_callout_topology(
    design_callouts: Iterable[PdfWeldCallout],
    ep3d_callouts: Iterable[PdfWeldCallout],
    *,
    design_skeleton: dict[str, Any] | None = None,
    ep3d_skeleton: dict[str, Any] | None = None,
    design_landmarks: Iterable[dict[str, Any]] = (),
    ep3d_landmarks: Iterable[dict[str, Any]] = (),
    design_page: fitz.Page | None = None,
    ep3d_page: fitz.Page | None = None,
    design_port_signatures: list[dict[str, Any]] | None = None,
    ep3d_port_signatures: list[dict[str, Any]] | None = None,
    enable_component_pair_constraints: bool = True,
) -> dict[str, Any]:
    """Match two callout graphs with semantics, explicit gaps and uniqueness.

    Displayed labels are deliberately excluded from the cost function.
    This project has identical north and sheet orientation in both exports, so
    reflected/axis-swapped hypotheses are invalid even when their point-cloud
    residual happens to be smaller.  A continuation-terminal mismatch is a
    hard rejection: it must become a page-boundary gap rather than shifting
    every later weld by one position.
    """

    design = list(design_callouts)
    ep3d = list(ep3d_callouts)
    low_cardinality = _match_low_cardinality_callouts(
        design, ep3d,
        design_skeleton=design_skeleton,
        ep3d_skeleton=ep3d_skeleton,
        design_landmarks=design_landmarks,
        ep3d_landmarks=ep3d_landmarks,
        design_page=design_page,
        ep3d_page=ep3d_page,
    )
    if low_cardinality is not None:
        return low_cardinality
    if len(design) < 3 or len(ep3d) < 3:
        # An unequal low-cardinality page cannot estimate a page-local
        # transform, but its ports are still first-class nodes in the
        # whole-line graph.  Preserve signatures, explicit SEE ISO ownership
        # and WELD LIST semantics instead of returning an evidence-free gap.
        # This is essential for singleton construction weld sheets whose
        # physical counterpart belongs to an adjacent design drawing.
        design_signatures = design_port_signatures or extract_local_port_signatures(
            design, design_skeleton, page=design_page
        )
        ep3d_signatures = ep3d_port_signatures or extract_local_port_signatures(
            ep3d, ep3d_skeleton, page=ep3d_page
        )
        ep3d_weld_list_semantics = (
            extract_ep3d_weld_list_semantics(ep3d_page)
            if ep3d_page is not None else {}
        )
        for index, callout in enumerate(ep3d):
            semantics = ep3d_weld_list_semantics.get(callout.label.upper())
            if semantics is not None:
                ep3d_signatures[index]["weld_list_semantics"] = dict(semantics)
        design_graph = build_pdf_callout_topology(
            design, design_skeleton, design_signatures
        )
        ep3d_graph = build_pdf_callout_topology(
            ep3d, ep3d_skeleton, ep3d_signatures
        )
        return {
            "status": "insufficient-callouts",
            "transform_source": "deferred-to-whole-line-low-cardinality",
            "design_port_signatures": design_signatures,
            "ep3d_port_signatures": ep3d_signatures,
            "ep3d_weld_list_semantics": ep3d_weld_list_semantics,
            "design_callout_graph": design_graph,
            "ep3d_callout_graph": ep3d_graph,
            "matches": [],
            "unmatched_design": [item.label for item in design],
            "unmatched_ep3d": [item.label for item in ep3d],
            "summary": {
                "design_callout_count": len(design),
                "ep3d_callout_count": len(ep3d),
                "accepted_match_count": 0,
                "high_or_medium_count": 0,
            },
        }
    source = np.asarray([item.weld_point for item in design], dtype=float)
    target = np.asarray([item.weld_point for item in ep3d], dtype=float)
    diagonal = float(np.linalg.norm(np.ptp(target, axis=0))) or 1.0
    primary_design_indices = [
        index for index, item in enumerate(design)
        if not item.label.upper().startswith("RP")
    ]
    if len(primary_design_indices) >= 3 and len(primary_design_indices) < len(design):
        # RP callouts are valid secondary weld annotations, but inserting one
        # into a dense co-located port cluster must not renumber the x/y ranks
        # of all established F/FS ports.  Compute primary ranks first and only
        # interpolate RP's own rank.  RP remains fully eligible for matching.
        primary_points = source[primary_design_indices]
        primary_ranks = _rank_coordinates(primary_points)
        source_ranks = np.zeros_like(source, dtype=float)
        source_ranks[primary_design_indices] = primary_ranks
        deferred = set(range(len(design))) - set(primary_design_indices)
        for index in deferred:
            for axis in range(2):
                source_ranks[index, axis] = float(
                    np.count_nonzero(primary_points[:, axis] < source[index, axis])
                ) / max(1, len(primary_points) - 1)
        source_ranks = np.clip(source_ranks, 0.0, 1.0)
    else:
        source_ranks = _rank_coordinates(source)
    target_ranks = _rank_coordinates(target)
    source_addresses, target_addresses = _mst_addresses(source), _mst_addresses(target)
    design_signatures = design_port_signatures or extract_local_port_signatures(
        design, design_skeleton, page=design_page
    )
    ep3d_signatures = ep3d_port_signatures or extract_local_port_signatures(
        ep3d, ep3d_skeleton, page=ep3d_page
    )
    ep3d_weld_list_semantics = (
        extract_ep3d_weld_list_semantics(ep3d_page)
        if ep3d_page is not None else {}
    )
    for index, callout in enumerate(ep3d):
        semantics = ep3d_weld_list_semantics.get(callout.label.upper())
        if semantics is not None:
            ep3d_signatures[index]["weld_list_semantics"] = dict(semantics)
    design_graph = build_pdf_callout_topology(design, design_skeleton, design_signatures)
    ep3d_graph = build_pdf_callout_topology(ep3d, ep3d_skeleton, ep3d_signatures)
    design_signatures, design_port_recovery = _refine_compact_component_ports(
        design, design_signatures, design_graph
    )
    ep3d_signatures, ep3d_port_recovery = _refine_compact_component_ports(
        ep3d, ep3d_signatures, ep3d_graph
    )
    # Addresses contain the semantic classes, so rebuild after the fitting-side
    # rays have converted false terminals into physical turn/through ports.
    design_graph = build_pdf_callout_topology(design, design_skeleton, design_signatures)
    ep3d_graph = build_pdf_callout_topology(ep3d, ep3d_skeleton, ep3d_signatures)
    (
        clustered_secondary_design_indices,
        clustered_secondary_design_audit,
    ) = _clustered_secondary_design_annotations(
        design, design_signatures, design_graph
    )
    for index in clustered_secondary_design_indices:
        design_signatures[index]["secondary_component_annotation"] = True
        design_signatures[index]["deferred_from_page_assignment"] = True
    (
        secondary_coincident_ep3d_indices,
        secondary_coincident_ep3d_audit,
    ) = _secondary_coincident_ep3d_labels(ep3d, target, ep3d_signatures)
    (
        continuation_terminal_constraints,
        continuation_terminal_constraint_audit,
    ) = _unique_continuation_terminal_constraints(
        design_signatures, ep3d_signatures
    )

    # Candidate transforms are deliberately restricted to the known page
    # handedness.  Skeleton extents are independent of callout cardinality and
    # are therefore particularly useful when a continuation weld belongs to
    # only one of the two sheets.
    transform_candidates: list[tuple[str, np.ndarray]] = [
        ("callout-bbox", _bbox_transform(source, target, False, False, False))
    ]
    one_sided_ep3d_terminals = [
        index for index, signature in enumerate(ep3d_signatures)
        if bool(signature.get("continuation_interface"))
        and str(signature.get("structure_class")) == "continuation-terminal"
        and int(signature.get("ray_count", 0)) == 1
    ]
    if (
        not any(
            bool(signature.get("continuation_interface"))
            for signature in design_signatures
        )
        and len(one_sided_ep3d_terminals) == 1
        and len(target) - 1 >= 3
    ):
        retained_target = np.delete(target, one_sided_ep3d_terminals[0], axis=0)
        transform_candidates.append((
            "callout-bbox-with-one-sided-continuation-gap",
            _bbox_transform(source, retained_target, False, False, False),
        ))
    robust_matrix = transform_candidates[0][1]
    for _ in range(12):
        robust_projected = _apply_affine(source, robust_matrix)
        robust_distance = np.linalg.norm(
            robust_projected[:, None, :] - target[None, :, :], axis=2
        )
        robust_left, robust_right = linear_sum_assignment(robust_distance)
        robust_pairs = list(zip(robust_left.tolist(), robust_right.tolist()))
        ranked_pairs = sorted(robust_pairs, key=lambda pair: robust_distance[pair])
        keep_count = max(3, min(len(ranked_pairs), int(round(min(len(source), len(target)) * 0.78))))
        fit_pairs = ranked_pairs[:keep_count]
        updated = _fit_affine(
            source[[left for left, _ in fit_pairs]], target[[right for _, right in fit_pairs]]
        )
        updated_determinant = float(np.linalg.det(updated[:2, :].T))
        if updated_determinant <= 0.03:
            break
        if np.max(np.abs(updated - robust_matrix)) < 1e-5:
            robust_matrix = updated
            break
        robust_matrix = updated
    if float(np.linalg.det(robust_matrix[:2, :].T)) > 0.03:
        transform_candidates.append(("trimmed-callout-affine", robust_matrix))
    design_segment_points = np.asarray(
        [point for segment in (design_skeleton or {}).get("segments", []) for point in (segment["start"], segment["end"])],
        dtype=float,
    )
    ep3d_segment_points = np.asarray(
        [point for segment in (ep3d_skeleton or {}).get("segments", []) for point in (segment["start"], segment["end"])],
        dtype=float,
    )
    if len(design_segment_points) >= 3 and len(ep3d_segment_points) >= 3:
        transform_candidates.append(
            ("process-skeleton-bbox", _bbox_transform(design_segment_points, ep3d_segment_points, False, False, False))
        )
    design_landmark_map = {item["key"]: item["paper_coordinate"] for item in design_landmarks}
    ep3d_landmark_map = {item["key"]: item["paper_coordinate"] for item in ep3d_landmarks}
    common_landmarks = sorted(set(design_landmark_map) & set(ep3d_landmark_map))
    landmark_audit = stable_landmark_orientation_audit(design_landmarks, ep3d_landmarks)
    if len(common_landmarks) >= 3:
        landmark_matrix = _fit_affine(
            np.asarray([design_landmark_map[key] for key in common_landmarks], dtype=float),
            np.asarray([ep3d_landmark_map[key] for key in common_landmarks], dtype=float),
        )
        if float(np.linalg.det(landmark_matrix[:2, :].T)) > 0.0:
            transform_candidates.append(("stable-landmark-affine", landmark_matrix))

    def graph_neighbours(graph: dict[str, Any]) -> list[set[int]]:
        result = [set() for _ in range(int(graph["node_count"]))]
        for edge in graph.get("edges", []):
            left, right = (int(value) for value in edge["nodes"])
            result[left].add(right)
            result[right].add(left)
        return result

    design_neighbours, ep3d_neighbours = graph_neighbours(design_graph), graph_neighbours(ep3d_graph)

    def build_cost(
        matrix: np.ndarray,
        projected_override: np.ndarray | None = None,
    ) -> tuple[np.ndarray, dict[tuple[int, int], dict[str, Any]]]:
        global_projected = _apply_affine(source, matrix)
        projected = global_projected if projected_override is None else projected_override
        spatial = np.linalg.norm(projected[:, None, :] - target[None, :, :], axis=2) / diagonal
        global_spatial = np.linalg.norm(global_projected[:, None, :] - target[None, :, :], axis=2) / diagonal
        result = np.zeros_like(spatial)
        evidence: dict[tuple[int, int], dict[str, Any]] = {}
        for left in range(len(design)):
            for right in range(len(ep3d)):
                semantic = _semantic_pair_evidence(
                    design_signatures[left], ep3d_signatures[right], matrix,
                    design_graph["addresses"][left], ep3d_graph["addresses"][right],
                )
                rank_gap = float(np.mean(np.abs(source_ranks[left] - target_ranks[right])))
                direction_score = semantic["port_direction_score"]
                direction_penalty = 0.35 if direction_score is None else 1.0 - float(direction_score)
                ray_penalty = 0.45 if (
                    design_signatures[left]["ray_count"] == 0 or ep3d_signatures[right]["ray_count"] == 0
                ) else 1.0 - float(semantic["ray_count_score"])
                pair_cost = (
                    0.65 * float(spatial[left, right])
                    + 0.24 * rank_gap
                    + 0.04 * ray_penalty
                    + 0.04 * direction_penalty
                    + 0.03 * (1.0 - float(semantic["graph_degree_score"]))
                )
                if (
                    bool(semantic["component_shapes_validated"])
                    and
                    design_signatures[left].get("shape_class") in {"flange-like", "tee-or-olet", "elbow-like"}
                    and ep3d_signatures[right].get("shape_class") in {"flange-like", "tee-or-olet", "elbow-like"}
                ):
                    pair_cost += 0.06 * (1.0 - float(semantic["component_shape_score"]))
                if int(semantic["hard_violation_count"]) > 0:
                    pair_cost = 100.0
                if left in clustered_secondary_design_indices:
                    pair_cost = 100.0
                if (
                    right in secondary_coincident_ep3d_indices
                    and not bool(design_signatures[left].get("continuation_interface"))
                ):
                    # The explicit callout owns the ordinary physical port.
                    # A coincident inherited compound identity remains
                    # eligible only for a genuine design continuation/open
                    # interface and therefore cannot shift a fitting chain.
                    pair_cost = 100.0
                shared_nearby_equipment = [
                    key for key in common_landmarks
                    if re.fullmatch(r"\d+[A-Z]{2,}\d+[A-Z0-9]*", key)
                    and math.dist(tuple(source[left]), tuple(design_landmark_map[key])) <= 120.0
                    and math.dist(tuple(target[right]), tuple(ep3d_landmark_map[key])) <= 120.0
                ]
                result[left, right] = pair_cost
                evidence[(left, right)] = {
                    "normalized_residual": float(spatial[left, right]),
                    "global_normalized_residual": float(global_spatial[left, right]),
                    "rank_order_score": 1.0 - rank_gap,
                    "semantic_gate": semantic,
                    "base_assignment_cost": float(pair_cost),
                    "projected_design_point": projected[left].tolist(),
                    "shared_nearby_equipment_landmarks": shared_nearby_equipment,
                    "secondary_coincident_ep3d_label": (
                        right in secondary_coincident_ep3d_indices
                    ),
                }
        return result, evidence

    # Two explicit gaps must remain more expensive than one geometrically
    # plausible but non-metric ISO match.  Hard semantic conflicts still cost
    # 100 and therefore always become gaps (for example P12 FS21 ownership).
    gap_cost = 0.45
    ranked_transforms = []
    for transform_source, candidate_matrix in transform_candidates:
        candidate_cost, candidate_evidence = build_cost(candidate_matrix)
        candidate_pairs, candidate_total = _augmented_assignment(candidate_cost, gap_cost)
        ranked_transforms.append(
            (candidate_total, -len(candidate_pairs), transform_source, candidate_matrix, candidate_cost, candidate_evidence)
        )
    transform_hypotheses = [
        {
            "source": item[2],
            "gap_aware_total_cost": round(float(item[0]), 6),
            "assigned_pair_count": int(-item[1]),
            "matrix": item[3].tolist(),
        }
        for item in sorted(ranked_transforms, key=lambda value: (value[0], value[1]))
    ]
    _, _, transform_source, matrix, real_cost, pair_evidence = min(
        ranked_transforms, key=lambda item: (item[0], item[1])
    )
    determinant = float(np.linalg.det(matrix[:2, :].T))

    (
        continuation_boundary_constraints,
        continuation_boundary_constraint_audit,
    ) = _mutual_continuation_boundary_constraints(
        source, target, matrix,
        design_signatures, ep3d_signatures,
        design_graph, ep3d_graph, diagonal,
    )
    # Propagate only the physical-interface role across a mutually unique
    # registered boundary.  The local structure class remains unchanged, so
    # a through/turn/flange signature still participates in material and port
    # semantics.  Rebuild the chosen-transform costs so the corresponding
    # continuation-vs-physical hard conflict is removed for this proven pair.
    for left, right in continuation_boundary_constraints.items():
        if not bool(design_signatures[left].get("continuation_interface")):
            design_signatures[left]["continuation_interface"] = True
            design_signatures[left]["inferred_continuation_counterpart"] = True
        if not bool(ep3d_signatures[right].get("continuation_interface")):
            ep3d_signatures[right]["continuation_interface"] = True
            ep3d_signatures[right]["inferred_continuation_counterpart"] = True
    if continuation_boundary_constraints:
        real_cost, pair_evidence = build_cost(matrix)
    deferred_continuation_secondary_design = set(
        continuation_boundary_constraint_audit.get(
            "deferred_secondary_design_indices", []
        )
    )
    deferred_continuation_secondary_ep3d = set(
        continuation_boundary_constraint_audit.get(
            "deferred_secondary_ep3d_indices", []
        )
    )
    for index in deferred_continuation_secondary_design:
        design_signatures[index]["continuation_interface"] = True
        design_signatures[index]["deferred_cross_page_secondary"] = True
    for index in deferred_continuation_secondary_ep3d:
        ep3d_signatures[index]["continuation_interface"] = True
        ep3d_signatures[index]["deferred_cross_page_secondary"] = True

    (
        validated_component_constraints,
        equivalent_component_constraints,
        physical_equivalent_ep3d_targets,
        validated_component_alignment,
    ) = (
        _validated_component_pair_constraints(
            design, ep3d, source, target, matrix,
            design_graph, ep3d_graph, design_signatures, ep3d_signatures,
        )
    )
    component_constraints, component_pair_alignment = _component_pair_constraints(
        source, target, matrix, design_graph, ep3d_graph,
        design_signatures, ep3d_signatures,
    )
    if not enable_component_pair_constraints:
        # In a PCF-led route the authoritative component pairing is applied
        # later from the source topology.  PDF-inferred elbow/component locks
        # can otherwise compete with that evidence and shift an entire local
        # label chain before PCF reconciliation.  Continuation constraints
        # below remain enabled because they describe page ownership, not
        # material identity.
        validated_component_constraints = {}
        equivalent_component_constraints = {}
        physical_equivalent_ep3d_targets = set()
        component_constraints = {}
        validated_component_alignment = dict(validated_component_alignment) | {
            "status": "disabled-by-authoritative-source-topology"
        }
        component_pair_alignment = dict(component_pair_alignment) | {
            "status": "disabled-by-authoritative-source-topology"
        }
    # The directly observed, directed two-port motif is stronger than a long
    # schematic chain hypothesis.  Keep only non-conflicting chain locks.
    reserved_ep3d = set(validated_component_constraints.values())
    reserved_ep3d.update(
        right for rights in equivalent_component_constraints.values() for right in rights
    )
    reserved_ep3d.update(physical_equivalent_ep3d_targets)
    component_constraints = {
        left: right for left, right in component_constraints.items()
        if left not in validated_component_constraints
        and left not in equivalent_component_constraints
        and right not in reserved_ep3d
    }
    component_constraints.update(validated_component_constraints)
    # A unique referenced open terminal is a page-topology identity, not a
    # geometric component.  It receives the same one-to-one reservation
    # machinery so it cannot be consumed by a nearer physical flange.
    all_continuation_constraints = dict(continuation_terminal_constraints)
    reserved_continuation_targets = set(all_continuation_constraints.values())
    for left, right in continuation_boundary_constraints.items():
        if left in all_continuation_constraints or right in reserved_continuation_targets:
            continue
        all_continuation_constraints[left] = right
        reserved_continuation_targets.add(right)
    component_constraints = {
        left: right for left, right in component_constraints.items()
        if left not in all_continuation_constraints
        and right not in set(all_continuation_constraints.values())
    }
    component_constraints.update(all_continuation_constraints)

    def apply_component_constraints() -> None:
        for design_index, ep3d_index in component_constraints.items():
            for other_ep3d in range(len(ep3d)):
                if other_ep3d != ep3d_index:
                    real_cost[design_index, other_ep3d] = 100.0
            for other_design in range(len(design)):
                if other_design != design_index:
                    real_cost[other_design, ep3d_index] = 100.0
            real_cost[design_index, ep3d_index] = min(
                float(real_cost[design_index, ep3d_index]), 0.16
            )
            pair_evidence[(design_index, ep3d_index)]["component_pair_constraint"] = True
            if all_continuation_constraints.get(design_index) == ep3d_index:
                pair_evidence[(design_index, ep3d_index)][
                    "continuation_terminal_constraint"
                ] = True
        for design_index, ep3d_indices in equivalent_component_constraints.items():
            for other_ep3d in range(len(ep3d)):
                if other_ep3d not in ep3d_indices:
                    real_cost[design_index, other_ep3d] = 100.0
            for ep3d_index in ep3d_indices:
                for other_design in range(len(design)):
                    if other_design != design_index:
                        real_cost[other_design, ep3d_index] = 100.0
                real_cost[design_index, ep3d_index] = min(
                    float(real_cost[design_index, ep3d_index]), 0.16
                )
                pair_evidence[(design_index, ep3d_index)][
                    "equivalent_component_pair_constraint"
                ] = True
        assigned_component_targets = set(component_constraints.values()) | {
            right for rights in equivalent_component_constraints.values() for right in rights
        }
        for ep3d_index in physical_equivalent_ep3d_targets - assigned_component_targets:
            for design_index in range(len(design)):
                design_signature = design_signatures[design_index]
                ep3d_signature = ep3d_signatures[ep3d_index]
                compatible_secondary_interface = bool(
                    design_signature.get("continuation_interface")
                    and int(design_signature.get("ray_count", 0))
                    == int(ep3d_signature.get("ray_count", -1))
                )
                if not compatible_secondary_interface:
                    real_cost[design_index, ep3d_index] = 100.0
        for design_index in deferred_continuation_secondary_design:
            real_cost[design_index, :] = 100.0
            for ep3d_index in range(len(ep3d)):
                pair_evidence[(design_index, ep3d_index)][
                    "reserved_for_cross_page_topology"
                ] = True
        for ep3d_index in deferred_continuation_secondary_ep3d:
            real_cost[:, ep3d_index] = 100.0
            for design_index in range(len(design)):
                pair_evidence[(design_index, ep3d_index)][
                    "reserved_for_cross_page_topology"
                ] = True
        for design_index in clustered_secondary_design_indices:
            real_cost[design_index, :] = 100.0
            for ep3d_index in range(len(ep3d)):
                pair_evidence[(design_index, ep3d_index)][
                    "secondary_component_annotation"
                ] = True

    apply_component_constraints()
    preliminary_pairs, _ = _augmented_assignment(real_cost, gap_cost)
    (
        one_sided_continuation_gap_targets,
        one_sided_continuation_gap_audit,
    ) = _disruptive_one_sided_continuation_targets(
        preliminary_pairs, pair_evidence,
        design_signatures, ep3d_signatures,
    )

    def apply_one_sided_continuation_gaps() -> None:
        for ep3d_index in one_sided_continuation_gap_targets:
            real_cost[:, ep3d_index] = 100.0
            for design_index in range(len(design)):
                pair_evidence[(design_index, ep3d_index)][
                    "reserved_for_cross_page_topology"
                ] = True

    if one_sided_continuation_gap_targets:
        reranked_transforms = []
        for candidate_source, candidate_matrix in transform_candidates:
            candidate_cost, candidate_evidence = build_cost(candidate_matrix)
            real_cost, pair_evidence = candidate_cost, candidate_evidence
            apply_component_constraints()
            apply_one_sided_continuation_gaps()
            candidate_pairs, candidate_total = _augmented_assignment(
                real_cost, gap_cost
            )
            reranked_transforms.append((
                candidate_total, -len(candidate_pairs), candidate_source,
                candidate_matrix, real_cost.copy(), pair_evidence,
            ))
        (
            _, _, transform_source, matrix, real_cost, pair_evidence,
        ) = min(reranked_transforms, key=lambda item: (item[0], item[1]))
        determinant = float(np.linalg.det(matrix[:2, :].T))
        preliminary_pairs, _ = _augmented_assignment(real_cost, gap_cost)
        one_sided_continuation_gap_audit["transform_hypotheses_after_gap"] = [
            {
                "source": item[2],
                "gap_aware_total_cost": round(float(item[0]), 6),
                "assigned_pair_count": int(-item[1]),
            }
            for item in sorted(
                reranked_transforms, key=lambda value: (value[0], value[1])
            )
        ]
        one_sided_continuation_gap_audit["selected_transform_source"] = (
            transform_source
        )
    else:
        apply_one_sided_continuation_gaps()
    (
        same_point_joint_constraints,
        same_point_joint_audit,
    ) = _same_point_joint_path_constraints(
        design,
        ep3d,
        design_graph,
        preliminary_pairs,
        ep3d_signatures,
        excluded_design_indices=clustered_secondary_design_indices,
        reserved_design_indices=set(component_constraints),
        reserved_ep3d_indices=(
            set(component_constraints.values())
            | {right for rights in equivalent_component_constraints.values() for right in rights}
            | set(deferred_continuation_secondary_ep3d)
            | set(one_sided_continuation_gap_targets)
        ),
    )

    def apply_same_point_joint_constraints() -> None:
        for design_index, ep3d_index in same_point_joint_constraints.items():
            for other_ep3d in range(len(ep3d)):
                if other_ep3d != ep3d_index:
                    real_cost[design_index, other_ep3d] = 100.0
            for other_design in range(len(design)):
                if other_design != design_index:
                    real_cost[other_design, ep3d_index] = 100.0
            real_cost[design_index, ep3d_index] = min(
                float(real_cost[design_index, ep3d_index]), 0.12
            )
            pair_evidence[(design_index, ep3d_index)][
                "same_point_joint_path_constraint"
            ] = True

    apply_same_point_joint_constraints()
    preliminary_pairs, _ = _augmented_assignment(real_cost, gap_cost)
    directional_mst_constraints, directional_mst_audit = _directional_mst_constraints(
        source, target, matrix, preliminary_pairs, pair_evidence
    )
    component_reverse = {right: left for left, right in component_constraints.items()}
    same_point_joint_reverse = {
        right: left for left, right in same_point_joint_constraints.items()
    }
    directional_mst_constraints = {
        left: right
        for left, right in directional_mst_constraints.items()
        if component_constraints.get(left, right) == right
        and component_reverse.get(right, left) == left
        and left not in equivalent_component_constraints
        and right not in reserved_ep3d
        and same_point_joint_constraints.get(left, right) == right
        and same_point_joint_reverse.get(right, left) == left
    }
    directional_mst_audit["constraint_count_after_component_conflict_gate"] = len(
        directional_mst_constraints
    )

    def apply_directional_mst_constraints() -> None:
        for design_index, ep3d_index in directional_mst_constraints.items():
            for other_ep3d in range(len(ep3d)):
                if other_ep3d != ep3d_index:
                    real_cost[design_index, other_ep3d] = 100.0
            for other_design in range(len(design)):
                if other_design != design_index:
                    real_cost[other_design, ep3d_index] = 100.0
            real_cost[design_index, ep3d_index] = min(
                float(real_cost[design_index, ep3d_index]), 0.14
            )
            pair_evidence[(design_index, ep3d_index)]["directional_mst_constraint"] = True

    apply_directional_mst_constraints()
    preliminary_pairs, _ = _augmented_assignment(real_cost, gap_cost)

    # The first assignment supplies topology anchors only.  Correct the global
    # affine with their local residual field, then solve again.  Continuation
    # terminals and semantic conflicts never become warp anchors.
    piecewise_anchor_pairs = [
        (left, right)
        for left, right in preliminary_pairs
        if real_cost[left, right] < 0.32
        and int(pair_evidence[(left, right)]["semantic_gate"]["hard_violation_count"]) == 0
        and design_signatures[left]["structure_class"] != "continuation-terminal"
        and ep3d_signatures[right]["structure_class"] != "continuation-terminal"
        and float(pair_evidence[(left, right)]["rank_order_score"]) >= 0.65
        and (
            pair_evidence[(left, right)]["semantic_gate"]["status"] == "matched"
            or (
                float(pair_evidence[(left, right)]["semantic_gate"]["component_shape_score"]) >= 0.95
                and (
                    pair_evidence[(left, right)]["semantic_gate"]["port_direction_score"] is None
                    or float(pair_evidence[(left, right)]["semantic_gate"]["port_direction_score"]) >= 0.70
                )
            )
        )
    ]
    local_projected, piecewise_projection = _piecewise_topology_projection(
        source, target, matrix, piecewise_anchor_pairs
    )
    if piecewise_projection["applied"]:
        real_cost, pair_evidence = build_cost(matrix, local_projected)
        apply_component_constraints()
        apply_one_sided_continuation_gaps()
        apply_same_point_joint_constraints()
        apply_directional_mst_constraints()
        preliminary_pairs, _ = _augmented_assignment(real_cost, gap_cost)
        transform_source = f"{transform_source}+piecewise-topology"
    else:
        local_projected = _apply_affine(source, matrix)

    # Match stable structural nodes first, then assign every other node to the
    # nearest matched landmark zone.  Crossing a zone boundary is penalized,
    # which localizes a missing/extra weld instead of propagating an offset.
    structural_classes = {"continuation-terminal", "junction", "turn"}
    landmark_pairs = [
        (left, right)
        for left, right in preliminary_pairs
        if real_cost[left, right] < 0.42
        and design_signatures[left]["structure_class"] == ep3d_signatures[right]["structure_class"]
        and design_signatures[left]["structure_class"] in structural_classes
    ]

    def nearest_zone(index: int, points: np.ndarray, anchors: list[int]) -> int | None:
        if not anchors:
            return None
        return min(range(len(anchors)), key=lambda zone: float(np.linalg.norm(points[index] - points[anchors[zone]])))

    design_zone_anchors = [left for left, _ in landmark_pairs]
    ep3d_zone_anchors = [right for _, right in landmark_pairs]
    preliminary_map = {left: right for left, right in preliminary_pairs}
    for left in range(len(design)):
        for right in range(len(ep3d)):
            if real_cost[left, right] >= 100.0:
                continue
            design_zone = nearest_zone(left, source, design_zone_anchors)
            ep3d_zone = nearest_zone(right, target, ep3d_zone_anchors)
            if design_zone is not None and ep3d_zone is not None and design_zone != ep3d_zone:
                real_cost[left, right] += 0.04
            mapped_neighbours = {
                preliminary_map[value] for value in design_neighbours[left] if value in preliminary_map
            }
            if mapped_neighbours:
                overlap = len(mapped_neighbours & ep3d_neighbours[right]) / max(1, len(mapped_neighbours))
                real_cost[left, right] += 0.03 * (1.0 - overlap)
                pair_evidence[(left, right)]["graph_neighbour_score"] = round(overlap, 3)
            else:
                pair_evidence[(left, right)]["graph_neighbour_score"] = None
            pair_evidence[(left, right)]["design_segment_zone"] = design_zone
            pair_evidence[(left, right)]["ep3d_segment_zone"] = ep3d_zone

    dominant_chain_pairs, dominant_chain_audit = _dominant_chain_sequence_audit(
        local_projected, target, real_cost, gap_cost
    )
    dominant_chain_audit["pairs"] = [
        {
            "design_index": left,
            "design_label": design[left].label,
            "ep3d_index": right,
            "ep3d_label": ep3d[right].label,
            "cost": round(float(real_cost[left, right]), 6),
        }
        for left, right in dominant_chain_pairs
    ]
    assigned_pairs, best_total = _augmented_assignment(real_cost, gap_cost)
    provisional_map = {left: right for left, right in assigned_pairs}
    chain_agreement_count = sum(
        provisional_map.get(left) == right
        for left, right in dominant_chain_pairs
    )
    chain_changed_count = len(dominant_chain_pairs) - chain_agreement_count
    chain_agreement = (
        chain_agreement_count / len(dominant_chain_pairs)
        if dominant_chain_pairs else 0.0
    )
    dominant_chain_audit.update({
        "provisional_agreement_count": chain_agreement_count,
        "provisional_changed_count": chain_changed_count,
        "provisional_agreement_fraction": round(chain_agreement, 3),
    })
    dominant_chain_applied = bool(
        dominant_chain_audit.get("eligible")
        and len(dominant_chain_pairs) >= 10
        and float(dominant_chain_audit["design_run"].get("coverage", 0.0)) >= 0.85
        and float(dominant_chain_audit["ep3d_run"].get("coverage", 0.0)) >= 0.85
        and chain_agreement >= 0.72
        and 2 <= chain_changed_count <= 4
        and max(
            (float(real_cost[left, right]) for left, right in dominant_chain_pairs),
            default=100.0,
        ) <= 0.32
        and all(
            component_constraints.get(left, right) == right
            and all_continuation_constraints.get(left, right) == right
            and same_point_joint_constraints.get(left, right) == right
            for left, right in dominant_chain_pairs
        )
    )
    dominant_chain_audit["applied"] = dominant_chain_applied
    if dominant_chain_applied:
        for design_index, ep3d_index in dominant_chain_pairs:
            for other_ep3d in range(len(ep3d)):
                if other_ep3d != ep3d_index:
                    real_cost[design_index, other_ep3d] = 100.0
            for other_design in range(len(design)):
                if other_design != design_index:
                    real_cost[other_design, ep3d_index] = 100.0
            real_cost[design_index, ep3d_index] = min(
                float(real_cost[design_index, ep3d_index]), 0.12
            )
            pair_evidence[(design_index, ep3d_index)][
                "dominant_chain_sequence_constraint"
            ] = True
        assigned_pairs, best_total = _augmented_assignment(real_cost, gap_cost)
    short_mst_constraints: dict[int, int] = {}
    short_mst_audit: dict[str, Any] = {
        "method": "short-mst-gap-aware-path-sequence",
        "applied": False,
        "reason": "dominant-chain-already-applied" if dominant_chain_applied else None,
        "number_used_as_identity": False,
    }
    if not dominant_chain_applied:
        short_mst_constraints, short_mst_audit = _short_mst_gap_path_constraints(
            source,
            target,
            real_cost,
            pair_evidence,
            assigned_pairs,
            design_signatures,
            ep3d_signatures,
            gap_cost,
            protected_design_indices=(
                set(component_constraints)
                | set(equivalent_component_constraints)
                | set(same_point_joint_constraints)
                | set(deferred_continuation_secondary_design)
                | set(clustered_secondary_design_indices)
            ),
            protected_ep3d_indices=(
                set(component_constraints.values())
                | {
                    right
                    for rights in equivalent_component_constraints.values()
                    for right in rights
                }
                | set(same_point_joint_constraints.values())
                | set(deferred_continuation_secondary_ep3d)
                | set(one_sided_continuation_gap_targets)
            ),
        )
    if short_mst_constraints:
        for design_index, ep3d_index in short_mst_constraints.items():
            for other_ep3d in range(len(ep3d)):
                if other_ep3d != ep3d_index:
                    real_cost[design_index, other_ep3d] = 100.0
            for other_design in range(len(design)):
                if other_design != design_index:
                    real_cost[other_design, ep3d_index] = 100.0
            real_cost[design_index, ep3d_index] = min(
                float(real_cost[design_index, ep3d_index]), 0.12
            )
            pair_evidence[(design_index, ep3d_index)][
                "short_mst_gap_path_constraint"
            ] = True
        for design_index, ep3d_index in short_mst_audit.get(
            "continuation_relaxations", []
        ):
            design_signatures[design_index]["continuation_interface"] = True
            design_signatures[design_index][
                "inferred_continuation_counterpart"
            ] = True
            pair_evidence[(design_index, ep3d_index)]["semantic_gate"] = (
                _semantic_pair_evidence(
                    design_signatures[design_index],
                    ep3d_signatures[ep3d_index],
                    matrix,
                    design_graph["addresses"][design_index],
                    ep3d_graph["addresses"][ep3d_index],
                )
            )
            pair_evidence[(design_index, ep3d_index)][
                "mst_path_continuation_normalized"
            ] = True
        assigned_pairs, best_total = _augmented_assignment(real_cost, gap_cost)
    (
        late_same_point_constraints,
        late_same_point_audit,
    ) = _same_point_joint_path_constraints(
        design,
        ep3d,
        design_graph,
        assigned_pairs,
        ep3d_signatures,
        excluded_design_indices=clustered_secondary_design_indices,
        reserved_design_indices=set(component_constraints),
        reserved_ep3d_indices=(
            set(component_constraints.values())
            | {right for rights in equivalent_component_constraints.values() for right in rights}
            | set(deferred_continuation_secondary_ep3d)
            | set(one_sided_continuation_gap_targets)
        ),
    )
    late_same_point_constraints = {
        left: right
        for left, right in late_same_point_constraints.items()
        if left not in same_point_joint_constraints
        and right not in same_point_joint_constraints.values()
    }
    if late_same_point_constraints:
        same_point_joint_constraints.update(late_same_point_constraints)
        same_point_joint_audit["constraint_count"] = len(
            same_point_joint_constraints
        )
        same_point_joint_audit["events"].extend(
            late_same_point_audit.get("events", [])
        )
        same_point_joint_audit["late_assignment_refinement"] = True
        apply_same_point_joint_constraints()
        assigned_pairs, best_total = _augmented_assignment(real_cost, gap_cost)
    else:
        same_point_joint_audit["late_assignment_refinement"] = False
    topology = _nearest_topology_score(source, target, assigned_pairs)

    # A match is auto-accepted only when removing it makes the globally best
    # gap-aware assignment measurably worse.  This is the PDF/PDF equivalent
    # of the old forced-edge maximum-bipartite adjudication.
    uniqueness_margin: dict[tuple[int, int], float] = {}
    for left, right in assigned_pairs:
        forbidden = real_cost.copy()
        forbidden[left, right] = 100.0
        _, alternative_total = _augmented_assignment(forbidden, gap_cost)
        uniqueness_margin[(left, right)] = max(0.0, alternative_total - best_total)

    accepted: list[tuple[int, int]] = []
    for left, right in assigned_pairs:
        evidence = pair_evidence[(left, right)]
        semantic = evidence["semantic_gate"]
        evidence["final_assignment_cost"] = round(float(real_cost[left, right]), 6)
        evidence["forced_uniqueness_margin"] = round(uniqueness_margin[(left, right)], 6)
        if (
            determinant > 0.0
            and int(semantic["hard_violation_count"]) == 0
            and float(real_cost[left, right]) < gap_cost * 2.0
            and (
                float(evidence["normalized_residual"]) <= 0.24
                or all_continuation_constraints.get(left) == right
            )
            and (
                float(evidence["rank_order_score"]) >= 0.58
                or all_continuation_constraints.get(left) == right
            )
            and uniqueness_margin[(left, right)] >= 0.002
        ):
            if all_continuation_constraints.get(left) == right:
                evidence["topology_residual_override"] = (
                    "unique-referenced-continuation-terminal-role"
                )
            accepted.append((left, right))
    continuation_secondary_pairs: list[tuple[int, int]] = []
    for left, right in assigned_pairs:
        if (left, right) in accepted:
            continue
        evidence = pair_evidence[(left, right)]
        semantic = evidence["semantic_gate"]
        direction_score = semantic.get("port_direction_score")
        unique_interface_role = bool(
            len(secondary_coincident_ep3d_indices) == 1
            and sum(
                bool(signature.get("continuation_interface"))
                for signature in design_signatures
            ) == 1
            and semantic.get("status") == "matched"
            and uniqueness_margin[(left, right)] >= 0.08
        )
        if (
            bool(design_signatures[left].get("continuation_interface"))
            and right in secondary_coincident_ep3d_indices
            and int(semantic["hard_violation_count"]) == 0
            and (
                direction_score is not None and float(direction_score) >= 0.94
                or unique_interface_role
            )
            and float(evidence["rank_order_score"]) >= 0.45
            and uniqueness_margin[(left, right)] >= 0.002
        ):
            # The secondary compound identity and the explicit physical
            # identity intentionally share a target dot.  ISO schematic
            # placement can put the corresponding page-open endpoint far
            # away, so topology/port direction outranks residual here.
            evidence["topology_residual_override"] = (
                "continuation-interface-to-secondary-coincident-label"
            )
            evidence["forced_uniqueness_margin"] = round(
                uniqueness_margin[(left, right)], 6
            )
            evidence["final_assignment_cost"] = round(float(real_cost[left, right]), 6)
            accepted.append((left, right))
            continuation_secondary_pairs.append((left, right))
    recovered_pairs, isolated_endpoint_recovery = _isolated_endpoint_recovery_pairs(
        assigned_pairs,
        accepted,
        pair_evidence,
        real_cost,
        uniqueness_margin,
        design_signatures,
        ep3d_signatures,
        landmark_audit,
        determinant,
        gap_cost,
    )
    for pair in recovered_pairs:
        pair_evidence[pair]["topology_residual_override"] = (
            "mutually-unique-isolated-flange-endpoint"
        )
        pair_evidence[pair]["forced_uniqueness_margin"] = round(uniqueness_margin[pair], 6)
        pair_evidence[pair]["final_assignment_cost"] = round(float(real_cost[pair]), 6)
        accepted.append(pair)
    topology_residual_pairs, topology_residual_recovery = (
        _topology_over_length_residual_recovery_pairs(
            assigned_pairs, accepted, pair_evidence, real_cost, uniqueness_margin
        )
    )
    for pair in topology_residual_pairs:
        pair_evidence[pair]["topology_residual_override"] = (
            "topology-and-port-evidence-over-length-residual"
        )
        pair_evidence[pair]["forced_uniqueness_margin"] = round(
            uniqueness_margin[pair], 6
        )
        pair_evidence[pair]["final_assignment_cost"] = round(float(real_cost[pair]), 6)
        accepted.append(pair)
    recovered_pair_set = (
        set(recovered_pairs)
        | set(topology_residual_pairs)
        | set(continuation_secondary_pairs)
        | set(all_continuation_constraints.items())
    )
    matches = []
    for left, right in accepted:
        residual = float(np.linalg.norm(local_projected[left] - target[right]))
        evidence = pair_evidence[(left, right)]
        normalized = float(evidence["normalized_residual"])
        topology_score = float(topology.get(left, 0.0))
        rank_order_score = float(evidence["rank_order_score"])
        semantic = evidence["semantic_gate"]
        margin = float(evidence["forced_uniqueness_margin"])
        direction_score = semantic["port_direction_score"]
        if (
            normalized <= 0.14 and rank_order_score >= 0.78 and margin >= 0.025
            and (direction_score is None or float(direction_score) >= 0.55)
        ):
            confidence = "high"
        elif (
            (normalized <= 0.24 or (left, right) in recovered_pair_set)
            and (
                rank_order_score >= 0.58
                or (left, right) in set(continuation_secondary_pairs)
                and rank_order_score >= 0.45
                or all_continuation_constraints.get(left) == right
            )
            and margin >= 0.002
        ):
            confidence = "medium"
        else:
            continue
        matches.append(
            {
                "design_index": left,
                "ep3d_index": right,
                "design_label": design[left].label,
                "ep3d_label": ep3d[right].label,
                "design_point": list(design[left].weld_point),
                "ep3d_point": list(ep3d[right].weld_point),
                "projected_design_point": local_projected[left].tolist(),
                "residual": round(residual, 3),
                "normalized_residual": round(normalized, 5),
                "global_normalized_residual": round(
                    float(evidence["global_normalized_residual"]), 5
                ),
                "local_topology_score": round(topology_score, 3),
                "rank_order_score": round(rank_order_score, 3),
                "design_topology_address": source_addresses[left],
                "ep3d_topology_address": target_addresses[right],
                "design_pdf_graph_address": design_graph["addresses"][left],
                "ep3d_pdf_graph_address": ep3d_graph["addresses"][right],
                "design_port_signature": design_signatures[left],
                "ep3d_port_signature": ep3d_signatures[right],
                "semantic_gate": semantic,
                "design_segment_zone": evidence.get("design_segment_zone"),
                "ep3d_segment_zone": evidence.get("ep3d_segment_zone"),
                "graph_neighbour_score": evidence.get("graph_neighbour_score"),
                "component_pair_constraint": bool(evidence.get("component_pair_constraint", False)),
                "same_point_joint_path_constraint": bool(
                    evidence.get("same_point_joint_path_constraint", False)
                ),
                "dominant_chain_sequence_constraint": bool(
                    evidence.get("dominant_chain_sequence_constraint", False)
                ),
                "short_mst_gap_path_constraint": bool(
                    evidence.get("short_mst_gap_path_constraint", False)
                ),
                "directional_mst_constraint": bool(evidence.get("directional_mst_constraint", False)),
                "topology_residual_override": evidence.get("topology_residual_override"),
                "shared_nearby_equipment_landmarks": evidence.get(
                    "shared_nearby_equipment_landmarks", []
                ),
                "forced_unique": True,
                "forced_uniqueness_margin": round(margin, 6),
                "assignment_cost": evidence["final_assignment_cost"],
                "design_skeleton_binding": design_signatures[left]["skeleton_binding"],
                "ep3d_skeleton_binding": ep3d_signatures[right]["skeleton_binding"],
                "confidence": confidence,
                "number_used_as_identity": False,
            }
        )
    matched_design = {item[0] for item in accepted}
    matched_ep3d = {item[1] for item in accepted}
    unmatched_gap_classification = {
        "design": {
            design[index].label: (
                "continuation-interface-gap"
                if bool(design_signatures[index].get("continuation_interface"))
                else "design-only"
            )
            for index in range(len(design))
            if index not in matched_design
        },
        "ep3d": {
            ep3d[index].label: (
                "continuation-interface-gap"
                if bool(ep3d_signatures[index].get("continuation_interface"))
                else "ep3d-only"
            )
            for index in range(len(ep3d))
            if index not in matched_ep3d
        },
    }
    high_medium = sum(item["confidence"] in {"high", "medium"} for item in matches)
    provisional_matches = []
    for left, right in assigned_pairs:
        evidence = pair_evidence[(left, right)]
        reasons = []
        if int(evidence["semantic_gate"]["hard_violation_count"]) > 0:
            reasons.extend(evidence["semantic_gate"]["hard_violation_reasons"])
        if float(real_cost[left, right]) >= gap_cost * 2.0:
            reasons.append("assignment-cost-exceeds-two-gaps")
        if float(evidence["normalized_residual"]) > 0.24 and (left, right) not in recovered_pair_set:
            reasons.append("normalized-residual-too-large")
        if float(evidence["rank_order_score"]) < 0.58:
            reasons.append("page-rank-order-too-weak")
        if uniqueness_margin[(left, right)] < 0.002:
            reasons.append("not-forced-in-global-gap-aware-optimum")
        provisional_matches.append(
            {
                "design_label": design[left].label,
                "ep3d_label": ep3d[right].label,
                "accepted": (left, right) in accepted,
                "assignment_cost": round(float(real_cost[left, right]), 6),
                "normalized_residual": round(float(evidence["normalized_residual"]), 5),
                "rank_order_score": round(float(evidence["rank_order_score"]), 3),
                "forced_uniqueness_margin": round(uniqueness_margin[(left, right)], 6),
                "semantic_gate": evidence["semantic_gate"],
                "topology_residual_override": evidence.get("topology_residual_override"),
                "rejection_reasons": reasons,
            }
        )
    # Persist the competing geometric / semantic hypotheses so a regression
    # can be explained from the production run itself.  Labels are reported
    # only after scoring and are never part of the cost function.
    candidate_diagnostics = {"design": [], "ep3d": []}
    for left, item in enumerate(design):
        ranked = sorted(range(len(ep3d)), key=lambda right: float(real_cost[left, right]))[:4]
        candidate_diagnostics["design"].append({
            "label": item.label,
            "candidates": [
                {
                    "label": ep3d[right].label,
                    "cost": round(float(real_cost[left, right]), 6),
                    "normalized_residual": round(float(pair_evidence[(left, right)]["normalized_residual"]), 5),
                    "rank_order_score": round(float(pair_evidence[(left, right)]["rank_order_score"]), 3),
                    "semantic_status": pair_evidence[(left, right)]["semantic_gate"]["status"],
                    "hard_violation_count": int(pair_evidence[(left, right)]["semantic_gate"]["hard_violation_count"]),
                }
                for right in ranked
            ],
        })
    for right, item in enumerate(ep3d):
        ranked = sorted(range(len(design)), key=lambda left: float(real_cost[left, right]))[:4]
        candidate_diagnostics["ep3d"].append({
            "label": item.label,
            "candidates": [
                {
                    "label": design[left].label,
                    "cost": round(float(real_cost[left, right]), 6),
                    "normalized_residual": round(float(pair_evidence[(left, right)]["normalized_residual"]), 5),
                    "rank_order_score": round(float(pair_evidence[(left, right)]["rank_order_score"]), 3),
                    "semantic_status": pair_evidence[(left, right)]["semantic_gate"]["status"],
                    "hard_violation_count": int(pair_evidence[(left, right)]["semantic_gate"]["hard_violation_count"]),
                }
                for left in ranked
            ],
        })
    return {
        "status": "candidate-mapping" if matches else "unresolved",
        "affine_matrix": matrix.tolist(),
        "orientation_lock": {
            "north_direction_same": True,
            "axis_swap_allowed": False,
            "mirror_allowed": False,
            "affine_determinant": round(determinant, 6),
        },
        "sequence_alignment": {
            "method": "piecewise-structural-zones-gap-aware-bipartite-topology",
            "forced_full_cardinality": False,
            "gap_cost": gap_cost,
            "structural_landmark_pair_count": len(landmark_pairs),
            "forced_unique_acceptance_required": True,
        },
        "transform_source": transform_source,
        "transform_hypotheses": transform_hypotheses,
        "piecewise_projection": piecewise_projection,
        "component_port_recovery": {
            "design": design_port_recovery,
            "ep3d": ep3d_port_recovery,
        },
        "component_pair_alignment": component_pair_alignment,
        "validated_component_pair_alignment": validated_component_alignment,
        "clustered_secondary_design_annotations": clustered_secondary_design_audit,
        "secondary_coincident_ep3d_labels": secondary_coincident_ep3d_audit,
        "same_point_joint_path_alignment": same_point_joint_audit,
        "unique_continuation_terminal_alignment": continuation_terminal_constraint_audit,
        "mutual_continuation_boundary_alignment": continuation_boundary_constraint_audit,
        "one_sided_continuation_gap_recovery": one_sided_continuation_gap_audit,
        "continuation_secondary_label_recovery": {
            "method": "continuation-interface-to-secondary-coincident-label",
            "recovered_pair_count": len(continuation_secondary_pairs),
            "recovered_pairs": [
                {
                    "design_index": left,
                    "ep3d_index": right,
                    "design_label": design[left].label,
                    "ep3d_label": ep3d[right].label,
                }
                for left, right in continuation_secondary_pairs
            ],
            "number_used_as_identity": False,
        },
        "directional_mst_alignment": directional_mst_audit,
        "dominant_chain_sequence_alignment": dominant_chain_audit,
        "short_mst_gap_path_alignment": short_mst_audit,
        "isolated_endpoint_recovery": isolated_endpoint_recovery,
        "topology_over_length_residual_recovery": topology_residual_recovery,
        "stable_landmark_audit": landmark_audit,
        "design_process_skeleton": {key: value for key, value in (design_skeleton or {}).items() if key != "segments"},
        "ep3d_process_skeleton": {key: value for key, value in (ep3d_skeleton or {}).items() if key != "segments"},
        "design_port_signatures": design_signatures,
        "ep3d_port_signatures": ep3d_signatures,
        "ep3d_weld_list_semantics": ep3d_weld_list_semantics,
        "design_callout_graph": design_graph,
        "ep3d_callout_graph": ep3d_graph,
        "continuation_normalization": {
            "design_continuation_terminals": [
                item["label"] for item in design_signatures if item["structure_class"] == "continuation-terminal"
            ],
            "ep3d_continuation_terminals": [
                item["label"] for item in ep3d_signatures if item["structure_class"] == "continuation-terminal"
            ],
            "ownership_difference_is_gap_not_sequence_shift": True,
        },
        "target_diagonal": diagonal,
        "provisional_matches": provisional_matches,
        "candidate_diagnostics": candidate_diagnostics,
        "unmatched_gap_classification": unmatched_gap_classification,
        "matches": sorted(matches, key=lambda item: _label_text_sort_key(item["design_label"])),
        "unmatched_design": [item.label for index, item in enumerate(design) if index not in matched_design],
        "unmatched_ep3d": [item.label for index, item in enumerate(ep3d) if index not in matched_ep3d],
        "summary": {
            "design_callout_count": len(design),
            "ep3d_callout_count": len(ep3d),
            "accepted_match_count": len(matches),
            "high_or_medium_count": high_medium,
            "semantic_conflict_rejection_count": sum(
                int(pair_evidence[pair]["semantic_gate"]["hard_violation_count"]) > 0
                for pair in pair_evidence
            ),
            "forced_unique_match_count": len(matches),
            "isolated_endpoint_recovery_count": len(recovered_pairs),
            "topology_over_length_residual_recovery_count": len(topology_residual_pairs),
        },
    }


def _drawing_sheet_sequence(value: str | None) -> int | None:
    match = re.search(r"-(\d+)$", str(value or "").upper())
    return int(match.group(1)) if match else None


def _ep3d_file_sheet_sequence(value: str | None) -> int | None:
    match = re.search(r"_SHEET_(\d+)\.PDF$", str(value or "").upper())
    return int(match.group(1)) if match else None


def _ep3d_reference_sheet_sequence(value: str | None) -> int | None:
    match = re.search(r"-(\d+)/(\d+)$", str(value or "").upper())
    return int(match.group(1)) if match else None


def _pair_signature_map(pair: dict[str, Any], side: str) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("label", "")).upper(): item
        for item in pair.get("mapping", {}).get(f"{side}_port_signatures", [])
    }


def _pair_callout_map(pair: dict[str, Any], side: str) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("label", "")).upper(): item
        for item in pair.get(f"{side}_callouts", [])
    }


def _pair_mst_paths(pair: dict[str, Any], side: str) -> list[list[str]]:
    callouts = pair.get(f"{side}_callouts", [])
    if len(callouts) < 2:
        return []
    points = np.asarray([item["weld_point"] for item in callouts], dtype=float)
    labels = [str(item["label"]).upper() for item in callouts]
    return [[labels[index] for index in path] for path in _maximal_mst_paths(points)]


def _compact_weld_list_joint_groups(
    pair: dict[str, Any],
    *,
    maximum_span: float = 26.0,
) -> list[dict[str, Any]]:
    """Return compact three-or-more weld groups sharing one WELD LIST node."""

    signatures = _pair_signature_map(pair, "ep3d")
    by_node: dict[str, list[str]] = {}
    for label, signature in signatures.items():
        for node in _joint_nodes(signature):
            by_node.setdefault(node, []).append(label)
    groups = []
    for node, labels in by_node.items():
        unique = sorted(set(labels))
        if len(unique) < 3:
            continue
        points = [signatures[label].get("paper_coordinate", [0.0, 0.0]) for label in unique]
        span = max(
            (math.dist(tuple(left), tuple(right)) for left in points for right in points),
            default=0.0,
        )
        if span <= maximum_span:
            groups.append({"joint_node": node, "labels": unique, "span": span})
    return groups


def _candidate_diagnostic_cost(
    pair: dict[str, Any], design_label: str, ep3d_label: str,
) -> float:
    diagnostics = pair.get("mapping", {}).get("candidate_diagnostics", {})
    design_rows = diagnostics.get("design", []) if isinstance(diagnostics, dict) else []
    for row in design_rows:
        if str(row.get("label", "")).upper() != design_label.upper():
            continue
        for candidate in row.get("candidates", []):
            if str(candidate.get("label", "")).upper() == ep3d_label.upper():
                return float(candidate.get("cost", 100.0))
    return 100.0


def _make_line_topology_match(
    design_pair: dict[str, Any],
    design_label: str,
    ep3d_pair: dict[str, Any],
    ep3d_label: str,
    *,
    method: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    design_label, ep3d_label = design_label.upper(), ep3d_label.upper()
    design_callout = _pair_callout_map(design_pair, "design")[design_label]
    ep3d_callout = _pair_callout_map(ep3d_pair, "ep3d")[ep3d_label]
    design_signature = _pair_signature_map(design_pair, "design")[design_label]
    ep3d_signature = _pair_signature_map(ep3d_pair, "ep3d")[ep3d_label]
    semantic = _semantic_pair_evidence(
        design_signature, ep3d_signature, np.eye(3), {}, {}
    )
    # Port directions reverse at a page interface.  WELD LIST continuity and
    # the two page-path endpoints are the hard identity evidence here; the
    # page-local direction score remains in the audit but is not a veto.
    semantic["cross_page_interface_direction_reversal_expected"] = (
        design_pair is not ep3d_pair
    )
    if (
        method in {
            "whole-line-boundary-prefix-shift",
            "whole-line-explicit-singleton-sheet-port",
            "whole-line-explicit-reciprocal-page-port",
        }
        and semantic.get("hard_violation_reasons")
        == ["continuation-terminal-vs-physical-weld"]
    ):
        semantic["hard_violation_count"] = 0
        semantic["hard_violation_reasons"] = []
        semantic["status"] = "compatible"
        semantic["whole_line_interface_role_normalized"] = True
    return {
        "design_index": next(
            index for index, item in enumerate(design_pair["design_callouts"])
            if str(item["label"]).upper() == design_label
        ),
        "ep3d_index": next(
            index for index, item in enumerate(ep3d_pair["ep3d_callouts"])
            if str(item["label"]).upper() == ep3d_label
        ),
        "design_label": design_label,
        "ep3d_label": ep3d_label,
        "design_point": list(design_callout["weld_point"]),
        "ep3d_point": list(ep3d_callout["weld_point"]),
        "projected_design_point": list(design_callout["weld_point"]),
        "residual": 0.0,
        "normalized_residual": 0.0,
        "global_normalized_residual": 0.0,
        "local_topology_score": 1.0,
        "rank_order_score": 1.0,
        "design_port_signature": design_signature,
        "ep3d_port_signature": ep3d_signature,
        "semantic_gate": semantic,
        "graph_neighbour_score": 1.0,
        "component_pair_constraint": False,
        "same_point_joint_path_constraint": False,
        "dominant_chain_sequence_constraint": False,
        "short_mst_gap_path_constraint": False,
        "directional_mst_constraint": False,
        "line_topology_constraint": True,
        "line_topology_method": method,
        "line_topology_evidence": evidence,
        "topology_residual_override": "whole-line-open-port-topology",
        "shared_nearby_equipment_landmarks": [],
        "forced_unique": True,
        "forced_uniqueness_margin": 0.9,
        "assignment_cost": 0.05,
        "confidence": "high",
        "number_used_as_identity": False,
        "ep3d_source_file": ep3d_pair.get("ep3d_file"),
        "ep3d_source_sheet": ep3d_pair.get("ep3d_sheet"),
        "ep3d_source_isometric_drawing_no": ep3d_pair.get("isometric_drawing_no"),
    }


def _replace_pair_matches(
    pair: dict[str, Any],
    removals: set[tuple[str, str]],
    additions: list[dict[str, Any]],
) -> None:
    mapping = pair["mapping"]
    retained = [
        match for match in mapping.get("matches", [])
        if (
            str(match.get("design_label", "")).upper(),
            str(match.get("ep3d_label", "")).upper(),
        ) not in removals
    ]
    removed_design = {left for left, _ in removals}
    removed_ep3d = {right for _, right in removals}
    addition_design = {
        str(item["design_label"]).upper() for item in additions
        if not bool(item.get("allow_shared_design_port"))
    }
    addition_ep3d = {
        str(item["ep3d_label"]).upper()
        for item in additions
        if not item.get("ep3d_source_file")
        or item.get("ep3d_source_file") == pair.get("ep3d_file")
    }
    retained = [
        match for match in retained
        if str(match.get("design_label", "")).upper() not in addition_design
        and not (
            str(match.get("ep3d_label", "")).upper() in addition_ep3d
            and (
                not match.get("ep3d_source_file")
                or match.get("ep3d_source_file") == pair.get("ep3d_file")
            )
        )
    ]
    mapping["matches"] = sorted(
        retained + additions,
        key=lambda item: _label_text_sort_key(str(item["design_label"])),
    )


def _refresh_line_pair_unmatched(pair: dict[str, Any]) -> None:
    mapping = pair["mapping"]
    matched_design = {
        str(item.get("design_label", "")).upper()
        for item in mapping.get("matches", [])
    }
    matched_local_ep3d = {
        str(item.get("ep3d_label", "")).upper()
        for item in mapping.get("matches", [])
        if not item.get("ep3d_source_file")
        or item.get("ep3d_source_file") == pair.get("ep3d_file")
    }
    owned_out = {
        str(value).upper()
        for value in mapping.get("cross_page_owned_ep3d", [])
    }
    mapping["unmatched_design"] = [
        item["label"] for item in pair.get("design_callouts", [])
        if str(item["label"]).upper() not in matched_design
    ]
    mapping["unmatched_ep3d"] = [
        item["label"] for item in pair.get("ep3d_callouts", [])
        if str(item["label"]).upper() not in matched_local_ep3d | owned_out
    ]
    mapping.setdefault("summary", {})["accepted_match_count"] = len(
        mapping.get("matches", [])
    )
    mapping["summary"]["high_or_medium_count"] = sum(
        item.get("confidence") in {"high", "medium"}
        for item in mapping.get("matches", [])
    )


def _pair_graph_adjacency_by_label(
    pair: dict[str, Any], side: str,
) -> dict[str, set[str]]:
    callouts = pair.get(f"{side}_callouts", [])
    labels = [str(item.get("label", "")).upper() for item in callouts]
    adjacency = {label: set() for label in labels}
    graph = pair.get("mapping", {}).get(f"{side}_callout_graph", {})
    for edge in graph.get("edges", []):
        left, right = (int(value) for value in edge.get("nodes", []))
        if not (0 <= left < len(labels) and 0 <= right < len(labels)):
            continue
        adjacency[labels[left]].add(labels[right])
        adjacency[labels[right]].add(labels[left])
    return adjacency


def _reconcile_local_graph_chain_endpoints(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extend two aligned PDF-graph anchors to their unique next ports.

    A geometric assignment can select an isolated nearby flange face after a
    correctly aligned elbow/pipe run.  Two consecutive, mutually adjacent
    matches establish the component path.  When both graphs have exactly one
    unused continuation and the page-local diagnostic cost supports it, the
    path continuation outranks the isolated Euclidean alternative.
    """

    events: list[dict[str, Any]] = []
    for pair in results:
        mapping = pair.get("mapping", {})
        design_adjacency = _pair_graph_adjacency_by_label(pair, "design")
        ep3d_adjacency = _pair_graph_adjacency_by_label(pair, "ep3d")
        if not design_adjacency or not ep3d_adjacency:
            continue
        matches = list(mapping.get("matches", []))
        design_owner = {
            str(item.get("design_label", "")).upper(): item for item in matches
        }
        ep3d_owner = {
            str(item.get("ep3d_label", "")).upper(): item for item in matches
            if not item.get("ep3d_source_file")
            or item.get("ep3d_source_file") == pair.get("ep3d_file")
        }
        proposals: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for previous in matches:
            previous_design = str(previous.get("design_label", "")).upper()
            previous_ep3d = str(previous.get("ep3d_label", "")).upper()
            if previous.get("ep3d_source_file") not in {None, "", pair.get("ep3d_file")}:
                continue
            for anchor in matches:
                anchor_design = str(anchor.get("design_label", "")).upper()
                anchor_ep3d = str(anchor.get("ep3d_label", "")).upper()
                if anchor is previous or anchor.get("ep3d_source_file") not in {
                    None, "", pair.get("ep3d_file")
                }:
                    continue
                if (
                    anchor_design not in design_adjacency.get(previous_design, set())
                    or anchor_ep3d not in ep3d_adjacency.get(previous_ep3d, set())
                ):
                    continue
                next_design = design_adjacency.get(anchor_design, set()) - {
                    previous_design
                }
                next_ep3d = ep3d_adjacency.get(anchor_ep3d, set()) - {
                    previous_ep3d
                }
                if len(next_design) != 1 or len(next_ep3d) != 1:
                    continue
                design_label = next(iter(next_design))
                ep3d_label = next(iter(next_ep3d))
                if design_owner.get(design_label, {}).get("ep3d_label") == ep3d_label:
                    continue
                if design_label in design_owner:
                    continue
                current = ep3d_owner.get(ep3d_label)
                current_design = (
                    str(current.get("design_label", "")).upper() if current else ""
                )
                if current_design in design_adjacency.get(anchor_design, set()):
                    continue
                candidate_cost = _candidate_diagnostic_cost(
                    pair, design_label, ep3d_label
                )
                if candidate_cost > 0.18:
                    continue
                current_cost = (
                    float(current.get("assignment_cost", 100.0)) if current else 100.0
                )
                if current and candidate_cost > current_cost + 0.03:
                    continue
                proposals.setdefault((design_label, ep3d_label), []).append({
                    "previous_design_label": previous_design,
                    "previous_ep3d_label": previous_ep3d,
                    "anchor_design_label": anchor_design,
                    "anchor_ep3d_label": anchor_ep3d,
                    "replaced_design_label": current_design or None,
                    "candidate_cost": round(candidate_cost, 6),
                    "replaced_cost": None if not current else round(current_cost, 6),
                })
        # Multiple traversal directions may prove the same extension.  A
        # competing endpoint proposal is ambiguous and must remain a gap.
        proposed_design = [key[0] for key in proposals]
        proposed_ep3d = [key[1] for key in proposals]
        for (design_label, ep3d_label), evidence_rows in proposals.items():
            if proposed_design.count(design_label) > 1 or proposed_ep3d.count(ep3d_label) > 1:
                continue
            current = ep3d_owner.get(ep3d_label)
            removals = set()
            if current is not None:
                removals.add((
                    str(current.get("design_label", "")).upper(), ep3d_label
                ))
            addition = _make_line_topology_match(
                pair, design_label, pair, ep3d_label,
                method="local-pdf-graph-two-anchor-endpoint-extension",
                evidence={"anchor_paths": evidence_rows},
            )
            if int(addition["semantic_gate"].get("hard_violation_count", 0)):
                continue
            _replace_pair_matches(pair, removals, [addition])
            event = {
                "event_type": "local-graph-chain-endpoint-extension",
                "line_id": pair.get("line_id"),
                "design_page": pair.get("design_page"),
                "design_label": design_label,
                "ep3d_label": ep3d_label,
                "replaced_design_label": (
                    str(current.get("design_label", "")).upper()
                    if current is not None else None
                ),
                "anchor_paths": evidence_rows,
                "number_used_as_identity": False,
            }
            events.append(event)
            mapping.setdefault(
                "local_graph_chain_endpoint_reconciliation", {"events": []}
            )["events"].append(event)
    return events


def _segment_projection_metrics(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float, float]:
    """Return unclamped fraction, perpendicular distance and segment length."""

    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return 0.0, math.dist(point, start), length
    fraction = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / (length * length)
    projected = (start[0] + fraction * dx, start[1] + fraction * dy)
    return fraction, math.dist(point, projected), length


def _reconcile_graph_sandwiched_internal_ports(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover one missing pipe weld between two established graph anchors.

    EP3D often preserves the long straight-run graph even when the design PDF
    process skeleton is split by a callout or a component gap.  A free EP port
    is accepted only when it is a degree-two ``through`` node joined by two
    process segments, both neighbours already have local matches, and exactly
    one compatible free design ``through`` port lies tightly inside the two
    matched design anchors.  Absolute run lengths are deliberately ignored.
    """

    events: list[dict[str, Any]] = []
    for pair in results:
        mapping = pair.get("mapping", {})
        design_free = {
            str(value).upper() for value in mapping.get("unmatched_design", [])
        }
        ep3d_free = {
            str(value).upper() for value in mapping.get("unmatched_ep3d", [])
        }
        if not design_free or not ep3d_free:
            continue
        ep3d_adjacency = _pair_graph_adjacency_by_label(pair, "ep3d")
        ep3d_signatures = _pair_signature_map(pair, "ep3d")
        design_signatures = _pair_signature_map(pair, "design")
        design_callouts = _pair_callout_map(pair, "design")
        ep3d_callouts = _pair_callout_map(pair, "ep3d")
        local_matches = [
            item for item in mapping.get("matches", [])
            if item.get("ep3d_source_file") in {None, "", pair.get("ep3d_file")}
        ]
        design_by_ep3d = {
            str(item.get("ep3d_label", "")).upper():
            str(item.get("design_label", "")).upper()
            for item in local_matches
        }
        process_edges = {
            frozenset((
                str(pair.get("ep3d_callouts", [])[int(edge["nodes"][0])]["label"]).upper(),
                str(pair.get("ep3d_callouts", [])[int(edge["nodes"][1])]["label"]).upper(),
            ))
            for edge in mapping.get("ep3d_callout_graph", {}).get("edges", [])
            if len(edge.get("nodes", [])) == 2
            and "shared-process-segment" in edge.get("evidence", [])
            and all(
                0 <= int(index) < len(pair.get("ep3d_callouts", []))
                for index in edge["nodes"]
            )
        }
        proposals: dict[tuple[str, str], dict[str, Any]] = {}
        for ep3d_label in sorted(ep3d_free):
            ep3d_signature = ep3d_signatures.get(ep3d_label, {})
            if ep3d_signature.get("structure_class") != "through":
                continue
            neighbours = sorted(ep3d_adjacency.get(ep3d_label, set()))
            if len(neighbours) != 2 or any(
                frozenset((ep3d_label, neighbour)) not in process_edges
                for neighbour in neighbours
            ):
                continue
            if any(neighbour not in design_by_ep3d for neighbour in neighbours):
                continue
            anchor_design = [design_by_ep3d[neighbour] for neighbour in neighbours]
            if anchor_design[0] == anchor_design[1]:
                continue
            design_start = tuple(design_callouts[anchor_design[0]]["weld_point"])
            design_end = tuple(design_callouts[anchor_design[1]]["weld_point"])
            ep3d_start = tuple(ep3d_callouts[neighbours[0]]["weld_point"])
            ep3d_end = tuple(ep3d_callouts[neighbours[1]]["weld_point"])
            ep_fraction, ep_distance, ep_length = _segment_projection_metrics(
                tuple(ep3d_callouts[ep3d_label]["weld_point"]), ep3d_start, ep3d_end
            )
            if not (0.05 <= ep_fraction <= 0.95):
                continue
            if ep_distance > max(6.0, 0.05 * ep_length):
                continue
            candidates = []
            for design_label in sorted(design_free):
                design_signature = design_signatures.get(design_label, {})
                if design_signature.get("structure_class") != "through":
                    continue
                fraction, distance, anchor_length = _segment_projection_metrics(
                    tuple(design_callouts[design_label]["weld_point"]),
                    design_start, design_end,
                )
                if anchor_length < 30.0 or not (0.05 <= fraction <= 0.95):
                    continue
                if distance > max(6.0, 0.02 * anchor_length):
                    continue
                addition = _make_line_topology_match(
                    pair, design_label, pair, ep3d_label,
                    method="local-graph-sandwiched-internal-port",
                    evidence={
                        "ep3d_graph_neighbours": neighbours,
                        "matched_design_anchors": anchor_design,
                        "design_segment_fraction": round(fraction, 6),
                        "design_perpendicular_distance": round(distance, 3),
                        "design_anchor_span": round(anchor_length, 3),
                        "ep3d_segment_fraction": round(ep_fraction, 6),
                        "ep3d_perpendicular_distance": round(ep_distance, 3),
                    },
                )
                semantic = addition.get("semantic_gate", {})
                if int(semantic.get("hard_violation_count", 0)):
                    continue
                if float(semantic.get("port_direction_score", 0.0)) < 0.90:
                    continue
                candidates.append((design_label, addition))
            if len(candidates) != 1:
                continue
            design_label, addition = candidates[0]
            proposals[(design_label, ep3d_label)] = {
                "addition": addition,
                "evidence": addition["line_topology_evidence"],
            }
        design_counts = Counter(key[0] for key in proposals)
        ep3d_counts = Counter(key[1] for key in proposals)
        for (design_label, ep3d_label), proposal in proposals.items():
            if design_counts[design_label] != 1 or ep3d_counts[ep3d_label] != 1:
                continue
            _replace_pair_matches(pair, set(), [proposal["addition"]])
            event = {
                "event_type": "graph-sandwiched-internal-port",
                "line_id": pair.get("line_id"),
                "design_page": pair.get("design_page"),
                "design_label": design_label,
                "ep3d_label": ep3d_label,
                **proposal["evidence"],
                "number_used_as_identity": False,
            }
            events.append(event)
            mapping.setdefault(
                "graph_sandwiched_internal_port_reconciliation", {"events": []}
            )["events"].append(event)
    return events


def _simple_graph_path_orders(
    adjacency: dict[str, set[str]], labels: set[str],
) -> list[list[str]]:
    if len(labels) < 2:
        return []
    if any(len(adjacency.get(label, set()) & labels) > 2 for label in labels):
        return []
    endpoints = sorted(
        label for label in labels
        if len(adjacency.get(label, set()) & labels) == 1
    )
    if len(endpoints) != 2:
        return []
    order = [endpoints[0]]
    previous = None
    current = endpoints[0]
    while len(order) < len(labels):
        options = sorted(
            (adjacency.get(current, set()) & labels) - ({previous} if previous else set())
        )
        if len(options) != 1 or options[0] in order:
            return []
        previous, current = current, options[0]
        order.append(current)
    return [order, list(reversed(order))]


def _directed_path_angle_error(
    left_order: list[str], right_order: list[str],
    left_points: dict[str, tuple[float, float]],
    right_points: dict[str, tuple[float, float]],
) -> float:
    errors = []
    for index in range(len(left_order) - 1):
        left_start, left_end = left_points[left_order[index]], left_points[left_order[index + 1]]
        right_start, right_end = right_points[right_order[index]], right_points[right_order[index + 1]]
        left_angle = math.degrees(math.atan2(
            left_end[1] - left_start[1], left_end[0] - left_start[0]
        )) % 360.0
        right_angle = math.degrees(math.atan2(
            right_end[1] - right_start[1], right_end[0] - right_start[0]
        )) % 360.0
        errors.append(_directed_angle_error(left_angle, right_angle))
    return float(np.mean(errors)) if errors else 180.0


def _reconcile_unmatched_referenced_boundary_paths(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Align a zero-match path after removing one referenced EP boundary.

    The ISO is non-metric, so absolute pipe lengths are ignored.  This rule
    requires a simple PDF graph on both sides, one EP endpoint carrying an
    adjacent ``n/m`` sheet reference, equal internal path cardinality and a
    unique same-axis direction.  The referenced endpoint remains open for the
    later whole-line ownership pass.
    """

    events: list[dict[str, Any]] = []
    for pair in results:
        mapping = pair.get("mapping", {})
        if mapping.get("matches"):
            continue
        design_adjacency = _pair_graph_adjacency_by_label(pair, "design")
        ep3d_adjacency = _pair_graph_adjacency_by_label(pair, "ep3d")
        design_free = {
            str(value).upper() for value in mapping.get("unmatched_design", [])
        }
        ep3d_free = {
            str(value).upper() for value in mapping.get("unmatched_ep3d", [])
        }
        if len(design_free) < 3 or len(ep3d_free) < 4:
            continue
        ep3d_signatures = _pair_signature_map(pair, "ep3d")
        source_sheet = _ep3d_file_sheet_sequence(pair.get("ep3d_file"))
        referenced_endpoints = []
        for label in ep3d_free:
            if len(ep3d_adjacency.get(label, set()) & ep3d_free) != 1:
                continue
            signature = ep3d_signatures.get(label, {})
            references = [
                str(signature.get("continuation_reference") or "").upper(),
                str(signature.get("nearest_continuation_reference_candidate") or "").upper(),
            ]
            target_sheets = {
                sheet for value in references
                if (sheet := _ep3d_reference_sheet_sequence(value)) is not None
            }
            if (
                source_sheet is not None
                and any(abs(sheet - source_sheet) == 1 for sheet in target_sheets)
            ):
                referenced_endpoints.append((label, sorted(target_sheets)))
        if len(referenced_endpoints) != 1:
            continue
        boundary_label, target_sheets = referenced_endpoints[0]
        internal_ep3d = ep3d_free - {boundary_label}
        ep3d_orders = _simple_graph_path_orders(ep3d_adjacency, internal_ep3d)
        if not ep3d_orders:
            continue
        # Connected design components are independent; only a component with
        # the exact internal EP path cardinality is eligible.
        components: list[set[str]] = []
        unseen = set(design_free)
        while unseen:
            start = next(iter(unseen))
            component = {start}
            frontier = [start]
            while frontier:
                node = frontier.pop()
                for neighbour in design_adjacency.get(node, set()) & design_free:
                    if neighbour not in component:
                        component.add(neighbour)
                        frontier.append(neighbour)
            unseen -= component
            components.append(component)
        candidates = []
        design_points = {
            label: tuple(item["weld_point"])
            for label, item in _pair_callout_map(pair, "design").items()
        }
        ep3d_points = {
            label: tuple(item["weld_point"])
            for label, item in _pair_callout_map(pair, "ep3d").items()
        }
        for component in components:
            if len(component) != len(internal_ep3d):
                continue
            design_orders = _simple_graph_path_orders(design_adjacency, component)
            for design_order in design_orders:
                for ep3d_order in ep3d_orders:
                    error = _directed_path_angle_error(
                        design_order, ep3d_order, design_points, ep3d_points
                    )
                    candidates.append((error, design_order, ep3d_order))
        # Forward/reverse enumeration duplicates each physical mapping.
        unique: dict[tuple[tuple[str, str], ...], tuple[float, list[str], list[str]]] = {}
        for error, design_order, ep3d_order in candidates:
            key = tuple(sorted(zip(design_order, ep3d_order)))
            current = unique.get(key)
            if current is None or error < current[0]:
                unique[key] = (error, design_order, ep3d_order)
        ranked = sorted(unique.values(), key=lambda item: item[0])
        if not ranked or ranked[0][0] > 35.0:
            continue
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 40.0:
            continue
        angle_error, design_order, ep3d_order = ranked[0]
        additions = []
        valid = True
        for design_label, ep3d_label in zip(design_order, ep3d_order):
            addition = _make_line_topology_match(
                pair, design_label, pair, ep3d_label,
                method="local-unmatched-path-after-referenced-boundary-gap",
                evidence={
                    "referenced_boundary_ep3d_label": boundary_label,
                    "referenced_target_sheets": target_sheets,
                    "directed_path_angle_error": round(angle_error, 3),
                },
            )
            if int(addition["semantic_gate"].get("hard_violation_count", 0)):
                valid = False
                break
            additions.append(addition)
        if not valid:
            continue
        _replace_pair_matches(pair, set(), additions)
        event = {
            "event_type": "unmatched-path-after-referenced-boundary-gap",
            "line_id": pair.get("line_id"),
            "design_page": pair.get("design_page"),
            "referenced_boundary_ep3d_label": boundary_label,
            "matches": [
                {"design_label": left, "ep3d_label": right}
                for left, right in zip(design_order, ep3d_order)
            ],
            "directed_path_angle_error": round(angle_error, 3),
            "number_used_as_identity": False,
        }
        events.append(event)
        mapping.setdefault(
            "unmatched_referenced_boundary_path_reconciliation", {"events": []}
        )["events"].append(event)
    return events


def _reconcile_explicit_singleton_sheet_ports(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach a singleton EP3D continuation weld to its referenced sheet.

    Some construction sheets contain one weld and a direct ``n/m`` SEE ISO
    reference.  Such a page has no point cloud from which to estimate an
    affine transform.  The explicit EP sheet reference resolves the target
    page, while the target design endpoint must independently point back to
    the source drawing and be the unique free one-ray port.  Neither weld
    number nor same numeric PDF page participates in the decision.
    """

    events: list[dict[str, Any]] = []
    by_line_sheet = {
        (str(pair.get("line_id", "")), _ep3d_file_sheet_sequence(pair.get("ep3d_file"))): pair
        for pair in results
        if _ep3d_file_sheet_sequence(pair.get("ep3d_file")) is not None
    }
    for source in results:
        ep3d_callouts = source.get("ep3d_callouts", [])
        if len(ep3d_callouts) != 1:
            continue
        ep3d_label = str(ep3d_callouts[0].get("label", "")).upper()
        if ep3d_label not in {
            str(value).upper() for value in source.get("mapping", {}).get(
                "unmatched_ep3d", []
            )
        }:
            continue
        source_signature = _pair_signature_map(source, "ep3d").get(ep3d_label)
        if not source_signature:
            continue
        reference = str(source_signature.get("continuation_reference") or "").upper()
        target_sheet = _ep3d_reference_sheet_sequence(reference)
        if target_sheet is None:
            continue
        target = by_line_sheet.get((str(source.get("line_id", "")), target_sheet))
        if target is None or target is source:
            continue
        source_drawing = str(source.get("isometric_drawing_no") or "").upper()
        target_signatures = _pair_signature_map(target, "design")
        free_design = {
            str(value).upper()
            for value in target.get("mapping", {}).get("unmatched_design", [])
        }
        ranked = []
        for design_label in free_design:
            signature = target_signatures.get(design_label)
            if not signature or int(signature.get("ray_count", 0)) != 1:
                continue
            direct = str(signature.get("continuation_reference") or "").upper()
            nearest = str(
                signature.get("nearest_continuation_reference_candidate") or ""
            ).upper()
            nearest_distance = float(
                signature.get("nearest_continuation_reference_distance") or 1_000.0
            )
            if direct == source_drawing:
                ranked.append((0, nearest_distance, design_label))
            elif nearest == source_drawing and nearest_distance <= 120.0:
                ranked.append((1, nearest_distance, design_label))
        ranked.sort()
        if not ranked:
            continue
        if (
            len(ranked) > 1
            and ranked[1][0] == ranked[0][0]
            and ranked[1][1] - ranked[0][1] < 35.0
        ):
            continue
        reference_rank, reference_distance, design_label = ranked[0]
        addition = _make_line_topology_match(
            target, design_label, source, ep3d_label,
            method="whole-line-explicit-singleton-sheet-port",
            evidence={
                "source_ep3d_reference": reference,
                "target_ep3d_sheet": target_sheet,
                "target_design_back_reference": source_drawing,
                "reference_kind": "direct" if reference_rank == 0 else "nearest",
                "reference_distance": round(reference_distance, 3),
                "source_page_callout_count": 1,
            },
        )
        if int(addition["semantic_gate"].get("hard_violation_count", 0)):
            continue
        _replace_pair_matches(target, set(), [addition])
        source["mapping"].setdefault("cross_page_owned_ep3d", []).append(
            ep3d_label
        )
        event = {
            "event_type": "explicit-singleton-sheet-port",
            "line_id": source.get("line_id"),
            "source_design_page": source.get("design_page"),
            "target_design_page": target.get("design_page"),
            "source_ep3d_label": ep3d_label,
            "target_design_label": design_label,
            "source_ep3d_reference": reference,
            "reference_distance": round(reference_distance, 3),
            "number_used_as_identity": False,
        }
        events.append(event)
        target["mapping"].setdefault(
            "explicit_singleton_sheet_port_reconciliation", {"events": []}
        )["events"].append(event)
    return events


def _reconcile_explicit_reciprocal_page_ports(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Move a direct EP ``n/m`` terminal to a reciprocal design endpoint.

    This handles ordinary multi-weld sheets, including a cascade where the
    target design endpoint is temporarily occupied by the next boundary weld.
    A move is accepted only when the EP terminal has a direct sheet reference
    and the target design endpoint independently points back to the source
    drawing.  A nearest-only design reference is normally restricted to 40
    pt.  It may extend to 120 pt only for a native FIELD weld landing on a
    unique, strongly detected flange terminal; that extra shape/topology gate
    covers continuation text placed far from the physical page endpoint.
    """

    events: list[dict[str, Any]] = []
    by_line_sheet = {
        (str(pair.get("line_id", "")), _ep3d_file_sheet_sequence(pair.get("ep3d_file"))): pair
        for pair in results
        if _ep3d_file_sheet_sequence(pair.get("ep3d_file")) is not None
    }
    # Moving a downstream boundary can free the reciprocal endpoint for the
    # preceding boundary.  Iterate to a fixed point rather than depending on
    # PDF or filename order.
    for _ in range(max(1, len(results))):
        progress = False
        for source in results:
            source_mapping = source.get("mapping", {})
            owned = {
                str(value).upper()
                for value in source_mapping.get("cross_page_owned_ep3d", [])
            }
            local_owner = {
                str(match.get("ep3d_label", "")).upper(): match
                for match in source_mapping.get("matches", [])
                if not match.get("ep3d_source_file")
                or match.get("ep3d_source_file") == source.get("ep3d_file")
            }
            for ep3d_label, signature in _pair_signature_map(
                source, "ep3d"
            ).items():
                if ep3d_label in owned:
                    continue
                if not (
                    int(signature.get("ray_count", 0)) == 1
                    and str(signature.get("structure_class"))
                    == "continuation-terminal"
                ):
                    continue
                reference = str(
                    signature.get("continuation_reference") or ""
                ).upper()
                target_sheet = _ep3d_reference_sheet_sequence(reference)
                if target_sheet is None:
                    continue
                target = by_line_sheet.get((
                    str(source.get("line_id", "")), target_sheet
                ))
                if target is None or target is source:
                    continue
                source_drawing = str(
                    source.get("isometric_drawing_no") or ""
                ).upper()
                source_fabrication = str(
                    (signature.get("weld_list_semantics") or {}).get(
                        "fabrication", ""
                    )
                ).upper()
                target_signatures = _pair_signature_map(target, "design")
                target_free = {
                    str(value).upper()
                    for value in target.get("mapping", {}).get(
                        "unmatched_design", []
                    )
                }
                ranked = []
                for design_label in target_free:
                    target_signature = target_signatures.get(design_label)
                    if (
                        not target_signature
                        or int(target_signature.get("ray_count", 0)) != 1
                    ):
                        continue
                    direct = str(
                        target_signature.get("continuation_reference") or ""
                    ).upper()
                    nearest = str(target_signature.get(
                        "nearest_continuation_reference_candidate"
                    ) or "").upper()
                    distance = float(target_signature.get(
                        "nearest_continuation_reference_distance"
                    ) or 1_000.0)
                    if direct == source_drawing:
                        ranked.append((0, distance, design_label))
                    elif nearest == source_drawing and distance <= 120.0:
                        # Retain every nearby reciprocal endpoint in the
                        # ambiguity ranking even if it cannot satisfy the
                        # stronger distant-flange acceptance gate below.
                        ranked.append((1, distance, design_label))
                ranked.sort()
                if not ranked:
                    continue
                if (
                    len(ranked) > 1
                    and ranked[1][0] == ranked[0][0]
                    and ranked[1][1] - ranked[0][1] < 35.0
                ):
                    continue
                reference_rank, reference_distance, design_label = ranked[0]
                selected_signature = target_signatures[design_label]
                if reference_rank == 1 and reference_distance > 40.0 and not (
                    source_fabrication == "FIELD"
                    and selected_signature.get("structure_class")
                    == "component-terminal"
                    and selected_signature.get("shape_class") == "flange-like"
                    and int(selected_signature.get(
                        "transverse_stroke_count", 0
                    )) >= 3
                ):
                    continue
                current = local_owner.get(ep3d_label)
                removals = set()
                if current is not None:
                    removals.add((
                        str(current.get("design_label", "")).upper(),
                        ep3d_label,
                    ))
                addition = _make_line_topology_match(
                    target, design_label, source, ep3d_label,
                    method="whole-line-explicit-reciprocal-page-port",
                    evidence={
                        "source_ep3d_reference": reference,
                        "target_ep3d_sheet": target_sheet,
                        "target_design_back_reference": source_drawing,
                        "reference_kind": (
                            "direct" if reference_rank == 0
                            else (
                                "nearest-tight" if reference_distance <= 40.0
                                else "nearest-strong-field-flange"
                            )
                        ),
                        "reference_distance": round(reference_distance, 3),
                        "replaced_source_design_label": (
                            None if current is None else str(
                                current.get("design_label", "")
                            ).upper()
                        ),
                    },
                )
                if int(addition["semantic_gate"].get(
                    "hard_violation_count", 0
                )):
                    continue
                if removals:
                    _replace_pair_matches(source, removals, [])
                _replace_pair_matches(target, set(), [addition])
                source_mapping.setdefault(
                    "cross_page_owned_ep3d", []
                ).append(ep3d_label)
                _refresh_line_pair_unmatched(source)
                _refresh_line_pair_unmatched(target)
                event = {
                    "event_type": "explicit-reciprocal-page-port",
                    "line_id": source.get("line_id"),
                    "source_design_page": source.get("design_page"),
                    "target_design_page": target.get("design_page"),
                    "source_ep3d_label": ep3d_label,
                    "target_design_label": design_label,
                    "source_ep3d_reference": reference,
                    "reference_kind": (
                        "direct" if reference_rank == 0
                        else (
                            "nearest-tight" if reference_distance <= 40.0
                            else "nearest-strong-field-flange"
                        )
                    ),
                    "reference_distance": round(reference_distance, 3),
                    "replaced_source_design_label": (
                        None if current is None else str(
                            current.get("design_label", "")
                        ).upper()
                    ),
                    "number_used_as_identity": False,
                }
                events.append(event)
                target["mapping"].setdefault(
                    "explicit_reciprocal_page_port_reconciliation",
                    {"events": []},
                )["events"].append(event)
                progress = True
        if not progress:
            break
    return events


def _reconcile_nearest_referenced_field_ports(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join a FIELD boundary port using reciprocal nearby page references.

    Some EP3D continuation text is close enough to an unresolved weld root to
    be recorded as a nearest candidate rather than as a direct attachment.
    This weaker route is restricted to native WELD LIST ``FIELD`` welds, a
    zero-ray EP endpoint, an adjacent referenced EP sheet, and exactly one
    free one-ray design endpoint that points back to the source drawing.  A
    nearest design back-reference is accepted only within 70 points; a direct
    design continuation reference remains authoritative beyond that radius.
    """

    by_line_sheet: dict[tuple[str, int], dict[str, Any]] = {}
    for pair in results:
        sheet = _ep3d_file_sheet_sequence(pair.get("ep3d_file"))
        if sheet is not None:
            by_line_sheet[(str(pair.get("line_id", "")), sheet)] = pair
    proposals: list[dict[str, Any]] = []
    for source in results:
        mapping = source.get("mapping", {})
        source_sheet = _ep3d_file_sheet_sequence(source.get("ep3d_file"))
        source_drawing = str(source.get("isometric_drawing_no", "")).upper()
        ep3d_signatures = _pair_signature_map(source, "ep3d")
        for ep3d_label_raw in mapping.get("unmatched_ep3d", []):
            ep3d_label = str(ep3d_label_raw).upper()
            signature = ep3d_signatures.get(ep3d_label, {})
            weld_semantics = signature.get("weld_list_semantics") or {}
            reference = str(
                signature.get("nearest_continuation_reference_candidate") or ""
            ).upper()
            target_sheet = _ep3d_reference_sheet_sequence(reference)
            reference_distance = float(
                signature.get("nearest_continuation_reference_distance") or 1000.0
            )
            if (
                signature.get("continuation_reference")
                or int(signature.get("ray_count", 0)) != 0
                or signature.get("structure_class") != "unresolved"
                or weld_semantics.get("fabrication") != "FIELD"
                or target_sheet is None
                or reference_distance > 50.0
                or source_sheet is None
                or abs(target_sheet - source_sheet) != 1
            ):
                continue
            target = by_line_sheet.get((str(source.get("line_id", "")), target_sheet))
            if target is None or target is source:
                continue
            target_mapping = target.get("mapping", {})
            target_signatures = _pair_signature_map(target, "design")
            candidates = []
            for design_label_raw in target_mapping.get("unmatched_design", []):
                design_label = str(design_label_raw).upper()
                design_signature = target_signatures.get(design_label, {})
                direct_back_reference = str(
                    design_signature.get("continuation_reference") or ""
                ).upper()
                nearest_back_reference = str(
                    design_signature.get("nearest_continuation_reference_candidate")
                    or ""
                ).upper()
                nearest_back_distance = float(
                    design_signature.get("nearest_continuation_reference_distance")
                    or 1000.0
                )
                reciprocal = direct_back_reference == source_drawing or (
                    not direct_back_reference
                    and nearest_back_reference == source_drawing
                    and nearest_back_distance <= 70.0
                )
                if (
                    int(design_signature.get("ray_count", 0)) == 1
                    and design_signature.get("structure_class") == "component-terminal"
                    and reciprocal
                ):
                    candidates.append((design_label, direct_back_reference, nearest_back_distance))
            if len(candidates) != 1:
                continue
            design_label, direct_back_reference, nearest_back_distance = candidates[0]
            proposals.append({
                "source": source,
                "target": target,
                "design_label": design_label,
                "ep3d_label": ep3d_label,
                "reference": reference,
                "reference_distance": reference_distance,
                "direct_back_reference": direct_back_reference or None,
                "nearest_back_reference_distance": nearest_back_distance,
            })
    target_counts = Counter(
        (id(item["target"]), item["design_label"]) for item in proposals
    )
    source_counts = Counter(
        (id(item["source"]), item["ep3d_label"]) for item in proposals
    )
    events = []
    for proposal in proposals:
        target_key = (id(proposal["target"]), proposal["design_label"])
        source_key = (id(proposal["source"]), proposal["ep3d_label"])
        if target_counts[target_key] != 1 or source_counts[source_key] != 1:
            continue
        source, target = proposal["source"], proposal["target"]
        addition = _make_line_topology_match(
            target, proposal["design_label"], source, proposal["ep3d_label"],
            method="whole-line-nearest-referenced-field-port",
            evidence={
                "source_nearest_sheet_reference": proposal["reference"],
                "source_reference_distance": round(proposal["reference_distance"], 3),
                "target_direct_back_reference": proposal["direct_back_reference"],
                "target_nearest_back_reference_distance": round(
                    proposal["nearest_back_reference_distance"], 3
                ),
                "ep3d_weld_list_fabrication": "FIELD",
            },
        )
        if int(addition["semantic_gate"].get("hard_violation_count", 0)):
            continue
        _replace_pair_matches(target, set(), [addition])
        source["mapping"].setdefault("cross_page_owned_ep3d", []).append(
            proposal["ep3d_label"]
        )
        event = {
            "event_type": "nearest-referenced-field-port",
            "line_id": source.get("line_id"),
            "source_design_page": source.get("design_page"),
            "target_design_page": target.get("design_page"),
            "design_label": proposal["design_label"],
            "ep3d_label": proposal["ep3d_label"],
            **addition["line_topology_evidence"],
            "number_used_as_identity": False,
        }
        events.append(event)
        target["mapping"].setdefault(
            "nearest_referenced_field_port_reconciliation", {"events": []}
        )["events"].append(event)
    return events


def _reconcile_referenced_shop_terminal_paths(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join a SHOP boundary branch to a proven flange-terminal path end.

    SHOP ports are not allowed through the weaker FIELD rule.  They require
    additional topology: the unresolved EP weld must be a graph leaf attached
    to an already matched local component, while the referenced sheet must
    contain exactly one free design graph leaf with a strongly detected
    flange signature (one process ray and at least three transverse strokes)
    whose nearby continuation text names the source drawing.
    """

    by_line_sheet: dict[tuple[str, int], dict[str, Any]] = {}
    for pair in results:
        sheet = _ep3d_file_sheet_sequence(pair.get("ep3d_file"))
        if sheet is not None:
            by_line_sheet[(str(pair.get("line_id", "")), sheet)] = pair
    proposals = []
    for source in results:
        mapping = source.get("mapping", {})
        source_sheet = _ep3d_file_sheet_sequence(source.get("ep3d_file"))
        source_drawing = str(source.get("isometric_drawing_no", "")).upper()
        ep3d_signatures = _pair_signature_map(source, "ep3d")
        ep3d_adjacency = _pair_graph_adjacency_by_label(source, "ep3d")
        matched_local_ep3d = {
            str(item.get("ep3d_label", "")).upper()
            for item in mapping.get("matches", [])
            if item.get("ep3d_source_file") in {None, "", source.get("ep3d_file")}
        }
        for ep3d_label_raw in mapping.get("unmatched_ep3d", []):
            ep3d_label = str(ep3d_label_raw).upper()
            signature = ep3d_signatures.get(ep3d_label, {})
            weld_semantics = signature.get("weld_list_semantics") or {}
            reference = str(
                signature.get("nearest_continuation_reference_candidate") or ""
            ).upper()
            target_sheet = _ep3d_reference_sheet_sequence(reference)
            reference_distance = float(
                signature.get("nearest_continuation_reference_distance") or 1000.0
            )
            neighbours = ep3d_adjacency.get(ep3d_label, set())
            if (
                signature.get("continuation_reference")
                or int(signature.get("ray_count", 0)) != 0
                or signature.get("structure_class") != "unresolved"
                or weld_semantics.get("fabrication") != "SHOP"
                or target_sheet is None
                or reference_distance > 40.0
                or source_sheet is None
                or abs(target_sheet - source_sheet) != 1
                or len(neighbours) != 1
                or not neighbours <= matched_local_ep3d
            ):
                continue
            target = by_line_sheet.get((str(source.get("line_id", "")), target_sheet))
            if target is None or target is source:
                continue
            target_mapping = target.get("mapping", {})
            target_signatures = _pair_signature_map(target, "design")
            target_adjacency = _pair_graph_adjacency_by_label(target, "design")
            candidates = []
            for design_label_raw in target_mapping.get("unmatched_design", []):
                design_label = str(design_label_raw).upper()
                design_signature = target_signatures.get(design_label, {})
                back_reference = str(
                    design_signature.get("continuation_reference")
                    or design_signature.get("nearest_continuation_reference_candidate")
                    or ""
                ).upper()
                if (
                    int(design_signature.get("ray_count", 0)) == 1
                    and design_signature.get("structure_class") == "component-terminal"
                    and design_signature.get("shape_class") == "flange-like"
                    and int(design_signature.get("transverse_stroke_count", 0)) >= 3
                    and len(target_adjacency.get(design_label, set())) == 1
                    and back_reference == source_drawing
                ):
                    candidates.append(design_label)
            if len(candidates) == 1:
                proposals.append({
                    "source": source, "target": target,
                    "design_label": candidates[0], "ep3d_label": ep3d_label,
                    "reference": reference,
                    "reference_distance": reference_distance,
                    "source_graph_neighbour": next(iter(neighbours)),
                })
    target_counts = Counter(
        (id(item["target"]), item["design_label"]) for item in proposals
    )
    events = []
    for proposal in proposals:
        if target_counts[(id(proposal["target"]), proposal["design_label"])] != 1:
            continue
        source, target = proposal["source"], proposal["target"]
        addition = _make_line_topology_match(
            target, proposal["design_label"], source, proposal["ep3d_label"],
            method="whole-line-referenced-shop-terminal-path",
            evidence={
                "source_nearest_sheet_reference": proposal["reference"],
                "source_reference_distance": round(proposal["reference_distance"], 3),
                "source_matched_graph_neighbour": proposal["source_graph_neighbour"],
                "target_terminal_graph_degree": 1,
                "target_minimum_transverse_strokes": 3,
                "ep3d_weld_list_fabrication": "SHOP",
            },
        )
        if int(addition["semantic_gate"].get("hard_violation_count", 0)):
            continue
        _replace_pair_matches(target, set(), [addition])
        source["mapping"].setdefault("cross_page_owned_ep3d", []).append(
            proposal["ep3d_label"]
        )
        event = {
            "event_type": "referenced-shop-terminal-path",
            "line_id": source.get("line_id"),
            "source_design_page": source.get("design_page"),
            "target_design_page": target.get("design_page"),
            "design_label": proposal["design_label"],
            "ep3d_label": proposal["ep3d_label"],
            **addition["line_topology_evidence"],
            "number_used_as_identity": False,
        }
        events.append(event)
        target["mapping"].setdefault(
            "referenced_shop_terminal_path_reconciliation", {"events": []}
        )["events"].append(event)
    return events


def _existing_identity_vacated_owner_assignment(
    pair: dict[str, Any],
    group_labels: list[str],
    boundary_label: str,
    owner_by_ep3d: dict[str, str],
    *,
    maximum_cost: float = 0.22,
) -> dict[str, Any] | None:
    """Conservatively re-seat one accepted identity after boundary extraction.

    The high-precision route must not turn an unmatched WELD LIST row into a
    new weld merely because it shares a compact material node.  It therefore
    extracts an already accepted boundary identity and lets exactly one other
    already accepted identity inherit the vacated design owner.  Ambiguous
    multi-identity clusters remain untouched for review.
    """

    boundary_label = str(boundary_label).upper()
    labels = [str(value).upper() for value in group_labels]
    boundary_owner = owner_by_ep3d.get(boundary_label)
    if not boundary_owner:
        return None
    remaining = [
        label for label in labels
        if label != boundary_label and label in owner_by_ep3d
    ]
    if len(remaining) != 1:
        return None
    inherited_ep3d = remaining[0]
    cost = _candidate_diagnostic_cost(pair, boundary_owner, inherited_ep3d)
    if cost > maximum_cost:
        return None
    existing_owners = {
        owner_by_ep3d[label] for label in labels if label in owner_by_ep3d
    }
    existing_identities = {
        label for label in labels if label in owner_by_ep3d
    }
    return {
        "assignments": [(boundary_owner, inherited_ep3d, float(cost))],
        "affected_design": existing_owners,
        "affected_ep3d": existing_identities,
        "mode": "existing-identity-vacated-owner-inheritance",
    }


def _reconcile_high_precision_direction_conflicts(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Repair only uniquely proven local direction conflicts.

    ISO pipe lengths are intentionally non-metric, but port directions remain
    useful when the contrast is extreme.  This pass never introduces an EP3D
    identity: it either moves an accepted identity to a free design port or
    swaps two accepted identities.  Both alternatives must be present in the
    page-local diagnostic matrix, preserve component/ray compatibility and
    improve the combined direction score by a wide margin.
    """

    events: list[dict[str, Any]] = []
    for pair in results:
        mapping = pair.get("mapping", {})
        local_matches = [
            item for item in mapping.get("matches", [])
            if item.get("ep3d_source_file") in {None, "", pair.get("ep3d_file")}
        ]
        if not local_matches:
            continue
        design_signatures = _pair_signature_map(pair, "design")
        ep3d_signatures = _pair_signature_map(pair, "ep3d")
        design_callouts = pair.get("design_callouts", [])
        ep3d_callouts = pair.get("ep3d_callouts", [])
        design_labels = [str(item.get("label", "")).upper() for item in design_callouts]
        ep3d_labels = [str(item.get("label", "")).upper() for item in ep3d_callouts]
        design_addresses = dict(zip(
            design_labels,
            mapping.get("design_callout_graph", {}).get("addresses", []),
        ))
        ep3d_addresses = dict(zip(
            ep3d_labels,
            mapping.get("ep3d_callout_graph", {}).get("addresses", []),
        ))
        if not design_addresses or not ep3d_addresses:
            continue
        matrix = np.asarray(mapping.get("affine_matrix", np.eye(3)), dtype=float)

        def semantic(design_label: str, ep3d_label: str) -> dict[str, Any] | None:
            if (
                design_label not in design_signatures
                or ep3d_label not in ep3d_signatures
                or design_label not in design_addresses
                or ep3d_label not in ep3d_addresses
            ):
                return None
            return _semantic_pair_evidence(
                design_signatures[design_label],
                ep3d_signatures[ep3d_label],
                matrix,
                design_addresses[design_label],
                ep3d_addresses[ep3d_label],
            )

        owner_by_design = {
            str(item["design_label"]).upper(): str(item["ep3d_label"]).upper()
            for item in local_matches
        }
        design_by_ep3d = {right: left for left, right in owner_by_design.items()}
        proposals: list[dict[str, Any]] = []
        for ep3d_label, current_design in design_by_ep3d.items():
            current_semantic = semantic(current_design, ep3d_label)
            current_direction = (
                None if current_semantic is None
                else current_semantic.get("port_direction_score")
            )
            if current_direction is None or float(current_direction) > 0.25:
                continue
            alternatives = []
            for candidate_design in design_signatures:
                if candidate_design == current_design:
                    continue
                candidate_semantic = semantic(candidate_design, ep3d_label)
                if candidate_semantic is None:
                    continue
                candidate_direction = candidate_semantic.get("port_direction_score")
                candidate_cost = _candidate_diagnostic_cost(
                    pair, candidate_design, ep3d_label
                )
                if (
                    candidate_direction is None
                    or float(candidate_direction) < 0.95
                    or candidate_cost > 0.22
                    or int(candidate_semantic.get("hard_violation_count", 0))
                    or float(candidate_semantic.get("component_shape_score", 0.0))
                        < float(current_semantic.get("component_shape_score", 0.0))
                    or float(candidate_semantic.get("ray_count_score", 0.0))
                        < float(current_semantic.get("ray_count_score", 0.0))
                ):
                    continue
                displaced_ep3d = owner_by_design.get(candidate_design)
                displaced = None
                direction_loss = 0.0
                if displaced_ep3d:
                    displaced_current = semantic(candidate_design, displaced_ep3d)
                    displaced_alternative = semantic(current_design, displaced_ep3d)
                    if displaced_current is None or displaced_alternative is None:
                        continue
                    displaced_current_direction = displaced_current.get(
                        "port_direction_score"
                    )
                    displaced_alternative_direction = displaced_alternative.get(
                        "port_direction_score"
                    )
                    if (
                        _candidate_diagnostic_cost(
                            pair, current_design, displaced_ep3d
                        ) > 0.22
                        or int(displaced_alternative.get("hard_violation_count", 0))
                        or float(displaced_alternative.get("component_shape_score", 0.0))
                            < float(displaced_current.get("component_shape_score", 0.0))
                        or float(displaced_alternative.get("ray_count_score", 0.0))
                            < float(displaced_current.get("ray_count_score", 0.0))
                        or (
                            displaced_current_direction is None
                            and displaced_alternative_direction is not None
                        )
                        or (
                            displaced_current_direction is not None
                            and displaced_alternative_direction is None
                        )
                    ):
                        continue
                    if displaced_current_direction is not None:
                        direction_loss = (
                            float(displaced_current_direction)
                            - float(displaced_alternative_direction)
                        )
                        if direction_loss > 0.25:
                            continue
                    displaced = {
                        "ep3d_label": displaced_ep3d,
                        "from_design": candidate_design,
                        "to_design": current_design,
                        "semantic": displaced_alternative,
                    }
                gain = (
                    float(candidate_direction)
                    - float(current_direction)
                    - direction_loss
                )
                if gain < 0.65:
                    continue
                alternatives.append({
                    "ep3d_label": ep3d_label,
                    "from_design": current_design,
                    "to_design": candidate_design,
                    "candidate_cost": float(candidate_cost),
                    "direction_gain": gain,
                    "semantic": candidate_semantic,
                    "displaced": displaced,
                })
            alternatives.sort(
                key=lambda item: (-item["direction_gain"], item["candidate_cost"])
            )
            if not alternatives:
                continue
            if (
                len(alternatives) > 1
                and alternatives[0]["direction_gain"]
                - alternatives[1]["direction_gain"] < 0.15
            ):
                continue
            proposals.append(alternatives[0])

        # Reject interacting proposals.  A safe correction must be uniquely
        # local, so no identity or design slot may participate twice.
        proposal_identity_counts = Counter(
            identity
            for proposal in proposals
            for identity in (
                proposal["ep3d_label"],
                (proposal.get("displaced") or {}).get("ep3d_label"),
            )
            if identity
        )
        proposal_design_counts = Counter(
            design_label
            for proposal in proposals
            for design_label in (proposal["from_design"], proposal["to_design"])
        )
        for proposal in proposals:
            identities = {
                proposal["ep3d_label"],
                (proposal.get("displaced") or {}).get("ep3d_label"),
            } - {None}
            designs = {proposal["from_design"], proposal["to_design"]}
            if (
                any(proposal_identity_counts[value] != 1 for value in identities)
                or any(proposal_design_counts[value] != 1 for value in designs)
            ):
                continue
            removals = {(
                proposal["from_design"], proposal["ep3d_label"]
            )}
            assignments = [(
                proposal["to_design"],
                proposal["ep3d_label"],
                proposal["semantic"],
            )]
            displaced = proposal.get("displaced")
            if displaced:
                removals.add((displaced["from_design"], displaced["ep3d_label"]))
                assignments.append((
                    displaced["to_design"],
                    displaced["ep3d_label"],
                    displaced["semantic"],
                ))
            additions = []
            for design_label, ep3d_label, semantic_evidence in assignments:
                addition = _make_line_topology_match(
                    pair, design_label, pair, ep3d_label,
                    method="high-precision-local-port-direction-reassignment",
                    evidence={
                        "direction_gain": round(
                            float(proposal["direction_gain"]), 6
                        ),
                        "identity_set_preserved": True,
                        "unique_direction_candidate": True,
                    },
                )
                addition["semantic_gate"] = semantic_evidence
                additions.append(addition)
            _replace_pair_matches(pair, removals, additions)
            event = {
                "event_type": "high-precision-local-port-direction-reassignment",
                "line_id": pair.get("line_id"),
                "design_page": pair.get("design_page"),
                "assignments": [
                    {"design_label": left, "ep3d_label": right}
                    for left, right, _ in assignments
                ],
                "direction_gain": round(float(proposal["direction_gain"]), 6),
                "identity_set_preserved": True,
                "number_used_as_identity": False,
            }
            events.append(event)
            mapping.setdefault(
                "high_precision_direction_reconciliation", {"events": []}
            )["events"].append(event)
    return events


def _reconcile_high_precision_one_to_many_fragment_partition(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Partition one EP page between its primary and secondary ISO fragments.

    A secondary fragment is not an independent observation of the whole EP
    page.  Identities already accepted by the primary relation therefore do
    not get a second owner.  Removing those duplicate owners exposes the few
    fragment ports that can be recovered from validated component signatures
    or a SHOP flange attached to a FIELD branch material node.
    """

    events: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pair in results:
        groups.setdefault((
            str(pair.get("line_id") or ""),
            str(pair.get("ep3d_file") or ""),
        ), []).append(pair)
    validated_statuses = {
        "human-confirmed",
        "validated-project-signature",
        "compact-component-vector-signature",
        "curved-mst-component-vector-signature",
    }

    def semantic(
        pair: dict[str, Any], design_label: str, ep3d_label: str,
    ) -> dict[str, Any] | None:
        design_signatures = _pair_signature_map(pair, "design")
        ep3d_signatures = _pair_signature_map(pair, "ep3d")
        design_labels = [
            str(item.get("label", "")).upper()
            for item in pair.get("design_callouts", [])
        ]
        ep3d_labels = [
            str(item.get("label", "")).upper()
            for item in pair.get("ep3d_callouts", [])
        ]
        design_addresses = dict(zip(
            design_labels,
            pair.get("mapping", {}).get("design_callout_graph", {}).get(
                "addresses", []
            ),
        ))
        ep3d_addresses = dict(zip(
            ep3d_labels,
            pair.get("mapping", {}).get("ep3d_callout_graph", {}).get(
                "addresses", []
            ),
        ))
        if (
            design_label not in design_signatures
            or ep3d_label not in ep3d_signatures
            or design_label not in design_addresses
            or ep3d_label not in ep3d_addresses
        ):
            return None
        matrix = np.asarray(
            pair.get("mapping", {}).get("affine_matrix", np.eye(3)), dtype=float
        )
        return _semantic_pair_evidence(
            design_signatures[design_label], ep3d_signatures[ep3d_label],
            matrix, design_addresses[design_label], ep3d_addresses[ep3d_label],
        )

    for (line_id, ep3d_file), pages in groups.items():
        if len(pages) < 2:
            continue
        primary_pages = [
            pair for pair in pages
            if str(pair.get("selected_page_relation") or "").startswith("primary")
        ]
        secondary_pages = [
            pair for pair in pages
            if "secondary-page-fragment" in str(
                pair.get("selected_page_relation") or ""
            )
        ]
        if len(primary_pages) != 1 or not secondary_pages:
            continue
        primary = primary_pages[0]
        primary_mapping = primary.get("mapping", {})
        primary_identities = {
            str(match.get("ep3d_label", "")).upper()
            for match in primary_mapping.get("matches", [])
            if match.get("ep3d_source_file") in {None, "", ep3d_file}
        }

        duplicate_removals = []
        for secondary in secondary_pages:
            removals = {
                (
                    str(match.get("design_label", "")).upper(),
                    str(match.get("ep3d_label", "")).upper(),
                )
                for match in secondary.get("mapping", {}).get("matches", [])
                if str(match.get("ep3d_label", "")).upper() in primary_identities
                and match.get("ep3d_source_file") in {None, "", ep3d_file}
            }
            if removals:
                _replace_pair_matches(secondary, removals, [])
                duplicate_removals.extend({
                    "design_page": secondary.get("design_page"),
                    "design_label": left,
                    "ep3d_label": right,
                } for left, right in sorted(removals))
            _refresh_line_pair_unmatched(secondary)

        component_moves = []
        for secondary in secondary_pages:
            mapping = secondary.get("mapping", {})
            ep3d_signatures = _pair_signature_map(secondary, "ep3d")
            design_signatures = _pair_signature_map(secondary, "design")
            for match in list(mapping.get("matches", [])):
                if match.get("ep3d_source_file") not in {None, "", ep3d_file}:
                    continue
                ep3d_label = str(match.get("ep3d_label", "")).upper()
                current_design = str(match.get("design_label", "")).upper()
                ep_signature = ep3d_signatures.get(ep3d_label, {})
                if ep_signature.get("component_shape_validation") not in validated_statuses:
                    continue
                current_semantic = semantic(
                    secondary, current_design, ep3d_label
                )
                if (
                    current_semantic is None
                    or float(current_semantic.get("component_shape_score", 0.0)) > 0.55
                ):
                    continue
                candidates = []
                for design_label in mapping.get("unmatched_design", []):
                    design_label = str(design_label).upper()
                    design_signature = design_signatures.get(design_label, {})
                    if (
                        design_signature.get("component_shape_validation")
                        not in validated_statuses
                    ):
                        continue
                    evidence = semantic(secondary, design_label, ep3d_label)
                    if (
                        evidence is not None
                        and int(evidence.get("hard_violation_count", 0)) == 0
                        and float(evidence.get("component_shape_score", 0.0)) == 1.0
                        and float(evidence.get("ray_count_score", 0.0)) == 1.0
                        and float(evidence.get("port_direction_score") or 0.0) >= 0.98
                    ):
                        candidates.append((design_label, evidence))
                if len(candidates) != 1:
                    continue
                design_label, evidence = candidates[0]
                removal = {(current_design, ep3d_label)}
                addition = _make_line_topology_match(
                    secondary, design_label, secondary, ep3d_label,
                    method="one-to-many-validated-component-fragment-partition",
                    evidence={
                        "primary_identity_partitioned": True,
                        "validated_component_signature": True,
                    },
                )
                addition["semantic_gate"] = evidence
                _replace_pair_matches(secondary, removal, [addition])
                _refresh_line_pair_unmatched(secondary)
                component_moves.append({
                    "design_page": secondary.get("design_page"),
                    "ep3d_label": ep3d_label,
                    "from_design": current_design,
                    "to_design": design_label,
                })

        # A SHOP flange joined in the WELD LIST to a FIELD branch node is a
        # strong fragment boundary.  Geometry is allowed to be non-metric, but
        # the free design port must independently be a unique, perfectly
        # directed flange signature.
        primary_signatures = _pair_signature_map(primary, "ep3d")
        fragment_moves = []
        for match in list(primary_mapping.get("matches", [])):
            ep3d_label = str(match.get("ep3d_label", "")).upper()
            ep_signature = primary_signatures.get(ep3d_label, {})
            weld_semantic = ep_signature.get("weld_list_semantics") or {}
            if (
                weld_semantic.get("fabrication") != "SHOP"
                or ep_signature.get("structure_class") != "component-terminal"
                or ep_signature.get("shape_class") != "flange-like"
            ):
                continue
            shared_field_branches = [
                other_label
                for other_label, other_signature in primary_signatures.items()
                if other_label != ep3d_label
                and _joint_nodes(ep_signature) & _joint_nodes(other_signature)
                and (other_signature.get("weld_list_semantics") or {}).get(
                    "fabrication"
                ) == "FIELD"
                and other_signature.get("structure_class") == "junction"
                and other_signature.get("shape_class") == "tee-or-olet"
            ]
            if not shared_field_branches:
                continue
            candidates = []
            for secondary in secondary_pages:
                if ep3d_label not in {
                    str(value).upper()
                    for value in secondary.get("mapping", {}).get(
                        "unmatched_ep3d", []
                    )
                }:
                    continue
                for design_label in secondary.get("mapping", {}).get(
                    "unmatched_design", []
                ):
                    design_label = str(design_label).upper()
                    design_signature = _pair_signature_map(
                        secondary, "design"
                    ).get(design_label, {})
                    evidence = semantic(secondary, design_label, ep3d_label)
                    if (
                        design_signature.get("structure_class")
                            == "component-terminal"
                        and design_signature.get("shape_class") == "flange-like"
                        and int(design_signature.get("transverse_stroke_count", 0)) >= 4
                        and evidence is not None
                        and int(evidence.get("hard_violation_count", 0)) == 0
                        and float(evidence.get("component_shape_score", 0.0)) == 1.0
                        and float(evidence.get("ray_count_score", 0.0)) == 1.0
                        and float(evidence.get("port_direction_score") or 0.0) >= 0.98
                    ):
                        candidates.append((secondary, design_label, evidence))
            if len(candidates) != 1:
                continue
            secondary, design_label, evidence = candidates[0]
            vacated_design = str(match.get("design_label", "")).upper()
            _replace_pair_matches(
                primary, {(vacated_design, ep3d_label)}, []
            )
            addition = _make_line_topology_match(
                secondary, design_label, secondary, ep3d_label,
                method="one-to-many-weld-list-branch-fragment-partition",
                evidence={
                    "shared_field_branch_ep3d_labels": shared_field_branches,
                    "identity_set_preserved": True,
                },
            )
            addition["semantic_gate"] = evidence
            _replace_pair_matches(secondary, set(), [addition])
            _refresh_line_pair_unmatched(primary)
            _refresh_line_pair_unmatched(secondary)

            # Let one uniquely superior existing primary identity inherit the
            # vacated port.  This prevents a boundary extraction from leaving
            # the following flange identity shifted by one slot.
            inheritance = []
            for owner in primary_mapping.get("matches", []):
                owner_ep3d = str(owner.get("ep3d_label", "")).upper()
                owner_design = str(owner.get("design_label", "")).upper()
                if owner_ep3d == ep3d_label:
                    continue
                current_evidence = owner.get("semantic_gate", {})
                candidate_evidence = semantic(
                    primary, vacated_design, owner_ep3d
                )
                if (
                    candidate_evidence is not None
                    and _candidate_diagnostic_cost(
                        primary, vacated_design, owner_ep3d
                    ) <= 0.22
                    and int(candidate_evidence.get("hard_violation_count", 0)) == 0
                    and float(candidate_evidence.get("component_shape_score", 0.0))
                        > float(current_evidence.get("component_shape_score", 0.0))
                    and float(candidate_evidence.get("ray_count_score", 0.0))
                        > float(current_evidence.get("ray_count_score", 0.0))
                    and float(candidate_evidence.get("port_direction_score") or 0.0)
                        >= 0.95
                ):
                    inheritance.append((
                        owner_design, owner_ep3d, candidate_evidence
                    ))
            inherited = None
            if len(inheritance) == 1:
                owner_design, owner_ep3d, candidate_evidence = inheritance[0]
                inherited_match = _make_line_topology_match(
                    primary, vacated_design, primary, owner_ep3d,
                    method="one-to-many-vacated-fragment-owner-inheritance",
                    evidence={
                        "vacated_by_ep3d_label": ep3d_label,
                        "identity_set_preserved": True,
                    },
                )
                inherited_match["semantic_gate"] = candidate_evidence
                _replace_pair_matches(
                    primary,
                    {(owner_design, owner_ep3d)},
                    [inherited_match],
                )
                _refresh_line_pair_unmatched(primary)
                inherited = {
                    "ep3d_label": owner_ep3d,
                    "from_design": owner_design,
                    "to_design": vacated_design,
                }
            fragment_moves.append({
                "ep3d_label": ep3d_label,
                "from_design_page": primary.get("design_page"),
                "from_design": vacated_design,
                "to_design_page": secondary.get("design_page"),
                "to_design": design_label,
                "shared_field_branch_ep3d_labels": shared_field_branches,
                "inherited_vacated_owner": inherited,
            })

        if duplicate_removals or component_moves or fragment_moves:
            event = {
                "event_type": "high-precision-one-to-many-fragment-partition",
                "line_id": line_id,
                "ep3d_file": ep3d_file,
                "duplicate_secondary_owners_removed": duplicate_removals,
                "validated_component_moves": component_moves,
                "weld_list_fragment_moves": fragment_moves,
                "identity_set_preserved": True,
                "number_used_as_identity": False,
            }
            events.append(event)
            primary_mapping.setdefault(
                "high_precision_one_to_many_fragment_partition", {"events": []}
            )["events"].append(event)
    return events


def reconcile_line_open_port_topology(
    results: list[dict[str, Any]],
    *,
    high_precision_existing_identity_reassignment: bool = False,
) -> dict[str, Any]:
    """Coordinate page-local results on a whole-line open-port graph.

    The layer uses only PDF-derived evidence.  It identifies an EP3D weld on
    the preceding sheet whose WELD LIST material node continues into an MST
    endpoint on the next sheet, inserts that weld at the next design path
    endpoint, and locally re-solves a compact three-port split if present.
    Page numbers and weld numbers are never used as identity.
    """

    high_precision_fragment_events = (
        _reconcile_high_precision_one_to_many_fragment_partition(results)
        if high_precision_existing_identity_reassignment else []
    )
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    high_precision_direction_events = (
        _reconcile_high_precision_direction_conflicts(results)
        if high_precision_existing_identity_reassignment else []
    )
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    local_graph_events = _reconcile_local_graph_chain_endpoints(results)
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    sandwiched_internal_port_events = (
        _reconcile_graph_sandwiched_internal_ports(results)
    )
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    referenced_boundary_path_events = (
        _reconcile_unmatched_referenced_boundary_paths(results)
    )
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    singleton_sheet_events = _reconcile_explicit_singleton_sheet_ports(results)
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    reciprocal_page_events = _reconcile_explicit_reciprocal_page_ports(results)
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    events: list[dict[str, Any]] = []
    by_line: dict[str, list[dict[str, Any]]] = {}
    for pair in results:
        by_line.setdefault(str(pair.get("line_id", "")), []).append(pair)
    for line_id, pages in by_line.items():
        ordered = sorted(
            pages,
            key=lambda item: (
                _drawing_sheet_sequence(item.get("isometric_drawing_no"))
                if _drawing_sheet_sequence(item.get("isometric_drawing_no")) is not None
                else 10**9,
                int(item.get("design_page", 0)),
            ),
        )
        for previous, following in zip(ordered, ordered[1:]):
            previous_ep3d_file = str(previous.get("ep3d_file") or "")
            initial_previous_owner = {
                str(match["ep3d_label"]).upper():
                    str(match["design_label"]).upper()
                for match in previous["mapping"].get("matches", [])
                if (
                    not match.get("ep3d_source_file")
                    or str(match.get("ep3d_source_file")) == previous_ep3d_file
                )
            }
            previous_sequence = _drawing_sheet_sequence(previous.get("isometric_drawing_no"))
            following_sequence = _drawing_sheet_sequence(following.get("isometric_drawing_no"))
            previous_ep_sheet = _ep3d_file_sheet_sequence(previous.get("ep3d_file"))
            following_ep_sheet = _ep3d_file_sheet_sequence(following.get("ep3d_file"))
            if (
                previous_sequence is None or following_sequence != previous_sequence + 1
                or previous_ep_sheet is None or following_ep_sheet is None
                or abs(following_ep_sheet - previous_ep_sheet) != 1
            ):
                continue
            previous_ep_signatures = _pair_signature_map(previous, "ep3d")
            following_ep_signatures = _pair_signature_map(following, "ep3d")
            following_design_signatures = _pair_signature_map(following, "design")
            previous_ep_paths = _pair_mst_paths(previous, "ep3d")
            following_ep_paths = _pair_mst_paths(following, "ep3d")
            following_design_paths = _pair_mst_paths(following, "design")
            if not previous_ep_paths or not following_ep_paths or not following_design_paths:
                continue
            compact_groups = _compact_weld_list_joint_groups(previous)
            compact_by_label = {
                label: group for group in compact_groups for label in group["labels"]
            }
            previous_path_endpoints = {
                endpoint for path in previous_ep_paths for endpoint in (path[0], path[-1])
            }
            candidates = []
            following_matches = {
                str(item["ep3d_label"]).upper(): str(item["design_label"]).upper()
                for item in following["mapping"].get("matches", [])
                if not item.get("ep3d_source_file")
            }
            following_unmatched_design = {
                str(value).upper()
                for value in following["mapping"].get("unmatched_design", [])
            }
            for ep_path in following_ep_paths:
                for following_entry in (ep_path[0], ep_path[-1]):
                    following_nodes = _joint_nodes(
                        following_ep_signatures[following_entry]
                    )
                    if not following_nodes:
                        continue
                    ep_order = ep_path if ep_path[0] == following_entry else list(reversed(ep_path))
                    for design_path in following_design_paths:
                        if len(design_path) < 2:
                            continue
                        orientation_options = []
                        for design_order in (design_path, list(reversed(design_path))):
                            positions = {label: index for index, label in enumerate(design_order)}
                            ep_positions = {label: index for index, label in enumerate(ep_order)}
                            anchors = [
                                (positions[design], ep_positions[ep])
                                for ep, design in following_matches.items()
                                if design in positions and ep in ep_positions
                            ]
                            offsets = [left - right for left, right in anchors]
                            median_offset = float(np.median(offsets)) if offsets else 0.0
                            # A page boundary may insert several design-only
                            # ports before the first EP3D weld.  Constant
                            # offset is harmless; reversal/non-monotonic order
                            # is what invalidates an orientation.
                            score = sum(
                                abs((left - right) - median_offset)
                                for left, right in anchors
                            ) / max(1, len(anchors))
                            orientation_options.append((score, -len(anchors), design_order))
                        _, negative_anchor_count, design_order = min(
                            orientation_options, key=lambda item: (item[0], item[1])
                        )
                        if -negative_anchor_count < 2:
                            continue
                        design_entry, design_second = design_order[:2]
                        entry_owner = following_matches.get(following_entry)
                        insertion_mode = None
                        if design_entry in following_unmatched_design:
                            insertion_mode = "free-design-entry"
                        elif (
                            entry_owner == design_entry
                            and design_second in following_unmatched_design
                        ):
                            insertion_mode = "shift-following-entry-one-port"
                        if insertion_mode is None:
                            continue
                        following_entry_signature = following_design_signatures[
                            design_entry
                        ]
                        following_entry_is_open = bool(
                            int(following_entry_signature.get("ray_count", 0)) == 1
                            or str(
                                following_entry_signature.get(
                                    "continuation_reference"
                                ) or ""
                            ).upper()
                            == str(previous.get("isometric_drawing_no") or "").upper()
                        )
                        same_page_design_entry = None
                        if not following_entry_is_open:
                            previous_design_signatures = _pair_signature_map(
                                previous, "design"
                            )
                            previous_endpoint_candidates = []
                            for previous_design_path in _pair_mst_paths(
                                previous, "design"
                            ):
                                for endpoint in (
                                    previous_design_path[0],
                                    previous_design_path[-1],
                                ):
                                    signature = previous_design_signatures[endpoint]
                                    if (
                                        str(signature.get(
                                            "nearest_continuation_reference_candidate"
                                        ) or "").upper()
                                        == str(following.get(
                                            "isometric_drawing_no"
                                        ) or "").upper()
                                        and float(signature.get(
                                            "nearest_continuation_reference_distance"
                                        ) or 1_000.0) <= 220.0
                                    ):
                                        previous_endpoint_candidates.append((
                                            float(signature[
                                                "nearest_continuation_reference_distance"
                                            ]), endpoint,
                                        ))
                            previous_endpoint_candidates.sort()
                            if (
                                len(previous_endpoint_candidates) == 1
                                or (
                                    len(previous_endpoint_candidates) > 1
                                    and previous_endpoint_candidates[1][0]
                                    - previous_endpoint_candidates[0][0] >= 35.0
                                )
                            ):
                                same_page_design_entry = (
                                    previous_endpoint_candidates[0][1]
                                )
                        for previous_label, previous_signature in previous_ep_signatures.items():
                            shared_nodes = sorted(
                                _joint_nodes(previous_signature) & following_nodes
                            )
                            if not shared_nodes:
                                continue
                            group = compact_by_label.get(previous_label)
                            is_previous_endpoint = previous_label in previous_path_endpoints
                            if group is None and not is_previous_endpoint:
                                continue
                            if group is not None:
                                same_group_links = [
                                    label for label in group["labels"]
                                    if _joint_nodes(previous_ep_signatures[label]) & following_nodes
                                ]
                                if same_group_links != [previous_label]:
                                    continue
                            candidates.append({
                                "previous_ep3d_label": previous_label,
                                "following_ep3d_label": following_entry,
                                "design_entry": design_entry,
                                "design_second": design_second,
                                "insertion_mode": insertion_mode,
                                "shared_joint_nodes": shared_nodes,
                                "compact_group": group,
                                "previous_is_path_endpoint": is_previous_endpoint,
                                "following_ep3d_order": ep_order,
                                "following_design_order": design_order,
                                "anchor_count": -negative_anchor_count,
                                "orientation_residual": score,
                                "design_entry_reference_match": (
                                    str(
                                        following_design_signatures[design_entry].get(
                                            "nearest_continuation_reference_candidate"
                                        ) or ""
                                    ).upper()
                                    == str(previous.get("isometric_drawing_no") or "").upper()
                                    and float(
                                        following_design_signatures[design_entry].get(
                                            "nearest_continuation_reference_distance"
                                        ) or 1_000.0
                                    ) <= 220.0
                                ),
                                "following_entry_is_open": following_entry_is_open,
                                "same_page_design_entry": same_page_design_entry,
                                "previous_local_owner": initial_previous_owner.get(
                                    previous_label
                                ),
                            })
            # Duplicate path combinations can describe the same physical
            # boundary.  Collapse them by the three identities before the
            # uniqueness gate.
            unique: dict[tuple[str, str, str], dict[str, Any]] = {}
            for candidate in candidates:
                key = (
                    candidate["previous_ep3d_label"],
                    candidate["following_ep3d_label"],
                    candidate["design_entry"],
                )
                current = unique.get(key)
                if current is None or candidate["anchor_count"] > current["anchor_count"]:
                    unique[key] = candidate
            candidates = list(unique.values())
            if high_precision_existing_identity_reassignment:
                # A whole-line correction may relocate an identity accepted
                # by the page-local matcher, but must not manufacture a new
                # identity from an unmatched WELD LIST row.  Without an
                # explicit page-reference arrow, require the stronger compact
                # shared-material-node observation as well.
                candidates = [
                    item for item in candidates
                    if item.get("previous_local_owner")
                    and (
                        item.get("compact_group") is not None
                        or bool(item.get("design_entry_reference_match"))
                    )
                ]
            candidates = [
                item for item in candidates
                if item.get("following_entry_is_open")
                or (
                    item.get("compact_group") is not None
                    and item.get("same_page_design_entry") is not None
                )
            ]
            compact_candidates = [
                item for item in candidates if item.get("compact_group") is not None
            ]
            if compact_candidates:
                candidates = compact_candidates
            else:
                previous_design_endpoints = {
                    endpoint
                    for path in _pair_mst_paths(previous, "design")
                    for endpoint in (path[0], path[-1])
                }
                previous_owner = {
                    str(match["ep3d_label"]).upper(): str(match["design_label"]).upper()
                    for match in previous["mapping"].get("matches", [])
                }
                previous_unmatched_ep3d = {
                    str(value).upper()
                    for value in previous["mapping"].get("unmatched_ep3d", [])
                }
                candidates = [
                    item for item in candidates
                    if (
                        previous_owner.get(item["previous_ep3d_label"])
                        in previous_design_endpoints
                        or item["previous_ep3d_label"] in previous_unmatched_ep3d
                    )
                ]
            reference_directed = [
                item for item in candidates
                if bool(item.get("design_entry_reference_match"))
            ]
            if reference_directed:
                candidates = reference_directed
            candidates.sort(key=lambda item: (
                float(item.get("orientation_residual", 100.0)),
                -int(item.get("anchor_count", 0)),
            ))
            if not candidates:
                continue
            if (
                len(candidates) > 1
                and float(candidates[1].get("orientation_residual", 100.0))
                - float(candidates[0].get("orientation_residual", 100.0)) < 0.35
            ):
                continue
            candidate = candidates[0]
            boundary_label = candidate["previous_ep3d_label"]
            following_entry = candidate["following_ep3d_label"]
            design_entry = candidate["design_entry"]
            design_second = candidate["design_second"]
            same_page_design_entry = candidate.get("same_page_design_entry")
            same_page_construction_split = bool(
                not candidate.get("following_entry_is_open")
                and same_page_design_entry
            )
            previous_matches = previous["mapping"].get("matches", [])
            following_page_matches = following["mapping"].get("matches", [])
            removals_previous: set[tuple[str, str]] = set()
            additions_previous: list[dict[str, Any]] = []
            cluster_audit = None
            group = candidate.get("compact_group")
            if group is not None:
                if high_precision_existing_identity_reassignment:
                    conservative = _existing_identity_vacated_owner_assignment(
                        previous,
                        group["labels"],
                        boundary_label,
                        initial_previous_owner,
                    )
                    if conservative is None:
                        continue
                    assignments = conservative["assignments"]
                    affected_design = conservative["affected_design"]
                    affected_ep = conservative["affected_ep3d"]
                    reallocation_mode = conservative["mode"]
                else:
                    remaining_ep = [
                        label for label in group["labels"] if label != boundary_label
                    ]
                    owners = {
                        str(match["design_label"]).upper()
                        for match in previous_matches
                        if str(match["ep3d_label"]).upper() in group["labels"]
                    }
                    design_candidates = set(owners)
                    for label in previous["mapping"].get("unmatched_design", []):
                        label = str(label).upper()
                        if any(
                            _candidate_diagnostic_cost(previous, label, ep_label) <= 0.22
                            for ep_label in remaining_ep
                        ):
                            design_candidates.add(label)
                    design_candidates = {
                        label for label in design_candidates
                        if label in _pair_signature_map(previous, "design")
                    }
                    if len(design_candidates) < len(remaining_ep):
                        continue
                    design_list = sorted(design_candidates)
                    costs = np.asarray([
                        [
                            _candidate_diagnostic_cost(previous, design_label, ep_label)
                            for ep_label in remaining_ep
                        ]
                        for design_label in design_list
                    ], dtype=float)
                    rows, columns = linear_sum_assignment(costs)
                    assignments = [
                        (design_list[int(row)], remaining_ep[int(column)], float(costs[row, column]))
                        for row, column in zip(rows, columns)
                        if float(costs[row, column]) <= 0.22
                    ]
                    if len(assignments) != len(remaining_ep):
                        continue
                    affected_design = {left for left, _, _ in assignments} | owners
                    affected_ep = set(group["labels"])
                    reallocation_mode = "full-compact-cluster-assignment"
                for match in previous_matches:
                    left = str(match["design_label"]).upper()
                    right = str(match["ep3d_label"]).upper()
                    if left in affected_design or right in affected_ep:
                        removals_previous.add((left, right))
                for design_label, ep3d_label, cost in assignments:
                    additions_previous.append(_make_line_topology_match(
                        previous, design_label, previous, ep3d_label,
                        method="compact-split-after-cross-page-port-extraction",
                        evidence={
                            "shared_joint_node": group["joint_node"],
                            "compact_group_labels": group["labels"],
                            "candidate_diagnostic_cost": round(cost, 6),
                        },
                    ))
                cluster_audit = {
                    "joint_node": group["joint_node"],
                    "labels": group["labels"],
                    "mode": reallocation_mode,
                    "assignments": [
                        {"design_label": left, "ep3d_label": right, "cost": round(cost, 6)}
                        for left, right, cost in assignments
                    ],
                }
            else:
                for match in previous_matches:
                    if str(match["ep3d_label"]).upper() == boundary_label:
                        removals_previous.add((
                            str(match["design_label"]).upper(), boundary_label
                        ))
            removals_following: set[tuple[str, str]] = set()
            additions_following: list[dict[str, Any]] = []
            if (
                not same_page_construction_split
                and candidate["insertion_mode"] == "shift-following-entry-one-port"
            ):
                removals_following.add((design_entry, following_entry))
                additions_following.append(_make_line_topology_match(
                    following, design_second, following, following_entry,
                    method="whole-line-boundary-prefix-shift",
                    evidence={
                        "inserted_predecessor_ep3d_label": boundary_label,
                        "shared_joint_nodes": candidate["shared_joint_nodes"],
                    },
                ))
            boundary_design_pair = previous if same_page_construction_split else following
            boundary_design_label = (
                same_page_design_entry
                if same_page_construction_split else design_entry
            )
            cross_match = _make_line_topology_match(
                boundary_design_pair, boundary_design_label, previous, boundary_label,
                method=(
                    "whole-line-construction-split-shared-design-port"
                    if same_page_construction_split
                    else "whole-line-cross-page-open-port"
                ),
                evidence={
                    "previous_isometric_drawing_no": previous.get("isometric_drawing_no"),
                    "following_isometric_drawing_no": following.get("isometric_drawing_no"),
                    "shared_joint_nodes": candidate["shared_joint_nodes"],
                    "previous_is_path_endpoint": candidate["previous_is_path_endpoint"],
                    "insertion_mode": candidate["insertion_mode"],
                    "same_page_construction_split": same_page_construction_split,
                },
            )
            if same_page_construction_split:
                cross_match["allow_shared_design_port"] = True
                cross_match["association_role"] = (
                    "construction-split-coincident-design-port"
                )
                additions_previous.append(cross_match)
            else:
                additions_following.append(cross_match)
            _replace_pair_matches(previous, removals_previous, additions_previous)
            _replace_pair_matches(following, removals_following, additions_following)
            if not same_page_construction_split:
                previous["mapping"].setdefault("cross_page_owned_ep3d", []).append(
                    boundary_label
                )
            event = {
                "line_id": line_id,
                "previous_design_page": previous.get("design_page"),
                "following_design_page": following.get("design_page"),
                "previous_isometric_drawing_no": previous.get("isometric_drawing_no"),
                "following_isometric_drawing_no": following.get("isometric_drawing_no"),
                "previous_ep3d_label": boundary_label,
                "following_ep3d_label": following_entry,
                "following_design_entry": design_entry,
                "resolved_design_page": boundary_design_pair.get("design_page"),
                "resolved_design_label": boundary_design_label,
                "insertion_mode": (
                    "same-page-construction-split-association"
                    if same_page_construction_split
                    else candidate["insertion_mode"]
                ),
                "shared_joint_nodes": candidate["shared_joint_nodes"],
                "orientation_residual": round(
                    float(candidate.get("orientation_residual", 0.0)), 6
                ),
                "design_entry_reference_match": bool(
                    candidate.get("design_entry_reference_match")
                ),
                "cluster_reallocation": cluster_audit,
                "number_used_as_identity": False,
            }
            events.append(event)
            following["mapping"].setdefault(
                "whole_line_open_port_topology", {"events": []}
            )["events"].append(event)
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    # Keep the native WELD LIST/material-node whole-line solver ahead of the
    # weaker nearest-text routes.  Besides assigning its boundary port, it can
    # re-solve a compact same-page split sharing that material node.  Running
    # the nearest FIELD rule first would consume the boundary and suppress that
    # established local repair (for example SA sheet 1, node ``2``).
    nearest_field_port_events = _reconcile_nearest_referenced_field_ports(results)
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    referenced_shop_terminal_events = (
        _reconcile_referenced_shop_terminal_paths(results)
    )
    for pair in results:
        _refresh_line_pair_unmatched(pair)
    duplicate_ep3d = 0
    for line_id, pages in by_line.items():
        identities = [
            (
                str(match.get("ep3d_source_file") or pair.get("ep3d_file") or ""),
                str(match["ep3d_label"]).upper(),
            )
            for pair in pages for match in pair["mapping"].get("matches", [])
        ]
        duplicate_ep3d += len(identities) - len(set(identities))
    return {
        "method": "whole-line-weld-list-open-port-topology",
        "event_count": len(events),
        "events": events,
        "high_precision_fragment_event_count": len(
            high_precision_fragment_events
        ),
        "high_precision_fragment_events": high_precision_fragment_events,
        "high_precision_direction_event_count": len(
            high_precision_direction_events
        ),
        "high_precision_direction_events": high_precision_direction_events,
        "local_graph_event_count": len(local_graph_events),
        "local_graph_events": local_graph_events,
        "sandwiched_internal_port_event_count": len(sandwiched_internal_port_events),
        "sandwiched_internal_port_events": sandwiched_internal_port_events,
        "referenced_boundary_path_event_count": len(referenced_boundary_path_events),
        "referenced_boundary_path_events": referenced_boundary_path_events,
        "explicit_singleton_sheet_event_count": len(singleton_sheet_events),
        "explicit_singleton_sheet_events": singleton_sheet_events,
        "explicit_reciprocal_page_event_count": len(reciprocal_page_events),
        "explicit_reciprocal_page_events": reciprocal_page_events,
        "nearest_referenced_field_port_event_count": len(nearest_field_port_events),
        "nearest_referenced_field_port_events": nearest_field_port_events,
        "referenced_shop_terminal_event_count": len(referenced_shop_terminal_events),
        "referenced_shop_terminal_events": referenced_shop_terminal_events,
        "duplicate_ep3d_assignment_count": duplicate_ep3d,
        "high_precision_existing_identity_reassignment": bool(
            high_precision_existing_identity_reassignment
        ),
        "number_used_as_identity": False,
        "human_truth_used_at_runtime": False,
    }
