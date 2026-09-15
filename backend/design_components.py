"""Component recognition for unlabelled design PDFs.

This module is intentionally placement-mode only. Valves are resolved from
the ERECTION MATERIALS table and matching boxed-number leaders; flanges and
supports retain their geometry-based rules. It never contributes weld
candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
import re
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
            if not item:
                continue
            if item[0] == "l":
                segments = [(item[1], item[2])]
            elif item[0] == "re":
                rect = item[1]
                segments = [
                    (rect.tl, rect.tr), (rect.tr, rect.br),
                    (rect.br, rect.bl), (rect.bl, rect.tl),
                ]
            elif item[0] == "qu":
                quad = item[1]
                segments = [
                    (quad.ul, quad.ur), (quad.ur, quad.lr),
                    (quad.lr, quad.ll), (quad.ll, quad.ul),
                ]
            else:
                continue
            for segment_index, (raw_start, raw_end) in enumerate(segments):
                start, end = _display_point(page, raw_start), _display_point(page, raw_end)
                length = math.dist(start, end)
                if length <= 0.15:
                    continue
                lines.append({
                    "drawing_index": drawing_index,
                    "item_index": item_index,
                    "segment_index": segment_index,
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


def _valve_material_numbers(page: fitz.Page) -> dict[str, str]:
    """Map material item numbers to supported valve table types."""

    words = list(page.get_text("words"))
    table_left = float(page.rect.width) * 0.55
    result: dict[str, str] = {}
    for keyword in words:
        value = str(keyword[4]).upper()
        valve_type = (
            "diaphragm" if "DIAPHRAGM" in value
            else "ball" if "BALL" in value
            else "gate" if value == "GATE"
            else "lift-check" if value == "LIFT"
            else None
        )
        if valve_type is None or float(keyword[0]) < table_left:
            continue
        keyword_y = (float(keyword[1]) + float(keyword[3])) / 2.0
        if value == "LIFT" and not any(
            str(word[4]).upper() == "CHECK"
            and abs((float(word[1]) + float(word[3])) / 2.0 - keyword_y) <= 7.0
            and 0.0 <= float(word[0]) - float(keyword[2]) <= 45.0
            for word in words
        ):
            continue
        row_numbers = [
            word for word in words
            if re.fullmatch(r"\d{1,3}", str(word[4]))
            and table_left <= float(word[0]) < float(keyword[0])
            and float(keyword[0]) - float(word[2]) <= 90.0
            and abs((float(word[1]) + float(word[3])) / 2.0 - keyword_y) <= 7.0
        ]
        if row_numbers:
            number = str(max(row_numbers, key=lambda word: float(word[0]))[4])
            result[number] = valve_type
    return result


def _point_box_distance(
    point: tuple[float, float], box: tuple[float, float, float, float]
) -> float:
    return math.hypot(
        max(box[0] - point[0], 0.0, point[0] - box[2]),
        max(box[1] - point[1], 0.0, point[1] - box[3]),
    )


def _directed_cosine(
    start: tuple[float, float],
    end: tuple[float, float],
    next_start: tuple[float, float],
    next_end: tuple[float, float],
) -> float:
    left = end[0] - start[0], end[1] - start[1]
    right = next_end[0] - next_start[0], next_end[1] - next_start[1]
    denominator = math.hypot(*left) * math.hypot(*right)
    return -1.0 if denominator <= 1e-9 else (left[0] * right[0] + left[1] * right[1]) / denominator


def _trace_material_leader(
    first: dict[str, Any],
    near: tuple[float, float],
    far: tuple[float, float],
    eligible: list[dict[str, Any]],
    arrows: list[dict[str, Any]],
) -> tuple[tuple[float, float], tuple[dict[str, Any], ...], dict[str, Any], float] | None:
    """Follow a short, nearly continuous leader stream to a filled arrow."""

    def arrow_at(point: tuple[float, float]) -> dict[str, Any] | None:
        return next((
            triangle for triangle in arrows
            if min(math.dist(point, vertex) for vertex in triangle["vertices"]) <= 2.5
        ), None)

    def visit(
        path: tuple[dict[str, Any], ...],
        previous: tuple[float, float],
        current: tuple[float, float],
        total_gap: float,
    ) -> tuple[tuple[float, float], tuple[dict[str, Any], ...], dict[str, Any], float] | None:
        arrow = arrow_at(current)
        if arrow is not None:
            return current, path, arrow, total_gap
        if len(path) >= 3:
            return None

        continuations = []
        used = {id(line) for line in path}
        for line in eligible:
            if id(line) in used:
                continue
            for attach, other in ((line["start"], line["end"]), (line["end"], line["start"])):
                gap = math.dist(current, attach)
                if gap > 4.0:
                    continue
                # Interrupted leader strokes preserve their direction. This
                # rejects crossing dimensions and nearby annotation frames.
                cosine = _directed_cosine(previous, current, attach, other)
                if cosine < math.cos(math.radians(50.0)):
                    continue
                continuations.append((gap, -cosine, -float(line["length"]), line, attach, other))
        for gap, _, _, line, attach, other in sorted(continuations, key=lambda item: item[:3]):
            traced = visit(path + (line,), attach, other, total_gap + gap)
            if traced is not None:
                return traced
        return None

    return visit((first,), near, far, 0.0)


def _stem_direction_text_bridge(
    frame_box: tuple[float, float, float, float],
    words: list[tuple[Any, ...]],
) -> tuple[tuple[float, float, float, float], str] | None:
    """Bridge a number frame to a leader through ``STEM <direction>`` text."""

    directions = {"UP", "DOWN", "NORTH", "SOUTH", "EAST", "WEST", "N", "S", "E", "W"}
    for stem in words:
        if str(stem[4]).strip().upper() != "STEM":
            continue
        stem_box = tuple(float(value) for value in stem[:4])
        if _point_box_distance(_center(stem_box), frame_box) > 22.0:
            continue
        stem_y = (stem_box[1] + stem_box[3]) / 2.0
        direction = next((
            word for word in words
            if str(word[4]).strip().upper() in directions
            and abs((float(word[1]) + float(word[3])) / 2.0 - stem_y) <= 5.0
            and -2.0 <= float(word[0]) - stem_box[2] <= 12.0
        ), None)
        if direction is None:
            continue
        bridge_box = (
            min(stem_box[0], float(direction[0])),
            min(stem_box[1], float(direction[1])),
            max(stem_box[2], float(direction[2])),
            max(stem_box[3], float(direction[3])),
        )
        phrase = f"STEM {str(direction[4]).strip().upper()}"
        return bridge_box, phrase
    return None


def _material_valve_callouts(
    page: fitz.Page, lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve boxed valve-material numbers to their arrow endpoints."""

    valve_numbers = _valve_material_numbers(page)
    if not valve_numbers:
        return []
    words = list(page.get_text("words"))
    numeric_words = []
    for word in words:
        label = str(word[4]).strip()
        if label not in valve_numbers:
            continue
        box = tuple(float(value) for value in word[:4])
        numeric_words.append((label, box, _center(box)))

    frames = _rectangle_cycles(lines)
    filled_triangles = _triangle_cycles([
        {**line, "filled": False} for line in lines if line["filled"]
    ])
    results = []
    for label, _, text_center in numeric_words:
        enclosing = [
            frame for frame in frames
            if frame["bbox"][0] - 1.5 <= text_center[0] <= frame["bbox"][2] + 1.5
            and frame["bbox"][1] - 1.5 <= text_center[1] <= frame["bbox"][3] + 1.5
        ]
        if not enclosing:
            continue
        frame = min(enclosing, key=lambda item: math.dist(item["center"], text_center))
        frame_indices = set(frame["source_indices"])
        text_bridge = _stem_direction_text_bridge(frame["bbox"], words)
        eligible = [
            line for line in lines
            if not line["filled"]
            and line["width"] <= 1.35
            and int(line["drawing_index"]) not in frame_indices
            and 4.0 <= line["length"] <= 160.0
        ]
        leaders = []
        for line in eligible:
            attachment_boxes = [(frame["bbox"], 24.0, None)]
            if text_bridge is not None:
                attachment_boxes.append((text_bridge[0], 8.0, text_bridge[1]))
            attachments = []
            for attachment_box, maximum_gap, phrase in attachment_boxes:
                start_gap = _point_box_distance(line["start"], attachment_box)
                end_gap = _point_box_distance(line["end"], attachment_box)
                gap = min(start_gap, end_gap)
                if gap <= maximum_gap:
                    attachments.append((gap, attachment_box, phrase, start_gap, end_gap))
            if not attachments:
                continue
            near_distance, attachment_box, phrase, start_distance, end_distance = min(
                attachments, key=lambda item: item[0]
            )
            near_distance = min(start_distance, end_distance)
            near = line["start"] if start_distance <= end_distance else line["end"]
            far = line["end"] if start_distance <= end_distance else line["start"]
            if _point_box_distance(far, attachment_box) <= near_distance + 4.0:
                continue
            traced = _trace_material_leader(line, near, far, eligible, filled_triangles)
            if traced is None:
                continue
            target, path, arrow, total_gap = traced
            total_length = sum(float(item["length"]) for item in path)
            leaders.append((near_distance, total_gap, -total_length, target, path, arrow, phrase))
        if not leaders:
            continue
        _, _, _, target, leader_path, arrow, phrase = min(leaders, key=lambda item: item[:3])
        results.append({
            "label": label,
            "valve_type": valve_numbers[label],
            "target": target,
            "frame_bbox": frame["bbox"],
            "text_bridge": phrase,
            "source_indices": tuple(sorted(
                frame_indices
                | {int(line["drawing_index"]) for line in leader_path}
                | set(arrow["source_indices"])
            )),
        })
    return results


