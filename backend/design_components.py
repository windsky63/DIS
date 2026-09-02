"""Shape-only component recognition for unlabelled design PDFs.

This module is intentionally placement-mode only.  It recognizes component
geometry without reading V / FL / SP labels and never contributes weld
candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Any, Iterable

import fitz


@dataclass(frozen=True)
class DesignComponentSymbol:
    component_type: str
    symbol_variant: str
    center: tuple[float, float]
    bbox: tuple[float, float, float, float]
    extraction_confidence: float
    evidence: tuple[str, ...]
    source_drawing_indices: tuple[int, ...]
    ep3d_symbol_variant: str
    assembly_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "componentType": self.component_type,
            "symbolVariant": self.symbol_variant,
            "center": [round(value, 3) for value in self.center],
            "bbox": [round(value, 3) for value in self.bbox],
            "geometryVerified": True,
            "confidence": round(self.extraction_confidence, 3),
            "evidence": list(self.evidence),
            "sourceDrawingIndices": list(self.source_drawing_indices),
            "assemblyKey": self.assembly_key,
            "ep3dCorrespondence": {
                "componentType": self.component_type,
                "symbolVariant": self.ep3d_symbol_variant,
            },
        }


def _display_point(page: fitz.Page, point: fitz.Point) -> tuple[float, float]:
    if int(page.rotation or 0) % 360 == 0:
        return float(point.x), float(point.y)
    mapped = point * page.rotation_matrix
    return float(mapped.x), float(mapped.y)


def _dark(value: Any, threshold: float = 0.35) -> bool:
    return bool(value is not None and len(value) >= 3 and max(float(item) for item in value[:3]) <= threshold)


def _line_primitives(page: fitz.Page, drawings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines = []
    for drawing_index, drawing in enumerate(drawings):
        if not (_dark(drawing.get("color")) or _dark(drawing.get("fill"))):
            continue
        filled = _dark(drawing.get("fill"))
        for item_index, item in enumerate(drawing.get("items", [])):
            if not item or item[0] != "l":
                continue
            start, end = _display_point(page, item[1]), _display_point(page, item[2])
            length = math.dist(start, end)
            if length <= 0.15:
                continue
            lines.append({
                "drawing_index": drawing_index,
                "item_index": item_index,
                "start": start,
                "end": end,
                "length": length,
                "width": float(drawing.get("width") or 0.0),
                "filled": filled,
            })
    return lines


def _projection(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, tuple[float, float], float]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length2 = dx * dx + dy * dy
    fraction = 0.0 if length2 <= 1e-12 else max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2),
    )
    projected = start[0] + fraction * dx, start[1] + fraction * dy
    return math.dist(point, projected), projected, fraction


def _axis_at(
    point: tuple[float, float], process_segments: list[dict[str, Any]], tolerance: float = 15.0
) -> dict[str, Any] | None:
    ranked = []
    for segment in process_segments:
        start = tuple(float(value) for value in segment["start"])
        end = tuple(float(value) for value in segment["end"])
        distance, projected, fraction = _projection(point, start, end)
        ranked.append((distance, start, end, projected, fraction))
    if not ranked:
        return None
    distance, start, end, projected, fraction = min(ranked, key=lambda item: item[0])
    if distance > tolerance:
        return None
    length = math.dist(start, end)
    if length <= 1e-6:
        return None
    axis = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
    return {
        "distance": distance,
        "projected": projected,
        "axis": axis,
        "normal": (-axis[1], axis[0]),
        "segment": {"start": start, "end": end},
        "fraction": fraction,
    }


def _local(point: tuple[float, float], origin: tuple[float, float], axis: tuple[float, float]) -> tuple[float, float]:
    dx, dy = point[0] - origin[0], point[1] - origin[1]
    return dx * axis[0] + dy * axis[1], -dx * axis[1] + dy * axis[0]


def _angle(line: dict[str, Any]) -> float:
    start, end = line["start"], line["end"]
    return math.atan2(end[1] - start[1], end[0] - start[0]) % math.pi


def _angle_difference(left: float, right: float) -> float:
    difference = abs(left - right) % math.pi
    return min(difference, math.pi - difference)


def _bbox(points: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    values = list(points)
    return (
        min(point[0] for point in values),
        min(point[1] for point in values),
        max(point[0] for point in values),
        max(point[1] for point in values),
    )


def _center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0


def _box_union(boxes: Iterable[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    values = list(boxes)
    return min(box[0] for box in values), min(box[1] for box in values), max(box[2] for box in values), max(box[3] for box in values)


def _box_overlap_fraction(
    inner: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
) -> float:
    """Return how much of ``inner`` is covered by ``outer``."""

    width = max(0.0, inner[2] - inner[0])
    height = max(0.0, inner[3] - inner[1])
    area = width * height
    if area <= 1e-9:
        return 0.0
    overlap_width = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    overlap_height = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    return overlap_width * overlap_height / area


def _contains_solid_arrow(
    symbol: DesignComponentSymbol, filled_triangles: list[dict[str, Any]]
) -> bool:
    """Reject valve-like outlines that contain or duplicate a solid arrow.

    CAD-to-PDF exporters frequently emit the arrow fill and its outline as
    separate paths with identical bounds.  A strict inset test misses that
    encoding because the filled triangle touches the candidate's outer edge.
    Requiring substantial overlap keeps nearby annotation arrows independent.
    """

    for triangle in filled_triangles:
        triangle_box = triangle["bbox"]
        if _box_overlap_fraction(triangle_box, symbol.bbox) < 0.5:
            continue
        center = triangle["center"]
        if (
            symbol.bbox[0] - 0.5 <= center[0] <= symbol.bbox[2] + 0.5
            and symbol.bbox[1] - 0.5 <= center[1] <= symbol.bbox[3] + 0.5
        ):
            return True
    return False


def _contains_dense_outline_arrow(
    symbol: DesignComponentSymbol, triangles: list[dict[str, Any]]
) -> bool:
    """Detect visually solid arrows exported as nested outline triangles.

    The Chengda design PDF does not use a PDF fill for its main-line flow
    arrow.  Instead it draws several progressively smaller closed triangles
    with a heavy black stroke.  Those nested contours look solid on screen but
    previously entered the composite-valve recognizer as several valves.
    """

    family = []
    for triangle in triangles:
        triangle_box = triangle["bbox"]
        overlap = max(
            _box_overlap_fraction(triangle_box, symbol.bbox),
            _box_overlap_fraction(symbol.bbox, triangle_box),
        )
        if overlap < 0.8 or math.dist(triangle["center"], symbol.center) > 6.0:
            continue
        family.append(float(triangle["area"]))
    if len(family) < 3:
        return False
    # Count genuinely different nesting levels rather than duplicate cycles
    # recovered from coincident CAD strokes.
    levels = []
    for area in sorted(family):
        if not levels or area >= levels[-1] * 1.12:
            levels.append(area)
    return len(levels) >= 3


def _snap(point: tuple[float, float], tolerance: float = 0.9) -> tuple[int, int]:
    return round(point[0] / tolerance), round(point[1] / tolerance)


def _triangle_cycles(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        line for line in lines
        if not line["filled"] and 0.6 <= line["length"] <= 62.0 and line["width"] <= 1.35
    ]
    adjacency: dict[tuple[int, int], set[tuple[int, int]]] = {}
    positions: dict[tuple[int, int], list[tuple[float, float]]] = {}
    edge_lines: dict[tuple[tuple[int, int], tuple[int, int]], list[dict[str, Any]]] = {}
    for line in candidates:
        left, right = _snap(line["start"]), _snap(line["end"])
        if left == right:
            continue
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
        positions.setdefault(left, []).append(line["start"])
        positions.setdefault(right, []).append(line["end"])
        edge_lines.setdefault(tuple(sorted((left, right))), []).append(line)
    result = []
    seen = set()
    for left, neighbours in adjacency.items():
        for right in neighbours:
            for third in neighbours & adjacency.get(right, set()):
                key = tuple(sorted((left, right, third)))
                if key in seen:
                    continue
                seen.add(key)
                vertices = [
                    (
                        sum(point[0] for point in positions[node]) / len(positions[node]),
                        sum(point[1] for point in positions[node]) / len(positions[node]),
                    )
                    for node in key
                ]
                area = abs(
                    (vertices[1][0] - vertices[0][0]) * (vertices[2][1] - vertices[0][1])
                    - (vertices[2][0] - vertices[0][0]) * (vertices[1][1] - vertices[0][1])
                ) / 2.0
                box = _bbox(vertices)
                if area < 0.45 or box[2] - box[0] > 52.0 or box[3] - box[1] > 52.0:
                    continue
                source_lines = []
                for edge in ((left, right), (right, third), (left, third)):
                    source_lines.extend(edge_lines.get(tuple(sorted(edge)), []))
                result.append({
                    "vertices": vertices,
                    "center": tuple(sum(point[axis] for point in vertices) / 3.0 for axis in range(2)),
                    "bbox": box,
                    "area": area,
                    "lines": source_lines,
                    "source_indices": sorted({int(line["drawing_index"]) for line in source_lines}),
                })
    return result


def _filled_triangle_regions(
    page: fitz.Page, drawings: list[dict[str, Any]], lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Recover solid triangles, including implicit close-path PDF encoding."""

    regions = _triangle_cycles([{**line, "filled": False} for line in lines if line["filled"]])
    lines_by_drawing: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        if line["filled"]:
            lines_by_drawing.setdefault(int(line["drawing_index"]), []).append(line)
    for drawing_index, drawing in enumerate(drawings):
        if not _dark(drawing.get("fill")):
            continue
        members = lines_by_drawing.get(drawing_index, [])
        points: dict[tuple[int, int], tuple[float, float]] = {}
        for line in members:
            for point in (line["start"], line["end"]):
                points.setdefault(_snap(point), point)
        # Filled triangles are often stored as two explicit sides and an
        # implicit closePath edge, so three unique vertices are sufficient.
        if len(points) != 3 or len(members) < 2:
            continue
        vertices = list(points.values())
        area = abs(
            sum(
                vertices[index][0] * vertices[(index + 1) % 3][1]
                - vertices[(index + 1) % 3][0] * vertices[index][1]
                for index in range(3)
            )
        ) / 2.0
        if area < 0.08:
            continue
        box = _bbox(vertices)
        if any(math.dist(_center(box), item["center"]) <= 1.0 for item in regions):
            continue
        regions.append({"vertices": vertices, "center": _center(box), "bbox": box, "area": area})
    return regions


