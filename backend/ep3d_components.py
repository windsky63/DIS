"""EP3D-side valve, flange and support callout recognition.

The design-drawing recognizer deliberately does not import this module.  EP3D
component identity comes first from a framed V / FL / SP label and its leader;
local vector geometry is then used to validate and refine the symbol subtype.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
import math
import re
from typing import Any, Iterable

import fitz


EP3D_COMPONENT_LABEL = re.compile(r"(?:FL|SP|V)\d+", re.IGNORECASE)
DESIGN_SUPPORT_LABEL = re.compile(r"S\d+[A-Z0-9-]*", re.IGNORECASE)
_TYPE_BY_PREFIX = {"V": "valve", "FL": "flange", "SP": "support"}


@dataclass(frozen=True)
class Ep3dComponentCallout:
    label: str
    component_type: str
    symbol_variant: str
    label_bbox: tuple[float, float, float, float]
    label_center: tuple[float, float]
    component_point: tuple[float, float]
    leader_start: tuple[float, float]
    leader_end: tuple[float, float]
    geometry_verified: bool
    evidence: tuple[str, ...]
    extraction_method: str
    extraction_confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _display_point(page: fitz.Page, point: fitz.Point) -> tuple[float, float]:
    if int(page.rotation or 0) % 360 == 0:
        return float(point.x), float(point.y)
    mapped = point * page.rotation_matrix
    return float(mapped.x), float(mapped.y)


def _display_bbox(page: fitz.Page, rect: fitz.Rect) -> tuple[float, float, float, float]:
    if int(page.rotation or 0) % 360 == 0:
        return float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    points = (
        _display_point(page, fitz.Point(rect.x0, rect.y0)),
        _display_point(page, fitz.Point(rect.x1, rect.y0)),
        _display_point(page, fitz.Point(rect.x0, rect.y1)),
        _display_point(page, fitz.Point(rect.x1, rect.y1)),
    )
    xs, ys = (point[0] for point in points), (point[1] for point in points)
    xs, ys = list(xs), list(ys)
    return min(xs), min(ys), max(xs), max(ys)


def _point_rect_distance(
    point: tuple[float, float], rect: tuple[float, float, float, float]
) -> float:
    x, y = point
    x0, y0, x1, y1 = rect
    return math.hypot(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1))


def _point_segment_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-12:
        return math.dist(point, start)
    fraction = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator),
    )
    projection = start[0] + fraction * dx, start[1] + fraction * dy
    return math.dist(point, projection)


def _angle(line: dict[str, Any]) -> float:
    start, end = line["start"], line["end"]
    return math.atan2(end[1] - start[1], end[0] - start[0]) % math.pi


def _angle_difference(left: float, right: float) -> float:
    difference = abs(left - right) % math.pi
    return min(difference, math.pi - difference)


def _line_primitives(
    page: fitz.Page, drawings: Iterable[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for drawing_index, drawing in enumerate(drawings if drawings is not None else page.get_drawings()):
        color = drawing.get("color")
        if color is not None and len(color) >= 3 and max(float(value) for value in color[:3]) > 0.35:
            continue
        for item_index, item in enumerate(drawing.get("items", [])):
            if not item or item[0] != "l":
                continue
            start, end = _display_point(page, item[1]), _display_point(page, item[2])
            length = math.dist(start, end)
            if length <= 0.15:
                continue
            result.append({
                "drawing_index": drawing_index,
                "item_index": item_index,
                "start": start,
                "end": end,
                "length": length,
                "width": float(drawing.get("width") or 0.0),
            })
    return result


def _snap(point: tuple[float, float], tolerance: float = 1.25) -> tuple[int, int]:
    return round(point[0] / tolerance), round(point[1] / tolerance)


def _line_cycle_boxes(lines: list[dict[str, Any]]) -> list[tuple[float, float, float, float]]:
    """Recover a four-edge frame when the PDF did not emit a rectangle item."""

    result = []
    for group in combinations(lines, 4):
        nodes: dict[tuple[int, int], list[tuple[float, float]]] = {}
        degree: dict[tuple[int, int], int] = {}
        for line in group:
            for point in (line["start"], line["end"]):
                key = _snap(point)
                nodes.setdefault(key, []).append(point)
                degree[key] = degree.get(key, 0) + 1
        if len(degree) != 4 or any(value != 2 for value in degree.values()):
            continue
        points = [point for values in nodes.values() for point in values]
        x0, x1 = min(point[0] for point in points), max(point[0] for point in points)
        y0, y1 = min(point[1] for point in points), max(point[1] for point in points)
        width, height = x1 - x0, y1 - y0
        if 5.0 <= width <= 75.0 and 5.0 <= height <= 55.0 and 0.42 <= width / max(height, 1e-6) <= 2.8:
            result.append((x0, y0, x1, y1))
    return result


def _frame_candidates(
    page: fitz.Page,
    lines: list[dict[str, Any]],
    label_centers: Iterable[tuple[float, float]] | None = None,
    drawings: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    centres = list(label_centers or [])
    lines_by_drawing: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        lines_by_drawing.setdefault(int(line["drawing_index"]), []).append(line)
    for drawing_index, drawing in enumerate(drawings if drawings is not None else page.get_drawings()):
        drawing_rect = drawing.get("rect")
        if centres and drawing_rect is not None:
            drawing_bbox = _display_bbox(page, drawing_rect)
            if not any(
                drawing_bbox[0] - 3.0 <= center[0] <= drawing_bbox[2] + 3.0
                and drawing_bbox[1] - 3.0 <= center[1] <= drawing_bbox[3] + 3.0
                for center in centres
            ):
                continue
        boxes: list[tuple[float, float, float, float]] = []
        for item in drawing.get("items", []):
            if not item:
                continue
            if item[0] == "re":
                boxes.append(_display_bbox(page, item[1]))
            elif item[0] == "qu":
                boxes.append(_display_bbox(page, item[1].rect))
        local_lines = lines_by_drawing.get(drawing_index, [])
        if 4 <= len(local_lines) <= 12:
            boxes.extend(_line_cycle_boxes(local_lines))
        # EP3D often leaves the side used by the leader visually open, so its
        # tiny SP frame is emitted as a three-edge U rather than ``re``/``qu``.
        if len(local_lines) == 3 and drawing.get("rect") is not None:
            boxes.append(_display_bbox(page, drawing["rect"]))
        for bbox in boxes:
            width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if not (5.0 <= width <= 75.0 and 5.0 <= height <= 55.0):
                continue
            if not 0.42 <= width / max(height, 1e-6) <= 2.8:
                continue
            if any(max(abs(a - b) for a, b in zip(bbox, item["bbox"])) <= 1.0 for item in result):
                continue
            result.append({"bbox": bbox, "drawing_index": drawing_index})
    return result


def _label_candidates(
    page: fitz.Page, pattern: re.Pattern[str] = EP3D_COMPONENT_LABEL
) -> list[tuple[str, tuple[float, float, float, float]]]:
    labels = []
    for word in page.get_text("words"):
        label = re.sub(r"\s+", "", str(word[4])).upper()
        if pattern.fullmatch(label):
            labels.append((label, _display_bbox(page, fitz.Rect(*word[:4]))))
    return labels


def _component_type(label: str) -> str:
    prefix = re.match(r"[A-Z]+", label).group(0)  # type: ignore[union-attr]
    return _TYPE_BY_PREFIX[prefix]


def _leader_paths(
    frame: dict[str, Any], lines: list[dict[str, Any]]
) -> list[tuple[tuple[float, float], tuple[float, float], float, int]]:
    bbox = frame["bbox"]
    paths = []
    endpoint_index: dict[tuple[int, int], list[tuple[dict[str, Any], tuple[float, float], tuple[float, float]]]] = {}
    for candidate in lines:
        endpoint_index.setdefault(_snap(candidate["start"], 1.6), []).append((candidate, candidate["start"], candidate["end"]))
        endpoint_index.setdefault(_snap(candidate["end"], 1.6), []).append((candidate, candidate["end"], candidate["start"]))
    for line in lines:
        if not 4.0 <= line["length"] <= 190.0 or line["width"] > 1.6:
            continue
        start_distance = _point_rect_distance(line["start"], bbox)
        end_distance = _point_rect_distance(line["end"], bbox)
        near_distance = min(start_distance, end_distance)
        if near_distance > 4.5:
            continue
        near = line["start"] if start_distance <= end_distance else line["end"]
        far = line["end"] if start_distance <= end_distance else line["start"]
        if _point_rect_distance(far, bbox) <= 1.0:
            continue
        stream_gap = abs(int(line["drawing_index"]) - int(frame["drawing_index"]))
        paths.append((near, far, near_distance, stream_gap))
        total_length = float(line["length"])
        used = {(int(line["drawing_index"]), int(line["item_index"]))}
        # Follow up to two dog-leg sections, but stop before entering a pipe
        # network.  The final geometry score decides between nearby leaders.
        for _ in range(2):
            connections = []
            key_x, key_y = _snap(far, 1.6)
            local_endpoints = [
                item
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                for item in endpoint_index.get((key_x + dx, key_y + dy), [])
            ]
            for candidate, connection, other in local_endpoints:
                key = int(candidate["drawing_index"]), int(candidate["item_index"])
                if key in used or candidate["width"] > 1.6 or candidate["length"] > 90.0:
                    continue
                if math.dist(far, connection) <= 1.6:
                    connections.append((candidate, other, key))
            # A leader dog-leg has one unambiguous continuation.  Multiple
            # attached strokes mean the leader has already reached a symbol.
            if len(connections) != 1:
                break
            extensions = []
            for candidate, other, key in connections:
                gain = _point_rect_distance(other, bbox) - _point_rect_distance(far, bbox)
                if gain > 1.0:
                    extensions.append((-gain, candidate, other, key))
            if not extensions:
                break
            _, extension, other, key = min(extensions, key=lambda item: item[0])
            used.add(key)
            far = other
            total_length += float(extension["length"])
            paths.append((near, far, near_distance, stream_gap))
            if total_length > 220.0:
                break
    return paths


def _triangle_candidates(
    lines: Iterable[dict[str, Any]],
    point: tuple[float, float],
    radius: float = 34.0,
    *,
    minimum_length: float = 1.5,
    minimum_area: float = 1.8,
) -> list[dict[str, Any]]:
    nearby = [
        line for line in lines
        if minimum_length <= line["length"] <= 32.0
        and _point_segment_distance(point, line["start"], line["end"]) <= radius
    ]
    nearby = sorted(
        nearby,
        key=lambda line: _point_segment_distance(point, line["start"], line["end"]),
    )[:36]
    triangles = []
    for group in combinations(nearby, 3):
        nodes: dict[tuple[int, int], list[tuple[float, float]]] = {}
        degree: dict[tuple[int, int], int] = {}
        for line in group:
            for endpoint in (line["start"], line["end"]):
                key = _snap(endpoint)
                nodes.setdefault(key, []).append(endpoint)
                degree[key] = degree.get(key, 0) + 1
        if len(nodes) != 3 or any(value != 2 for value in degree.values()):
            continue
        vertices = [
            (sum(value[0] for value in values) / len(values), sum(value[1] for value in values) / len(values))
            for values in nodes.values()
        ]
        area = abs(
            sum(
                vertices[index][0] * vertices[(index + 1) % 3][1]
                - vertices[(index + 1) % 3][0] * vertices[index][1]
                for index in range(3)
            )
        ) / 2.0
        if area < minimum_area:
            continue
        center = tuple(sum(vertex[axis] for vertex in vertices) / 3.0 for axis in range(2))
        key = tuple(sorted(_snap(vertex) for vertex in vertices))
        if not any(item["key"] == key for item in triangles):
            triangles.append({"vertices": vertices, "center": center, "key": key})
    return triangles


def _double_triangle_at(point: tuple[float, float], lines: list[dict[str, Any]]) -> bool:
    triangles = _triangle_candidates(lines, point)
    for left, right in combinations(triangles, 2):
        pairs = [
            (a, b) for a in left["vertices"] for b in right["vertices"]
            if math.dist(a, b) <= 2.0
        ]
        if not pairs:
            continue
        shared = min(pairs, key=lambda pair: math.dist(point, pair[0]))
        apex = ((shared[0][0] + shared[1][0]) / 2.0, (shared[0][1] + shared[1][1]) / 2.0)
        if math.dist(point, apex) > 9.0:
            continue
        left_vector = left["center"][0] - apex[0], left["center"][1] - apex[1]
        right_vector = right["center"][0] - apex[0], right["center"][1] - apex[1]
        if left_vector[0] * right_vector[0] + left_vector[1] * right_vector[1] < 0:
            return True
    return False


def _parallel_short_lines_at(point: tuple[float, float], lines: list[dict[str, Any]]) -> bool:
    nearby = [
        line for line in lines
        if 2.0 <= line["length"] <= 30.0
        and _point_segment_distance(point, line["start"], line["end"]) <= 18.0
    ]
    nearby = sorted(
        nearby,
        key=lambda line: _point_segment_distance(point, line["start"], line["end"]),
    )[:40]
    for left, right in combinations(nearby, 2):
        if _angle_difference(_angle(left), _angle(right)) > math.radians(12.0):
            continue
        ratio = max(left["length"], right["length"]) / max(min(left["length"], right["length"]), 1e-6)
        if ratio > 2.5:
            continue
        left_mid = ((left["start"][0] + left["end"][0]) / 2.0, (left["start"][1] + left["end"][1]) / 2.0)
        right_mid = ((right["start"][0] + right["end"][0]) / 2.0, (right["start"][1] + right["end"][1]) / 2.0)
        separation = _point_segment_distance(left_mid, right["start"], right["end"])
        if 1.0 <= separation <= 14.0 and min(math.dist(point, left_mid), math.dist(point, right_mid)) <= 14.0:
            return True
    return False


def _z_arrow_at(point: tuple[float, float], lines: list[dict[str, Any]]) -> bool:
    nearby = [
        line for line in lines
        if 0.25 <= line["length"] <= 42.0
        and _point_segment_distance(point, line["start"], line["end"]) <= 20.0
    ]
    nearby = sorted(
        nearby,
        key=lambda line: _point_segment_distance(point, line["start"], line["end"]),
    )[:40]
    arrow_triangles = [
        triangle
        for triangle in _triangle_candidates(
            nearby, point, 15.0, minimum_length=0.25, minimum_area=0.08
        )
        if math.dist(point, triangle["center"]) <= 9.0
    ]
    if not arrow_triangles:
        return False
    for middle in nearby:
        if _point_segment_distance(point, middle["start"], middle["end"]) > 7.0:
            continue
        sides = [
            line for line in nearby
            if line is not middle and _angle_difference(_angle(line), _angle(middle)) >= math.radians(20.0)
        ]
        for left, right in combinations(sides, 2):
            if _angle_difference(_angle(left), _angle(right)) > math.radians(12.0):
                continue
            left_attachment = min(math.dist(endpoint, middle["start"]) for endpoint in (left["start"], left["end"]))
            right_attachment = min(math.dist(endpoint, middle["end"]) for endpoint in (right["start"], right["end"]))
            reverse_left = min(math.dist(endpoint, middle["end"]) for endpoint in (left["start"], left["end"]))
            reverse_right = min(math.dist(endpoint, middle["start"]) for endpoint in (right["start"], right["end"]))
            if min(max(left_attachment, right_attachment), max(reverse_left, reverse_right)) <= 10.0:
                return True
    return False


def _geometry(
    component_type: str, point: tuple[float, float], lines: list[dict[str, Any]]
) -> tuple[str, bool, tuple[str, ...]]:
    if component_type == "valve":
        if _double_triangle_at(point, lines):
            return "double-triangle-valve", True, ("two-triangles-sharing-apex",)
        if _z_arrow_at(point, lines):
            return "z-arrow-valve", True, ("z-outline", "central-direction-arrow")
        return "label-and-leader-only", False, ("valve-geometry-unresolved",)
    if _parallel_short_lines_at(point, lines):
        name = "parallel-line-flange" if component_type == "flange" else "parallel-line-support"
        return name, True, ("short-parallel-line-pair",)
    return "label-and-leader-only", False, (f"{component_type}-geometry-unresolved",)


def extract_ep3d_component_callouts(page: fitz.Page) -> list[Ep3dComponentCallout]:
    """Recognize EP3D V/FL/SP framed labels and resolve their leader endpoints.

    A valid framed label plus attached leader is sufficient for a result because
    EP3D's leader endpoint is authoritative.  Geometry never changes a prefix's
    component type; it only verifies the symbol and records its subtype.
    """

    drawings = page.get_drawings()
    lines = _line_primitives(page, drawings)
    labels = _label_candidates(page)
    label_centers = [
        ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        for _, bbox in labels
    ]
    frames = _frame_candidates(page, lines, label_centers, drawings)
    found: dict[str, Ep3dComponentCallout] = {}
    for label, text_bbox in labels:
        center = ((text_bbox[0] + text_bbox[2]) / 2.0, (text_bbox[1] + text_bbox[3]) / 2.0)
        enclosing = [
            frame for frame in frames
            if frame["bbox"][0] - 2.5 <= center[0] <= frame["bbox"][2] + 2.5
            and frame["bbox"][1] - 2.5 <= center[1] <= frame["bbox"][3] + 2.5
        ]
        if not enclosing:
            continue
        frame = min(
            enclosing,
            key=lambda item: math.dist(
                center,
                ((item["bbox"][0] + item["bbox"][2]) / 2.0, (item["bbox"][1] + item["bbox"][3]) / 2.0),
            ),
        )
        component_type = _component_type(label)
        ranked = []
        leader_paths = sorted(
            _leader_paths(frame, lines),
            key=lambda item: (item[2], item[3], -math.dist(item[0], item[1])),
        )[:5]
        for near, far, near_distance, stream_gap in leader_paths:
            variant, verified, geometry_evidence = _geometry(component_type, far, lines)
            ranked.append((not verified, near_distance, min(stream_gap, 30), -math.dist(near, far), near, far, variant, verified, geometry_evidence))
        if not ranked:
            continue
        _, near_distance, stream_gap, _, near, far, variant, verified, geometry_evidence = min(ranked)
        confidence = min(
            0.98 if verified else 0.88,
            max(0.72, 0.94 - float(near_distance) / 20.0 - float(stream_gap) / 120.0),
        )
        callout = Ep3dComponentCallout(
            label=label,
            component_type=component_type,
            symbol_variant=variant,
            label_bbox=tuple(float(value) for value in frame["bbox"]),
            label_center=center,
            component_point=far,
            leader_start=near,
            leader_end=far,
            geometry_verified=verified,
            evidence=("native-text-prefix", "enclosing-square-frame", "attached-leader", *geometry_evidence),
            extraction_method="ep3d-framed-component-label-attached-leader",
            extraction_confidence=round(confidence, 3),
        )
        if label not in found or callout.extraction_confidence > found[label].extraction_confidence:
            found[label] = callout
    return sorted(found.values(), key=lambda item: (item.component_type, int(re.search(r"\d+", item.label).group(0))))


def extract_design_support_callouts(page: fitz.Page) -> list[Ep3dComponentCallout]:
    """Resolve design-side framed S-number leaders to support points.

    ``S`` is a design annotation rather than the final EP3D ``SP`` identity.
    Several S frames may describe the same physical support, so results are
    deduplicated by the leader endpoint and retain the labels as evidence only.
    """

    drawings = page.get_drawings()
    lines = _line_primitives(page, drawings)
    labels = _label_candidates(page, DESIGN_SUPPORT_LABEL)
    label_centers = [
        ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        for _, bbox in labels
    ]
    frames = _frame_candidates(page, lines, label_centers, drawings)
    candidates: list[Ep3dComponentCallout] = []
    for label, text_bbox in labels:
        center = ((text_bbox[0] + text_bbox[2]) / 2.0, (text_bbox[1] + text_bbox[3]) / 2.0)
        enclosing = [
            frame for frame in frames
            if frame["bbox"][0] - 2.5 <= center[0] <= frame["bbox"][2] + 2.5
            and frame["bbox"][1] - 2.5 <= center[1] <= frame["bbox"][3] + 2.5
        ]
        if not enclosing:
            # Some design exports emit every side of an S frame as a separate
            # drawing object. Reconstruct only the small local cycle to avoid
            # treating the drawing border or material tables as label frames.
            nearby = [
                line for line in lines
                if line["length"] <= 75.0
                and _point_segment_distance(center, line["start"], line["end"]) <= 28.0
            ]
            local_boxes = _line_cycle_boxes(nearby[:24])
            enclosing = [
                {"bbox": bbox, "drawing_index": min(
                    (int(line["drawing_index"]) for line in nearby
                     if _point_rect_distance(line["start"], bbox) <= 1.5),
                    default=0,
                )}
                for bbox in local_boxes
                if bbox[0] - 2.5 <= center[0] <= bbox[2] + 2.5
                and bbox[1] - 2.5 <= center[1] <= bbox[3] + 2.5
            ]
        if not enclosing:
            continue
        frame = min(enclosing, key=lambda item: math.dist(
            center,
            ((item["bbox"][0] + item["bbox"][2]) / 2.0, (item["bbox"][1] + item["bbox"][3]) / 2.0),
        ))
        paths = sorted(
            _leader_paths(frame, lines),
            key=lambda item: (item[2], item[3], -math.dist(item[0], item[1])),
        )
        if not paths:
            continue
        near, far, near_distance, stream_gap = paths[0]
        variant, verified, geometry_evidence = _geometry("support", far, lines)
        confidence = min(
            0.97 if verified else 0.9,
            max(0.74, 0.94 - float(near_distance) / 20.0 - min(stream_gap, 30) / 120.0),
        )
        candidates.append(Ep3dComponentCallout(
            label=label,
            component_type="support",
            symbol_variant=variant if verified else "design-s-leader-support",
            label_bbox=tuple(float(value) for value in frame["bbox"]),
            label_center=center,
            component_point=far,
            leader_start=near,
            leader_end=far,
            geometry_verified=verified,
            evidence=(
                "design-native-s-number",
                "enclosing-rectangular-frame",
                "attached-leader-authoritative-endpoint",
                *geometry_evidence,
            ),
            extraction_method="design-framed-s-label-attached-leader",
            extraction_confidence=round(confidence, 3),
        ))

    selected: list[Ep3dComponentCallout] = []
    for candidate in sorted(candidates, key=lambda item: item.extraction_confidence, reverse=True):
        duplicate_index = next((
            index for index, item in enumerate(selected)
            if math.dist(item.component_point, candidate.component_point) <= 12.0
        ), None)
        if duplicate_index is None:
            selected.append(candidate)
            continue
        existing = selected[duplicate_index]
        labels = sorted({existing.label, candidate.label})
        selected[duplicate_index] = Ep3dComponentCallout(
            **{
                **existing.__dict__,
                "evidence": tuple(existing.evidence) + (f"merged-design-labels:{','.join(labels)}",),
            }
        )
    return sorted(selected, key=lambda item: (item.component_point[1], item.component_point[0]))