def _material_callout_valves(
    callouts: list[dict[str, Any]],
) -> list[DesignComponentSymbol]:
    """Create valves solely from supported material rows and boxed callouts."""

    result = []
    for callout in callouts:
        target = tuple(float(value) for value in callout["target"])
        valve_type = str(callout["valve_type"])
        result.append(DesignComponentSymbol(
            component_type="valve",
            symbol_variant=f"{valve_type}-material-callout-valve",
            center=target,
            bbox=(target[0] - 6.0, target[1] - 6.0, target[0] + 6.0, target[1] + 6.0),
            extraction_confidence=0.99,
            evidence=(
                f"valve-material-number:{callout['label']}",
                f"material-table-valve-type:{valve_type}",
                "boxed-material-number",
                "leader-arrow-authoritative-endpoint",
                *(
                    (f"frame-text-leader-bridge:{callout['text_bridge']}",)
                    if callout.get("text_bridge") else ()
                ),
            ),
            source_drawing_indices=callout["source_indices"],
            ep3d_symbol_variant="material-callout-valve",
        ))
    return _deduplicate(result, distance=8.0)


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


def _rectangle_cycles(
    lines: list[dict[str, Any]],
    *,
    maximum_side: float = 20.0,
    maximum_extent: float = 20.0,
) -> list[dict[str, Any]]:
    """Return closed four-edge rectangles made from outline strokes."""

    candidates = [
        line for line in lines
        if not line["filled"]
        and 2.0 <= line["length"] <= maximum_side
        and line["width"] <= 1.35
    ]
    adjacency: dict[tuple[int, int], set[tuple[int, int]]] = {}
    positions: dict[tuple[int, int], list[tuple[float, float]]] = {}
    edge_lines: dict[tuple[tuple[int, int], tuple[int, int]], list[dict[str, Any]]] = {}
    for line in candidates:
        start, end = _snap(line["start"]), _snap(line["end"])
        if start == end:
            continue
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)
        positions.setdefault(start, []).append(line["start"])
        positions.setdefault(end, []).append(line["end"])
        edge_lines.setdefault(tuple(sorted((start, end))), []).append(line)

    raw_cycles: list[tuple[tuple[int, int], ...]] = []
    seen: set[frozenset[tuple[int, int]]] = set()

    def walk(
        start: tuple[int, int],
        current: tuple[int, int],
        path: tuple[tuple[int, int], ...],
    ) -> None:
        for neighbour in adjacency.get(current, set()):
            if neighbour == start and 4 <= len(path) <= 5:
                key = frozenset(path)
                if key not in seen:
                    seen.add(key)
                    raw_cycles.append(path)
                continue
            if neighbour in path or len(path) >= 5:
                continue
            walk(start, neighbour, path + (neighbour,))

    for node in adjacency:
        walk(node, node, (node,))

    result = []
    for nodes in raw_cycles:
        vertices = [
            (
                sum(point[0] for point in positions[node]) / len(positions[node]),
                sum(point[1] for point in positions[node]) / len(positions[node]),
            )
            for node in nodes
        ]
        source_lines = [
            min(
                edge_lines[tuple(sorted((nodes[index], nodes[(index + 1) % len(nodes)])))],
                key=lambda line: line["length"],
            )
            for index in range(len(nodes))
        ]

        # CAD export may split one rectangle side where it meets the pipe or
        # an overlaid weld dot.  Collapse that collinear intermediate vertex;
        # this still requires a genuinely closed outline and cannot turn two
        # open square brackets into a flange.
        while len(vertices) > 4:
            removable = next((
                index for index in range(len(vertices))
                if _angle_difference(
                    math.atan2(
                        vertices[index][1] - vertices[index - 1][1],
                        vertices[index][0] - vertices[index - 1][0],
                    ) % math.pi,
                    math.atan2(
                        vertices[(index + 1) % len(vertices)][1] - vertices[index][1],
                        vertices[(index + 1) % len(vertices)][0] - vertices[index][0],
                    ) % math.pi,
                ) <= math.radians(8.0)
            ), None)
            if removable is None:
                break
            vertices.pop(removable)
        if len(vertices) != 4:
            continue

        edge_vectors = [
            (
                vertices[(index + 1) % 4][0] - vertices[index][0],
                vertices[(index + 1) % 4][1] - vertices[index][1],
            )
            for index in range(4)
        ]
        lengths = [math.hypot(*vector) for vector in edge_vectors]
        angles = [math.atan2(vector[1], vector[0]) % math.pi for vector in edge_vectors]
        if min(lengths) < 2.0:
            continue
        if _angle_difference(angles[0], angles[2]) > math.radians(8.0):
            continue
        if _angle_difference(angles[1], angles[3]) > math.radians(8.0):
            continue
        corner_angle = _angle_difference(angles[0], angles[1])
        # A CAD rectangle is displayed as a parallelogram after isometric
        # projection, so screen-space corners are commonly about 60/120°.
        if not math.radians(45.0) <= corner_angle <= math.radians(135.0):
            continue
        if max(lengths[0], lengths[2]) / min(lengths[0], lengths[2]) > 1.3:
            continue
        if max(lengths[1], lengths[3]) / min(lengths[1], lengths[3]) > 1.3:
            continue
        box = _bbox(vertices)
        if not (
            4.0 <= box[2] - box[0] <= maximum_extent
            and 4.0 <= box[3] - box[1] <= maximum_extent
        ):
            continue
        result.append({
            "center": tuple(sum(point[axis] for point in vertices) / 4.0 for axis in range(2)),
            "bbox": box,
            "lines": source_lines,
            "edge_lengths": tuple(lengths),
            "source_indices": tuple(sorted({int(line["drawing_index"]) for line in source_lines})),
        })
    return result