def _parallel_pairs(
    lines: list[dict[str, Any]],
    origin: tuple[float, float],
    axis: tuple[float, float],
    *,
    radius: float = 32.0,
) -> list[dict[str, Any]]:
    nearby = []
    for line in lines:
        if line["filled"] or not (2.0 <= line["length"] <= 25.0) or line["width"] > 1.35:
            continue
        midpoint = ((line["start"][0] + line["end"][0]) / 2.0, (line["start"][1] + line["end"][1]) / 2.0)
        local_midpoint = _local(midpoint, origin, axis)
        if math.hypot(*local_midpoint) <= radius:
            nearby.append((line, midpoint, local_midpoint))
    result = []
    for (left, left_midpoint, left_local), (right, right_midpoint, right_local) in combinations(nearby, 2):
        if _angle_difference(_angle(left), _angle(right)) > math.radians(10.0):
            continue
        ratio = max(left["length"], right["length"]) / max(min(left["length"], right["length"]), 1e-6)
        if ratio > 2.1:
            continue
        separation = math.dist(left_midpoint, right_midpoint)
        if not 1.2 <= separation <= 14.0:
            continue
        pair_center = ((left_midpoint[0] + right_midpoint[0]) / 2.0, (left_midpoint[1] + right_midpoint[1]) / 2.0)
        pair_local = _local(pair_center, origin, axis)
        result.append({
            "center": pair_center,
            "local": pair_local,
            "bbox": _bbox((left["start"], left["end"], right["start"], right["end"])),
            "lines": (left, right),
            "source_indices": (int(left["drawing_index"]), int(right["drawing_index"])),
            "separation": separation,
        })
    return result


def _point_in_triangle(point: tuple[float, float], vertices: list[tuple[float, float]], margin: float = 0.0) -> bool:
    def sign(first: tuple[float, float], second: tuple[float, float], third: tuple[float, float]) -> float:
        return (first[0] - third[0]) * (second[1] - third[1]) - (second[0] - third[0]) * (first[1] - third[1])

    values = [
        sign(point, vertices[0], vertices[1]),
        sign(point, vertices[1], vertices[2]),
        sign(point, vertices[2], vertices[0]),
    ]
    return not (any(value < -margin for value in values) and any(value > margin for value in values))


def _is_composite_flange_pair(
    pair: dict[str, Any], origin: tuple[float, float], axis: tuple[float, float]
) -> bool:
    process_angle = math.atan2(axis[1], axis[0]) % math.pi
    midpoints = [
        ((line["start"][0] + line["end"][0]) / 2.0, (line["start"][1] + line["end"][1]) / 2.0)
        for line in pair["lines"]
    ]
    local = [_local(point, origin, axis) for point in midpoints]
    if any(_angle_difference(_angle(line), process_angle) > math.radians(10.0) for line in pair["lines"]):
        return False
    if any(not 3.0 <= line["length"] <= 10.0 for line in pair["lines"]):
        return False
    if local[0][1] * local[1][1] >= 0:
        return False
    if any(not 2.0 <= abs(point[1]) <= 10.0 for point in local):
        return False
    if abs(local[0][0] - local[1][0]) > 8.0 or abs(pair["local"][1]) > 3.0:
        return False
    return 7.0 <= pair["separation"] <= 14.0