def _rectangle_contains_native_text(
    rectangle: dict[str, Any], words: list[tuple[Any, ...]], *, padding: float = 1.0
) -> bool:
    """Reject material-number and dimension frames before route recovery."""

    x0, y0, x1, y1 = rectangle["bbox"]
    return any(
        x0 - padding <= (float(word[0]) + float(word[2])) / 2.0 <= x1 + padding
        and y0 - padding <= (float(word[1]) + float(word[3])) / 2.0 <= y1 + padding
        for word in words
    )


def _rectangle_contains_recognizable_number(
    rectangle: dict[str, Any], words: list[tuple[Any, ...]], *, padding: float = 1.0
) -> bool:
    """Identify numbered annotation frames that must never be shape flanges."""

    x0, y0, x1, y1 = rectangle["bbox"]
    return any(
        re.search(r"\d", str(word[4]))
        and x0 - padding <= (float(word[0]) + float(word[2])) / 2.0 <= x1 + padding
        and y0 - padding <= (float(word[1]) + float(word[3])) / 2.0 <= y1 + padding
        for word in words
    )


def _rectangle_has_flange_aspect_ratio(rectangle: dict[str, Any]) -> bool:
    """Require the geometric flange's long edge to be at least 2x its short edge."""

    lengths = [float(value) for value in rectangle.get("edge_lengths") or () if float(value) > 1e-6]
    return bool(lengths and max(lengths) / min(lengths) >= 2.0)