def _composite_valves(
    triangles: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
) -> tuple[list[DesignComponentSymbol], set[int]]:
    result = []
    consumed: set[int] = set()
    for triangle in triangles:
        box, area = triangle["bbox"], float(triangle["area"])
        width, height = box[2] - box[0], box[3] - box[1]
        if not (28.0 <= area <= 1200.0 and max(width, height) >= 12.0):
            continue
        axis_info = _axis_at(triangle["center"], process_segments, 16.0)
        if axis_info is None:
            continue
        axis = axis_info["axis"]
        inner_lines = []
        for line in lines:
            if line["filled"] or line["width"] > 1.35 or not 0.7 <= line["length"] <= 12.0:
                continue
            if int(line["drawing_index"]) in set(triangle["source_indices"]):
                continue
            midpoint = ((line["start"][0] + line["end"][0]) / 2.0, (line["start"][1] + line["end"][1]) / 2.0)
            if _point_in_triangle(midpoint, triangle["vertices"], margin=3.0):
                inner_lines.append(line)
        if len(inner_lines) < 2:
            continue
        pairs = [
            pair
            for pair in _parallel_pairs(lines, triangle["center"], axis, radius=max(34.0, max(width, height) * 1.35))
            if _is_composite_flange_pair(pair, triangle["center"], axis)
        ]
        negative = [pair for pair in pairs if pair["local"][0] <= -max(4.0, max(width, height) * 0.20)]
        positive = [pair for pair in pairs if pair["local"][0] >= max(4.0, max(width, height) * 0.20)]
        flange_pairs = []
        if negative:
            flange_pairs.append(("negative", min(negative, key=lambda pair: abs(pair["local"][0]))))
        if positive:
            flange_pairs.append(("positive", min(positive, key=lambda pair: abs(pair["local"][0]))))
        assembly_key = f"design-valve-assembly:{triangle['center'][0]:.2f}:{triangle['center'][1]:.2f}"
        source_indices = set(triangle["source_indices"])
        source_indices.update(int(line["drawing_index"]) for line in inner_lines)
        for _, pair in flange_pairs:
            source_indices.update(pair["source_indices"])
        assembly_bbox = _box_union((triangle["bbox"], *(pair["bbox"] for _, pair in flange_pairs)))
        result.append(DesignComponentSymbol(
            component_type="valve",
            symbol_variant="triangle-arrow-valve",
            center=triangle["center"],
            bbox=assembly_bbox,
            extraction_confidence=0.97,
            evidence=(
                "large-triangle-body",
                "internal-direction-arrow",
                "two-attached-triangle-transitions",
                "on-main-process-axis",
                "flanges-optional-and-independently-recognized",
            ),
            source_drawing_indices=tuple(sorted(source_indices)),
            ep3d_symbol_variant="z-arrow-valve",
            assembly_key=assembly_key,
        ))
        for position, pair in flange_pairs:
            result.append(DesignComponentSymbol(
                component_type="flange",
                symbol_variant="valve-attached-rectangle-flange",
                center=pair["center"],
                bbox=pair["bbox"],
                extraction_confidence=0.96,
                evidence=(
                    "short-parallel-rectangle-sides",
                    f"{position}-side-of-composite-valve",
                    "direct-main-pipe-connection",
                ),
                source_drawing_indices=tuple(sorted(pair["source_indices"])),
                ep3d_symbol_variant="parallel-line-flange",
                assembly_key=assembly_key,
            ))
        consumed.update(source_indices)
    return result, consumed


def _exact_double_triangle_valves(
    triangles: list[dict[str, Any]], process_segments: list[dict[str, Any]]
) -> list[DesignComponentSymbol]:
    result = []
    for left, right in combinations(triangles, 2):
        shared_pairs = [
            (a, b) for a in left["vertices"] for b in right["vertices"]
            if math.dist(a, b) <= 2.0
        ]
        if not shared_pairs:
            continue
        apex_pair = min(shared_pairs, key=lambda pair: math.dist(left["center"], pair[0]) + math.dist(right["center"], pair[1]))
        apex = ((apex_pair[0][0] + apex_pair[1][0]) / 2.0, (apex_pair[0][1] + apex_pair[1][1]) / 2.0)
        left_vector = left["center"][0] - apex[0], left["center"][1] - apex[1]
        right_vector = right["center"][0] - apex[0], right["center"][1] - apex[1]
        if left_vector[0] * right_vector[0] + left_vector[1] * right_vector[1] >= 0:
            continue
        box = _box_union((left["bbox"], right["bbox"]))
        width, height = box[2] - box[0], box[3] - box[1]
        # Random fragments of dimension lines and text can form sub-point
        # triangle cycles.  A physical double-triangle valve must have both a
        # visible axial span and a visible transverse span.
        if max(width, height) > 48.0 or max(width, height) < 6.0 or min(width, height) < 3.0:
            continue
        if _axis_at(apex, process_segments, 9.0) is None:
            continue
        indices = tuple(sorted(set(left["source_indices"]) | set(right["source_indices"])))
        result.append(DesignComponentSymbol(
            component_type="valve",
            symbol_variant="double-triangle-valve",
            center=apex,
            bbox=box,
            extraction_confidence=0.97,
            evidence=("two-triangles-sharing-apex", "on-main-process-axis"),
            source_drawing_indices=indices,
            ep3d_symbol_variant="double-triangle-valve",
        ))
    return _deduplicate(result, distance=8.0)


def _open_double_triangle_valves(
    lines: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
    excluded_boxes: list[tuple[float, float, float, float]],
) -> list[DesignComponentSymbol]:
    seeds = []
    for line in lines:
        if line["filled"] or line["width"] > 1.35 or not 4.0 <= line["length"] <= 36.0:
            continue
        for point in (line["start"], line["end"]):
            axis_info = _axis_at(point, process_segments, 9.0)
            if axis_info is not None:
                seeds.append((axis_info["projected"], axis_info))
    result = []
    for origin, axis_info in seeds:
        if any(box[0] - 38 <= origin[0] <= box[2] + 38 and box[1] - 38 <= origin[1] <= box[3] + 38 for box in excluded_boxes):
            continue
        axis = axis_info["axis"]
        local_lines = []
        for line in lines:
            if line["filled"] or line["width"] > 1.35 or not 4.0 <= line["length"] <= 36.0:
                continue
            midpoint = ((line["start"][0] + line["end"][0]) / 2.0, (line["start"][1] + line["end"][1]) / 2.0)
            local_midpoint = _local(midpoint, origin, axis)
            relative_angle = _angle_difference(_angle(line), math.atan2(axis[1], axis[0]) % math.pi)
            if math.hypot(*local_midpoint) <= 22.0 and math.radians(18) <= relative_angle <= math.radians(82):
                local_lines.append(line)
        if len(local_lines) < 3:
            continue
        node_to_lines: dict[tuple[int, int], set[int]] = {}
        for index, line in enumerate(local_lines):
            for point in (line["start"], line["end"]):
                node_to_lines.setdefault(_snap(point, 1.35), set()).add(index)
        adjacency = [set() for _ in local_lines]
        for indices in node_to_lines.values():
            for left_index in indices:
                adjacency[left_index].update(indices - {left_index})
        remaining = set(range(len(local_lines)))
        components = []
        while remaining:
            seed_index = remaining.pop()
            component = {seed_index}
            queue = [seed_index]
            while queue:
                current = queue.pop()
                attached = adjacency[current] & remaining
                remaining.difference_update(attached)
                component.update(attached)
                queue.extend(attached)
            components.append([local_lines[index] for index in component])
        for component in components:
            if not 3 <= len(component) <= 8:
                continue
            points = [point for line in component for point in (line["start"], line["end"])]
            local_points = [_local(point, origin, axis) for point in points]
            axial_span = max(point[0] for point in local_points) - min(point[0] for point in local_points)
            normal_low, normal_high = min(point[1] for point in local_points), max(point[1] for point in local_points)
            if not 12.0 <= axial_span <= 43.0 or normal_low > -2.5 or normal_high < 2.5:
                continue
            angle_bins = {round(math.degrees(_angle(line)) / 20.0) for line in component}
            if len(angle_bins) < 2:
                continue
            box = _bbox(points)
            if not (box[0] - 3.0 <= origin[0] <= box[2] + 3.0 and box[1] - 3.0 <= origin[1] <= box[3] + 3.0):
                continue
            result.append(DesignComponentSymbol(
                component_type="valve",
                symbol_variant="double-triangle-valve",
                center=origin,
                bbox=box,
                extraction_confidence=0.91,
                evidence=(
                    "occlusion-tolerant-double-triangle-outline",
                    "opposed-normal-extent",
                    "connected-oblique-side-chain",
                    "on-main-process-axis",
                ),
                source_drawing_indices=tuple(sorted({int(line["drawing_index"]) for line in component})),
                ep3d_symbol_variant="double-triangle-valve",
            ))
    return _deduplicate(result, distance=18.0)