def _boxed_f_flange_callouts(
    page: fitz.Page, lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve ``F<n> ...`` assembly frames to their flange arrow endpoints."""

    words = list(page.get_text("words"))
    frames = [
        frame for frame in _rectangle_cycles(
            lines, maximum_side=120.0, maximum_extent=120.0
        )
        if _inside_component_drawing_region(page, frame)
        and 4.0 <= frame["bbox"][3] - frame["bbox"][1] <= 30.0
        and 10.0 <= frame["bbox"][2] - frame["bbox"][0] <= 120.0
    ]
    filled_triangles = _triangle_cycles([
        {**line, "filled": False} for line in lines if line["filled"]
    ])
    results = []
    used_frames: set[tuple[int, ...]] = set()
    for word in words:
        label = str(word[4]).strip().upper()
        # Project flange item numbers are one or two digits.  Keeping this
        # bounded explicitly excludes material grades such as F304.
        if not re.fullmatch(r"F\d{1,2}", label):
            continue
        text_center = _center(tuple(float(value) for value in word[:4]))
        enclosing = [
            frame for frame in frames
            if frame["bbox"][0] - 1.5 <= text_center[0] <= frame["bbox"][2] + 1.5
            and frame["bbox"][1] - 1.5 <= text_center[1] <= frame["bbox"][3] + 1.5
        ]
        if not enclosing:
            continue
        frame = min(
            enclosing,
            key=lambda item: (
                (item["bbox"][2] - item["bbox"][0]) * (item["bbox"][3] - item["bbox"][1]),
                math.dist(item["center"], text_center),
            ),
        )
        frame_key = tuple(int(value) for value in frame["source_indices"])
        if frame_key in used_frames:
            continue
        frame_indices = set(frame["source_indices"])
        eligible = [
            line for line in lines
            if not line["filled"]
            and line["width"] <= 1.35
            and int(line["drawing_index"]) not in frame_indices
            and 4.0 <= line["length"] <= 200.0
        ]
        leaders = []
        for line in eligible:
            start_distance = _point_box_distance(line["start"], frame["bbox"])
            end_distance = _point_box_distance(line["end"], frame["bbox"])
            near_distance = min(start_distance, end_distance)
            if near_distance > 15.0:
                continue
            near = line["start"] if start_distance <= end_distance else line["end"]
            far = line["end"] if start_distance <= end_distance else line["start"]
            if _point_box_distance(far, frame["bbox"]) <= near_distance + 4.0:
                continue
            traced = _trace_material_leader(line, near, far, eligible, filled_triangles)
            if traced is None:
                continue
            target, path, arrow, total_gap = traced
            total_length = sum(float(item["length"]) for item in path)
            leaders.append((near_distance, total_gap, -total_length, target, path, arrow))
        if not leaders:
            continue
        _, _, _, target, leader_path, arrow = min(leaders, key=lambda item: item[:3])
        frame_text = " ".join(
            str(item[4]).strip()
            for item in sorted(words, key=lambda item: (float(item[1]), float(item[0])))
            if frame["bbox"][0] - 1.0 <= (float(item[0]) + float(item[2])) / 2.0 <= frame["bbox"][2] + 1.0
            and frame["bbox"][1] - 1.0 <= (float(item[1]) + float(item[3])) / 2.0 <= frame["bbox"][3] + 1.0
        )
        results.append({
            "label": label,
            "frame_text": frame_text,
            "target": target,
            "frame_bbox": frame["bbox"],
            "source_indices": tuple(sorted(
                frame_indices
                | {int(line["drawing_index"]) for line in leader_path}
                | set(arrow["source_indices"])
            )),
        })
        used_frames.add(frame_key)
    return results


def _inside_component_drawing_region(
    page: fitz.Page, rectangle: dict[str, Any]
) -> bool:
    """Keep component geometry out of the right/bottom tables and page frame."""

    x0, y0, x1, y1 = rectangle["bbox"]
    width, height = float(page.rect.width), float(page.rect.height)
    return (
        x0 >= width * 0.025
        and y0 >= height * 0.015
        and x1 <= width * 0.70
        and y1 <= height * 0.82
    )


def _eligible_flange_rectangles(
    page: fitz.Page, lines: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    words = list(page.get_text("words"))
    return [
        rectangle
        for rectangle in _rectangle_cycles(lines)
        if _inside_component_drawing_region(page, rectangle)
        and _rectangle_has_flange_aspect_ratio(rectangle)
        and not _rectangle_contains_recognizable_number(rectangle, words)
        and not _rectangle_contains_native_text(rectangle, words)
    ]


def _reconstructed_component_process_segments(
    page: fitz.Page,
    all_lines: list[dict[str, Any]],
    base_segments: list[dict[str, Any]],
    rectangles: list[dict[str, Any]],
    valves: list[DesignComponentSymbol],
) -> list[dict[str, Any]]:
    """Supplement the heavy-stroke skeleton with constrained thin pipe runs.

    DWG printers use 0.72--0.96 pt strokes inside valve/flange assemblies even
    when the adjoining process run is 1.68--2.64 pt. Importing every thin
    stroke would also import dimensions and leaders, so a thin run is retained
    only when it is long and passes through a text-free component rectangle or
    an authoritative material-table valve endpoint.
    """

    anchors = [
        {
            "center": tuple(float(value) for value in rectangle["center"]),
            "source_indices": set(int(value) for value in rectangle["source_indices"]),
            "kind": "flange-rectangle",
        }
        for rectangle in rectangles
    ] + [
        {
            "center": tuple(float(value) for value in valve.center),
            "source_indices": set(int(value) for value in valve.source_drawing_indices),
            "kind": "material-valve",
        }
        for valve in valves
    ]
    if not anchors:
        return list(base_segments)

    width, height = float(page.rect.width), float(page.rect.height)
    supplemental: list[dict[str, Any]] = []
    seen = {
        (
            round(float(segment["start"][0]), 3),
            round(float(segment["start"][1]), 3),
            round(float(segment["end"][0]), 3),
            round(float(segment["end"][1]), 3),
        )
        for segment in base_segments
    }
    for line in all_lines:
        midpoint = (
            (float(line["start"][0]) + float(line["end"][0])) / 2.0,
            (float(line["start"][1]) + float(line["end"][1])) / 2.0,
        )
        if (
            line["filled"]
            or float(line["width"]) < 0.60
            or float(line["length"]) < 24.0
            or midpoint[0] > width * 0.70
            or midpoint[1] > height * 0.82
        ):
            continue
        supporting_anchor = next((
            anchor for anchor in anchors
            if int(line["drawing_index"]) not in anchor["source_indices"]
            and _projection(
                anchor["center"], tuple(line["start"]), tuple(line["end"])
            )[0] <= 3.0
        ), None)
        if supporting_anchor is None:
            continue
        key = (
            round(float(line["start"][0]), 3),
            round(float(line["start"][1]), 3),
            round(float(line["end"][0]), 3),
            round(float(line["end"][1]), 3),
        )
        if key in seen:
            continue
        seen.add(key)
        supplemental.append({
            "start": list(line["start"]),
            "end": list(line["end"]),
            "source": f"component-anchored-thin-route:{supporting_anchor['kind']}",
            "drawing_index": int(line["drawing_index"]),
            "confidence": 0.90,
        })
    return list(base_segments) + supplemental


def _rectangular_flange_symbols(
    page: fitz.Page,
    lines: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
) -> list[DesignComponentSymbol]:
    """Recognize text-free closed flange rectangles in a process corridor."""

    result: list[DesignComponentSymbol] = []
    for rectangle in _eligible_flange_rectangles(page, lines):
        center = rectangle["center"]
        axis_info = _axis_at(center, process_segments, 2.5)
        if axis_info is None:
            # Filled bands and double-line CAD pipes place their recovered
            # centreline several points beside the visible flange rectangle.
            # A narrow corridor is safe here because text-bearing frames and
            # table/title regions were already removed above.
            axis_info = _axis_at(center, process_segments, 15.0)
            if axis_info is None:
                continue
            confidence = 0.93
            route_evidence = "center-in-reconstructed-process-corridor"
        else:
            confidence = 0.97
            route_evidence = "center-on-main-process-line"
        result.append(DesignComponentSymbol(
            component_type="flange",
            symbol_variant="closed-rectangle-flange",
            center=center,
            bbox=rectangle["bbox"],
            extraction_confidence=confidence,
            evidence=(
                "closed-four-sided-rectangle",
                route_evidence,
                "pymupdf-vector-path",
            ),
            source_drawing_indices=rectangle["source_indices"],
            ep3d_symbol_variant="rectangle-flange",
        ))
    return _deduplicate(result, distance=7.0)


def _boxed_f_flange_symbols(
    callouts: list[dict[str, Any]],
) -> list[DesignComponentSymbol]:
    result = []
    for callout in callouts:
        target = tuple(float(value) for value in callout["target"])
        result.append(DesignComponentSymbol(
            component_type="flange",
            symbol_variant="boxed-f-material-arrow-flange",
            center=target,
            bbox=(target[0] - 6.0, target[1] - 6.0, target[0] + 6.0, target[1] + 6.0),
            extraction_confidence=0.99,
            evidence=(
                f"boxed-flange-material-label:{callout['label']}",
                f"boxed-assembly-text:{callout['frame_text']}",
                "attached-leader-filled-arrow",
                "leader-arrow-authoritative-endpoint",
            ),
            source_drawing_indices=callout["source_indices"],
            ep3d_symbol_variant="rectangle-flange",
        ))
    return _deduplicate(result, distance=8.0)


def _merge_flange_sources(
    labelled: list[DesignComponentSymbol],
    geometric: list[DesignComponentSymbol],
) -> list[DesignComponentSymbol]:
    """Prefer an F-callout when its arrow and a shape identify one flange."""

    remaining = [
        flange for flange in geometric
        if not any(
            math.dist(flange.center, item.center) <= 12.0
            or _point_box_distance(item.center, flange.bbox) <= 6.0
            for item in labelled
        )
    ]
    return sorted(labelled + remaining, key=lambda item: (item.center[1], item.center[0]))


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


def _merge_support_sources(
    labelled: list[DesignComponentSymbol],
    geometric: list[DesignComponentSymbol],
) -> list[DesignComponentSymbol]:
    """Prefer an S-callout when its arrow lands beside the same support glyph.

    A leader endpoint need not be the centre of the two-line support symbol.
    In the 300900 drawing the endpoint is about 18 PDF units from the glyph
    centre, outside the old 12-unit centre-only merge radius, while remaining
    only 7 units from its bounding box.  Bounding-box proximity is therefore
    used only as a constrained cross-source supplement; unrelated nearby
    supports still require both a short centre distance and a near landing.
    """

    remaining = []
    for support in geometric:
        duplicate = any(
            math.dist(support.center, item.center) <= 12.0
            or (
                math.dist(support.center, item.center) <= 24.0
                and _point_box_distance(item.center, support.bbox) <= 8.0
            )
            for item in labelled
        )
        if not duplicate:
            remaining.append(support)
    return labelled + remaining


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
    all_lines = _line_primitives(page, drawings)
    excluded_weld_indices = {
        int(index)
        for occluder in list(features.get("recognized_weld_occluders") or [])
        for key in ("source_drawing_indices", "modifier_drawing_indices")
        for index in list(occluder.get(key) or [])
    }
    # Weld recognition owns its circle, X and bracket drawings. Exclude those
    # primitives before associating material callout leaders or other symbols.
    lines = [
        line for line in all_lines
        if int(line["drawing_index"]) not in excluded_weld_indices
    ]
    fallback_process_segments = _fallback_process_segments(all_lines)
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
    material_callouts = _material_valve_callouts(page, lines)
    # Design-side valve identity and position now come only from the material
    # table plus the matching boxed material-number leader on the drawing.
    valves = _material_callout_valves(material_callouts)
    boxed_f_flanges = _boxed_f_flange_symbols(_boxed_f_flange_callouts(page, lines))
    flange_rectangles = _eligible_flange_rectangles(page, lines)
    process_segments = _reconstructed_component_process_segments(
        page,
        all_lines,
        process_segments,
        flange_rectangles,
        valves + boxed_f_flanges,
    )
    valve_boxes = [symbol.bbox for symbol in valves]
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
    supports = _merge_support_sources(labelled_supports, supports)
    flanges = _merge_flange_sources(
        boxed_f_flanges,
        _rectangular_flange_symbols(page, lines, process_segments),
    )
    symbols = valves + supports + flanges
    return sorted(symbols, key=lambda item: (item.center[1], item.center[0], item.component_type))