def _weld_occluded_double_triangle_valves(
    lines: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
    weld_occluders: list[dict[str, Any]],
    excluded_boxes: list[tuple[float, float, float, float]],
) -> list[DesignComponentSymbol]:
    """Recover valve bodies whose outer vertices are hidden by weld dots."""

    occluders = []
    for item in weld_occluders:
        center_value = item.get("center")
        if not isinstance(center_value, (list, tuple)) or len(center_value) != 2:
            continue
        center = float(center_value[0]), float(center_value[1])
        bbox_value = item.get("bbox")
        bbox = (
            tuple(float(value) for value in bbox_value)
            if isinstance(bbox_value, (list, tuple)) and len(bbox_value) == 4
            else (center[0] - 3.0, center[1] - 3.0, center[0] + 3.0, center[1] + 3.0)
        )
        occluders.append({"center": center, "bbox": bbox, "id": str(item.get("id") or "")})

    result = []
    for first, second in combinations(occluders, 2):
        first_center, second_center = first["center"], second["center"]
        spacing = math.dist(first_center, second_center)
        if not 12.0 <= spacing <= 42.0:
            continue
        center = (
            (first_center[0] + second_center[0]) / 2.0,
            (first_center[1] + second_center[1]) / 2.0,
        )
        if any(
            box[0] - 12.0 <= center[0] <= box[2] + 12.0
            and box[1] - 12.0 <= center[1] <= box[3] + 12.0
            for box in excluded_boxes
        ):
            continue
        pair_axis = (
            (second_center[0] - first_center[0]) / spacing,
            (second_center[1] - first_center[1]) / spacing,
        )
        endpoint_axes = [
            _axis_at(first_center, process_segments, 8.0),
            _axis_at(second_center, process_segments, 8.0),
        ]
        # The centreline is expected to be absent inside a valve.  Validate
        # the pipe direction immediately outside both recognized weld dots
        # instead of requiring a segment through the occluded body centre.
        if any(item is None for item in endpoint_axes):
            continue
        if any(
            abs(pair_axis[0] * item["axis"][0] + pair_axis[1] * item["axis"][1])
            < math.cos(math.radians(12.0))
            for item in endpoint_axes
            if item is not None
        ):
            continue
        # Use the two confirmed weld centres as the local process axis.  The
        # valve outline may stop at either circle boundary, so it need not form
        # closed vector cycles after PDF extraction.
        local_axis = pair_axis
        process_angle = math.atan2(local_axis[1], local_axis[0]) % math.pi
        body_lines = []
        for line in lines:
            if line["filled"] or line["width"] > 1.35 or not 4.0 <= line["length"] <= spacing * 1.45:
                continue
            relative_angle = _angle_difference(_angle(line), process_angle)
            if not math.radians(18.0) <= relative_angle <= math.radians(78.0):
                continue
            midpoint = (
                (line["start"][0] + line["end"][0]) / 2.0,
                (line["start"][1] + line["end"][1]) / 2.0,
            )
            local_midpoint = _local(midpoint, center, local_axis)
            if (
                abs(local_midpoint[0]) <= spacing / 2.0 + 3.5
                and abs(local_midpoint[1]) <= max(12.0, spacing * 0.55)
            ):
                body_lines.append(line)
        if len(body_lines) < 4:
            continue
        local_points = [
            _local(point, center, local_axis)
            for line in body_lines
            for point in (line["start"], line["end"])
        ]
        if (
            min(point[0] for point in local_points) > -spacing * 0.32
            or max(point[0] for point in local_points) < spacing * 0.32
            or min(point[1] for point in local_points) > -4.0
            or max(point[1] for point in local_points) < 4.0
        ):
            continue
        midpoint_endpoints = sum(
            math.dist(point, center) <= 2.8
            for line in body_lines
            for point in (line["start"], line["end"])
        )
        direction_families = {
            1 if (line["end"][0] - line["start"][0]) * (line["end"][1] - line["start"][1]) >= 0 else -1
            for line in body_lines
        }
        if midpoint_endpoints < 1 or len(direction_families) < 2:
            continue
        body_box = _box_union((first["bbox"], second["bbox"], _bbox(
            point for line in body_lines for point in (line["start"], line["end"])
        )))
        result.append(DesignComponentSymbol(
            component_type="valve",
            symbol_variant="double-triangle-valve",
            center=center,
            bbox=body_box,
            extraction_confidence=0.95,
            evidence=(
                "two-recognized-weld-occluders",
                "occlusion-bridged-double-triangle",
                "opposed-oblique-side-families",
                "on-main-process-axis",
            ),
            source_drawing_indices=tuple(sorted({
                int(line["drawing_index"]) for line in body_lines
            })),
            ep3d_symbol_variant="double-triangle-valve",
        ))
    return _deduplicate(result, distance=12.0)


def _support_symbols(
    lines: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
    excluded_boxes: list[tuple[float, float, float, float]],
) -> list[DesignComponentSymbol]:
    result = []
    candidate_lines = [
        line for line in lines
        if not line["filled"] and 6.0 <= line["length"] <= 18.0 and line["width"] <= 1.35
    ]
    grid: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for line in candidate_lines:
        midpoint = ((line["start"][0] + line["end"][0]) / 2.0, (line["start"][1] + line["end"][1]) / 2.0)
        grid.setdefault((round(midpoint[0] / 24.0), round(midpoint[1] / 24.0)), []).append(line)
    seen_pairs = set()
    for cell, members in grid.items():
        nearby = [
            line
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for line in grid.get((cell[0] + dx, cell[1] + dy), [])
        ]
        for left in members:
            for right in nearby:
                pair_key = tuple(sorted((int(left["drawing_index"]), int(right["drawing_index"]))))
                if left is right or pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                if _angle_difference(_angle(left), _angle(right)) > math.radians(8.0):
                    continue
                ratio = max(left["length"], right["length"]) / max(min(left["length"], right["length"]), 1e-6)
                if ratio > 1.8:
                    continue
                left_mid = ((left["start"][0] + left["end"][0]) / 2.0, (left["start"][1] + left["end"][1]) / 2.0)
                right_mid = ((right["start"][0] + right["end"][0]) / 2.0, (right["start"][1] + right["end"][1]) / 2.0)
                separation = math.dist(left_mid, right_mid)
                if not 7.0 <= separation <= 16.0:
                    continue
                center = ((left_mid[0] + right_mid[0]) / 2.0, (left_mid[1] + right_mid[1]) / 2.0)
                if any(box[0] - 18 <= center[0] <= box[2] + 18 and box[1] - 18 <= center[1] <= box[3] + 18 for box in excluded_boxes):
                    continue
                axis_info = _axis_at(center, process_segments, 13.0)
                if axis_info is None:
                    continue
                axis = axis_info["axis"]
                process_angle = math.atan2(axis[1], axis[0]) % math.pi
                if _angle_difference(_angle(left), process_angle) > math.radians(8.0):
                    continue
                left_local = _local(left_mid, axis_info["projected"], axis)
                right_local = _local(right_mid, axis_info["projected"], axis)
                # A design support is drawn as one short stroke on each side
                # of the process centreline.  This rejects elbows, arrows and
                # arbitrary pairs of nearby parallel drafting strokes.
                if left_local[1] * right_local[1] >= 0:
                    continue
                if not (2.5 <= abs(left_local[1]) <= 10.0 and 2.5 <= abs(right_local[1]) <= 10.0):
                    continue
                if abs(left_local[0] - right_local[0]) > 8.0 or abs(axis_info["distance"]) > 2.5:
                    continue
                parallel_family = 0
                for candidate in candidate_lines:
                    candidate_mid = (
                        (candidate["start"][0] + candidate["end"][0]) / 2.0,
                        (candidate["start"][1] + candidate["end"][1]) / 2.0,
                    )
                    if (
                        math.dist(center, candidate_mid) <= 18.0
                        and _angle_difference(_angle(left), _angle(candidate)) <= math.radians(8.0)
                    ):
                        parallel_family += 1
                if parallel_family > 4:
                    continue
                box = _bbox((left["start"], left["end"], right["start"], right["end"]))
                result.append(DesignComponentSymbol(
                    component_type="support",
                    symbol_variant="parallel-line-support",
                    center=center,
                    bbox=box,
                    extraction_confidence=0.9,
                    evidence=("two-short-parallel-lines", "adjacent-to-main-process-axis"),
                    source_drawing_indices=pair_key,
                    ep3d_symbol_variant="parallel-line-support",
                ))
    return _deduplicate(result, distance=10.0)


def _independent_flange_symbols(lines: list[dict[str, Any]]) -> list[DesignComponentSymbol]:
    """Recognize flange line pairs without requiring a neighbouring valve.

    In production CAD PDFs one side is commonly emitted as a normal stroke
    and the other as the edge of a filled process/valve path. That mixed
    encoding is a useful discriminator against dimensions and support marks.
    """
    outline_sides = [
        line for line in lines
        if not line["filled"] and 8.0 <= line["length"] <= 14.5 and line["width"] <= 1.35
    ]
    filled_sides = [
        line for line in lines
        if line["filled"] and 8.0 <= line["length"] <= 14.5
    ]
    result = []
    for outline in outline_sides:
        for filled in filled_sides:
            if _angle_difference(_angle(outline), _angle(filled)) > math.radians(10.0):
                continue
            outline_midpoint = (
                (outline["start"][0] + outline["end"][0]) / 2.0,
                (outline["start"][1] + outline["end"][1]) / 2.0,
            )
            filled_midpoint = (
                (filled["start"][0] + filled["end"][0]) / 2.0,
                (filled["start"][1] + filled["end"][1]) / 2.0,
            )
            separation = _projection(outline_midpoint, filled["start"], filled["end"])[0]
            if not 7.0 <= separation <= 14.0:
                continue
            center = (
                (outline_midpoint[0] + filled_midpoint[0]) / 2.0,
                (outline_midpoint[1] + filled_midpoint[1]) / 2.0,
            )
            result.append(DesignComponentSymbol(
                component_type="flange",
                symbol_variant="independent-parallel-line-flange",
                center=center,
                bbox=_bbox((outline["start"], outline["end"], filled["start"], filled["end"])),
                extraction_confidence=0.95,
                evidence=(
                    "independent-short-parallel-line-pair",
                    "mixed-outline-and-filled-path-encoding",
                    "not-dependent-on-valve-presence",
                ),
                source_drawing_indices=tuple(sorted({
                    int(outline["drawing_index"]), int(filled["drawing_index"]),
                })),
                ep3d_symbol_variant="parallel-line-flange",
            ))
    return _deduplicate(result, distance=7.0)


def _deduplicate(symbols: list[DesignComponentSymbol], *, distance: float) -> list[DesignComponentSymbol]:
    selected = []
    for symbol in sorted(symbols, key=lambda item: item.extraction_confidence, reverse=True):
        if any(
            item.component_type == symbol.component_type
            and item.symbol_variant == symbol.symbol_variant
            and math.dist(item.center, symbol.center) <= distance
            for item in selected
        ):
            continue
        selected.append(symbol)
    return sorted(selected, key=lambda item: (item.center[1], item.center[0], item.component_type))


def _fallback_process_segments(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [
        {"start": list(line["start"]), "end": list(line["end"]), "source": "design-component-fallback"}
        for line in lines
        if not line["filled"] and line["width"] >= 1.45 and line["length"] >= 8.0
    ]
    filled_groups: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        if line["filled"]:
            filled_groups.setdefault(int(line["drawing_index"]), []).append(line)
    for drawing_index, members in filled_groups.items():
        points = [point for line in members for point in (line["start"], line["end"])]
        if len(points) < 4:
            continue
        box = _bbox(points)
        width, height = box[2] - box[0], box[3] - box[1]
        if max(width, height) < 16.0 or max(width, height) / max(min(width, height), 1e-6) < 5.0:
            continue
        longest = max(members, key=lambda line: line["length"])
        dx, dy = longest["end"][0] - longest["start"][0], longest["end"][1] - longest["start"][1]
        length = math.hypot(dx, dy)
        axis = (dx / length, dy / length)
        center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        half = max(width, height) / 2.0
        result.append({
            "start": [center[0] - axis[0] * half, center[1] - axis[1] * half],
            "end": [center[0] + axis[0] * half, center[1] + axis[1] * half],
            "source": "design-filled-band-centerline",
            "drawing_index": drawing_index,
        })
    return result


def extract_design_component_symbols(
    page: fitz.Page, features: dict[str, Any]
) -> list[DesignComponentSymbol]:
    """Recognize unlabelled placement-side valve/flange/support geometry."""

    drawings = page.get_drawings()
    lines = _line_primitives(page, drawings)
    fallback_process_segments = _fallback_process_segments(lines)
    formal_process_segments = list(features.get("strong_process_segments") or [])
    if formal_process_segments:
        # The formal research skeleton filters borders and annotation strokes,
        # while source PDFs can encode the actual process run either as a
        # heavy stroke or a filled band. Component acceptance still requires
        # the stricter straddling/assembly geometry, so both representations
        # are retained here.
        process_segments = formal_process_segments + fallback_process_segments
    else:
        process_segments = fallback_process_segments
    triangles = _triangle_cycles(lines)
    filled_triangles = _filled_triangle_regions(page, drawings, lines)

    composite, consumed = _composite_valves(triangles, lines, process_segments)
    composite_boxes = [symbol.bbox for symbol in composite if symbol.component_type == "valve"]
    exact = [
        symbol for symbol in _exact_double_triangle_valves(triangles, process_segments)
        if not any(
            box[0] - 38 <= symbol.center[0] <= box[2] + 38
            and box[1] - 38 <= symbol.center[1] <= box[3] + 38
            for box in composite_boxes
        )
    ]
    open_valves = _open_double_triangle_valves(lines, process_segments, composite_boxes)
    occluded_valves = _weld_occluded_double_triangle_valves(
        lines,
        process_segments,
        list(features.get("recognized_weld_occluders") or []),
        composite_boxes,
    )
    existing_valves = occluded_valves + exact + open_valves
    valves = _deduplicate(existing_valves, distance=18.0)
    valve_boxes = composite_boxes + [symbol.bbox for symbol in valves]
    supports = _support_symbols(lines, process_segments, valve_boxes)
    try:
        from .ep3d_components import extract_design_support_callouts
    except ImportError:
        from ep3d_components import extract_design_support_callouts
    labelled_supports = [
        DesignComponentSymbol(
            component_type="support",
            symbol_variant=callout.symbol_variant,
            center=callout.component_point,
            bbox=callout.label_bbox,
            extraction_confidence=callout.extraction_confidence,
            evidence=callout.evidence,
            source_drawing_indices=(),
            ep3d_symbol_variant="parallel-line-support",
        )
        for callout in extract_design_support_callouts(page)
    ]
    supports = labelled_supports + [
        support for support in supports
        if not any(math.dist(support.center, labelled.center) <= 12.0 for labelled in labelled_supports)
    ]
    independent_flanges = _independent_flange_symbols(lines)
    existing_flange_centers = [symbol.center for symbol in composite if symbol.component_type == "flange"]
    independent_flanges = [
        symbol for symbol in independent_flanges
        if not any(math.dist(symbol.center, center) <= 7.0 for center in existing_flange_centers)
    ]
    symbols = composite + valves + supports + independent_flanges
    symbols = [
        symbol for symbol in symbols
        if symbol.component_type != "valve" or not (
            "two-recognized-weld-occluders" not in symbol.evidence
            and (
                _contains_solid_arrow(symbol, filled_triangles)
                or _contains_dense_outline_arrow(symbol, triangles)
            )
        )
    ]
    return sorted(symbols, key=lambda item: (item.center[1], item.center[0], item.component_type))
