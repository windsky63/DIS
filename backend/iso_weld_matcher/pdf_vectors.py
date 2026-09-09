"""Extract traceable, rotation-normalized features from vector ISO PDFs."""

from __future__ import annotations

import html
import itertools
import math
from pathlib import Path
from typing import Any

import fitz

from .geometry_scale import estimate_vector_scale


DEFAULT_ROI = (0.06, 0.05, 0.78, 0.78)
DIRECTION_COLORS = (
    "#d73027",
    "#fc8d59",
    "#fee08b",
    "#91cf60",
    "#1a9850",
    "#4575b4",
    "#7b3294",
    "#c51b7d",
)


def _display_point(page: fitz.Page, point: fitz.Point) -> tuple[float, float]:
    mapped = point * page.rotation_matrix
    return float(mapped.x), float(mapped.y)


def _line_angle(start: tuple[float, float], end: tuple[float, float]) -> float:
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 180.0


def _inside_roi(midpoint: tuple[float, float], width: float, height: float, roi: tuple[float, float, float, float]) -> bool:
    return (
        roi[0] * width <= midpoint[0] <= roi[2] * width
        and roi[1] * height <= midpoint[1] <= roi[3] * height
    )


def _continuation_terminal_candidates(
    page: fitz.Page,
    segments: list[dict[str, Any]],
    width: float,
    height: float,
    distance_scale: float,
) -> list[dict[str, Any]]:
    """Find process junctions next to explicit CONN/CONT continuation text."""

    labels = []
    for word in page.get_text("words"):
        token = str(word[4]).strip().upper().rstrip(".:")
        if token not in {"CONN", "CONT", "CONNECTION", "CONTINUED"}:
            continue
        box = _display_bbox(page, fitz.Rect(*word[:4]))
        labels.append(((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0))
    if not labels:
        return []

    endpoints: list[tuple[tuple[float, float], int]] = []
    for segment_index, segment in enumerate(segments):
        if float(segment.get("length", 0.0)) < 10.0 * distance_scale:
            continue
        endpoints.extend(
            [
                (tuple(segment["start"]), segment_index),
                (tuple(segment["end"]), segment_index),
            ]
        )
    clusters: list[dict[str, Any]] = []
    remaining = set(range(len(endpoints)))
    snap = 2.6 * distance_scale
    while remaining:
        seed = remaining.pop()
        members = [seed]
        queue = [seed]
        while queue:
            current = queue.pop()
            attached = [index for index in remaining if math.dist(endpoints[current][0], endpoints[index][0]) <= snap]
            for index in attached:
                remaining.remove(index)
                members.append(index)
                queue.append(index)
        segment_ids = {endpoints[index][1] for index in members}
        if len(segment_ids) < 2:
            continue
        center = tuple(sum(endpoints[index][0][axis] for index in members) / len(members) for axis in range(2))
        if not _inside_roi(center, width, height, DEFAULT_ROI):
            continue
        clusters.append({"center": center, "segment_ids": segment_ids})

    result = []
    for label in labels:
        ranked = sorted(
            (
                math.dist(label, cluster["center"])
                - 20.0 * distance_scale * min(2, len(cluster["segment_ids"]) - 2),
                math.dist(label, cluster["center"]),
                cluster,
            )
            for cluster in clusters
        )
        if not ranked or ranked[0][1] > 150.0 * distance_scale:
            continue
        _, distance, cluster = ranked[0]
        point = [round(value, 3) for value in cluster["center"]]
        result.append(
            {
                "center": point,
                "process_coordinate": point,
                "component_kind": "continuation-terminal",
                "confidence": round(max(0.78, 0.96 - distance / (600.0 * distance_scale)), 3),
                "evidence": "continuation-text-near-multiline-process-terminal",
                "continuation_label_coordinate": [round(value, 3) for value in label],
                "incident_segment_count": len(cluster["segment_ids"]),
            }
        )
    deduplicated = []
    for candidate in result:
        if not any(math.dist(candidate["center"], existing["center"]) <= 4.0 * distance_scale for existing in deduplicated):
            deduplicated.append(candidate)
    return deduplicated


def _display_bbox(page: fitz.Page, rect: fitz.Rect) -> tuple[float, float, float, float]:
    corners = [
        _display_point(page, fitz.Point(rect.x0, rect.y0)),
        _display_point(page, fitz.Point(rect.x1, rect.y0)),
        _display_point(page, fitz.Point(rect.x0, rect.y1)),
        _display_point(page, fitz.Point(rect.x1, rect.y1)),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _is_dark(color: Any) -> bool:
    return bool(color is not None and len(color) >= 3 and max(float(value) for value in color[:3]) <= 0.25)


def _axial_angle_distance(left: float, right: float) -> float:
    difference = abs(left - right) % 180.0
    return min(difference, 180.0 - difference)


def _filled_band_centerline(
    page: fitz.Page,
    drawing: dict[str, Any],
    drawing_index: int,
    width: float,
    height: float,
) -> dict[str, Any] | None:
    """Recover a centreline from a filled thin quadrilateral.

    Some DWG-to-PDF drivers export process pipes as closed, filled bands and
    set stroke width to zero.  The two longest parallel sides retain the exact
    centreline direction and endpoints.
    """

    if not _is_dark(drawing.get("fill")):
        return None
    items = list(drawing.get("items", []))
    # AutoCAD can emit an axis-aligned pipe band as one PDF ``re`` operator
    # instead of four explicit ``l`` sides.  Rotation may swap its display
    # axes, so derive the centreline from the normalized display bounding box.
    if len(items) == 1 and items[0] and items[0][0] == "re" and drawing.get("rect") is not None:
        x0, y0, x1, y1 = _display_bbox(page, drawing["rect"])
        box_width = x1 - x0
        box_height = y1 - y0
        band_width = min(box_width, box_height)
        length = max(box_width, box_height)
        midpoint = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        if (
            0.45 <= band_width <= 4.5
            and length >= 8.0
            and length / max(band_width, 1e-9) >= 5.0
            and _inside_roi(midpoint, width, height, DEFAULT_ROI)
        ):
            if box_width >= box_height:
                start = (x0, midpoint[1])
                end = (x1, midpoint[1])
            else:
                start = (midpoint[0], y0)
                end = (midpoint[0], y1)
            return {
                "drawing_index": drawing_index,
                "item_index": -1,
                "start": [round(start[0], 3), round(start[1], 3)],
                "end": [round(end[0], 3), round(end[1], 3)],
                "length": round(length, 3),
                "angle_deg": round(_line_angle(start, end), 3),
                "stroke_width": round(band_width, 3),
                "dashes": "",
                "filled_path": False,
                "derived_from_filled_band": True,
            }
    sides = []
    for item_index, item in enumerate(items):
        if not item or item[0] != "l":
            continue
        start = _display_point(page, item[1])
        end = _display_point(page, item[2])
        length = math.dist(start, end)
        sides.append((length, item_index, start, end, _line_angle(start, end)))
    if len(sides) < 4:
        return None
    longest = sorted(sides, reverse=True, key=lambda item: item[0])[:2]
    if longest[1][0] < 8.0 or longest[1][0] / max(longest[0][0], 1e-9) < 0.92:
        return None
    if _axial_angle_distance(longest[0][4], longest[1][4]) > 2.0:
        return None

    first_start, first_end = longest[0][2], longest[0][3]
    second_start, second_end = longest[1][2], longest[1][3]
    direct = math.dist(first_start, second_start) + math.dist(first_end, second_end)
    crossed = math.dist(first_start, second_end) + math.dist(first_end, second_start)
    if crossed < direct:
        second_start, second_end = second_end, second_start
    start = ((first_start[0] + second_start[0]) / 2.0, (first_start[1] + second_start[1]) / 2.0)
    end = ((first_end[0] + second_end[0]) / 2.0, (first_end[1] + second_end[1]) / 2.0)
    band_width = (math.dist(first_start, second_start) + math.dist(first_end, second_end)) / 2.0
    length = math.dist(start, end)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    if not (0.45 <= band_width <= 4.5 and length / band_width >= 5.0):
        return None
    if not _inside_roi(midpoint, width, height, DEFAULT_ROI):
        return None
    return {
        "drawing_index": drawing_index,
        "item_index": -1,
        "start": [round(start[0], 3), round(start[1], 3)],
        "end": [round(end[0], 3), round(end[1], 3)],
        "length": round(length, 3),
        "angle_deg": round(_line_angle(start, end), 3),
        "stroke_width": round(band_width, 3),
        "dashes": "",
        "filled_path": False,
        "derived_from_filled_band": True,
    }


def _direction_peaks(segments: list[dict[str, Any]], bin_size: float = 5.0, peak_count: int = 6) -> list[dict[str, float]]:
    bin_count = int(round(180.0 / bin_size))
    bins = [0.0] * bin_count
    for segment in segments:
        # Equal-ish weighting is intentional.  Paper length is a layout result,
        # not an engineering length.  Stroke width only gives a small boost to
        # likely process lines over dimension lines.
        weight = 1.0 + min(max(float(segment["stroke_width"]), 0.0), 2.0) * 0.25
        index = int(round(float(segment["angle_deg"]) / bin_size)) % bin_count
        bins[index] += weight

    ranked: list[dict[str, float]] = []
    for index in sorted(range(bin_count), key=lambda item: bins[item], reverse=True):
        if bins[index] <= 0:
            continue
        angle = (index * bin_size) % 180.0
        if any(min(abs(angle - item["angle_deg"]), 180.0 - abs(angle - item["angle_deg"])) < 10.0 for item in ranked):
            continue
        ranked.append({"angle_deg": round(angle, 3), "weight": round(bins[index], 3)})
        if len(ranked) >= peak_count:
            break
    return ranked


def _point_segment_distance(point: tuple[float, float], segment: dict[str, Any]) -> float:
    return _point_segment_projection(point, segment)[0]


def _point_segment_projection(
    point: tuple[float, float], segment: dict[str, Any]
) -> tuple[float, tuple[float, float], float]:
    x, y = point
    x1, y1 = segment["start"]
    x2, y2 = segment["end"]
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(x - x1, y - y1), (float(x1), float(y1)), 0.0
    fraction = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length_sq))
    projected = (x1 + fraction * dx, y1 + fraction * dy)
    return math.hypot(x - projected[0], y - projected[1]), projected, fraction


def _cluster_marker_candidates(candidates: list[dict[str, Any]], tolerance: float = 2.2) -> list[dict[str, Any]]:
    clusters: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        center = tuple(float(value) for value in candidate["center"])
        matching = None
        for cluster in clusters:
            cluster_center = (
                sum(float(item["center"][0]) for item in cluster) / len(cluster),
                sum(float(item["center"][1]) for item in cluster) / len(cluster),
            )
            if math.dist(center, cluster_center) <= tolerance:
                matching = cluster
                break
        if matching is None:
            clusters.append([candidate])
        else:
            matching.append(candidate)
    result = []
    for cluster in clusters:
        result.append(
            {
                "center": [
                    round(sum(float(item["center"][0]) for item in cluster) / len(cluster), 3),
                    round(sum(float(item["center"][1]) for item in cluster) / len(cluster), 3),
                ],
                "source_drawing_indices": sorted({int(item["drawing_index"]) for item in cluster}),
                "primitive_count": len(cluster),
            }
        )
    return result


def _strong_process_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stroked = [
        segment for segment in segments
        if not segment["filled_path"] and float(segment["stroke_width"]) > 0 and float(segment["length"]) >= 6.0
    ]
    if not stroked:
        return []
    maximum_width = max(float(segment["stroke_width"]) for segment in stroked)
    # On the supplied design ISO pages the process centreline is the strongest
    # stroke inside the drawing ROI.  Keep this as explicit project evidence,
    # not as a universal PDF rule.
    return [
        segment for segment in stroked
        if float(segment["stroke_width"]) >= maximum_width * 0.90
    ]


def _weld_anchor_candidates(
    process_segments: list[dict[str, Any]],
    marker_candidates: list[dict[str, Any]],
    *,
    distance_scale: float = 1.0,
) -> list[dict[str, Any]]:
    if not process_segments:
        return []
    anchors = []
    for cluster in _cluster_marker_candidates(
        marker_candidates, tolerance=2.5 * max(1.0, float(distance_scale))
    ):
        center = tuple(float(value) for value in cluster["center"])
        nearest = min(
            ((*_point_segment_projection(center, segment), segment) for segment in process_segments),
            key=lambda item: item[0],
        )
        if nearest[0] > 3.0 * max(1.0, float(distance_scale)):
            continue
        anchors.append(
            cluster
            | {
                "distance_to_strong_process_line": round(float(nearest[0]), 3),
                "process_coordinate": [round(float(value), 3) for value in nearest[1]],
                "process_projection_fraction": round(float(nearest[2]), 6),
                "nearest_process_segment": {
                    "drawing_index": nearest[3]["drawing_index"],
                    "item_index": nearest[3]["item_index"],
                    "start": nearest[3]["start"],
                    "end": nearest[3]["end"],
                    "stroke_width": nearest[3]["stroke_width"],
                },
                "evidence": "clustered-small-dark-path-on-strongest-design-stroke",
            }
        )
    # Large-format printers may emit the two dark halves of one physical weld
    # marker as separate paths whose *marker* centres are too far apart to
    # merge, even though both project to the same short process interval.
    # Deduplicate once more in process space using the normalized page scale.
    tolerance = 3.1 * max(1.0, float(distance_scale))
    projected_clusters: list[list[dict[str, Any]]] = []
    for anchor in anchors:
        point = tuple(float(value) for value in anchor["process_coordinate"])
        matching = None
        for cluster in projected_clusters:
            cluster_point = (
                sum(float(item["process_coordinate"][0]) for item in cluster) / len(cluster),
                sum(float(item["process_coordinate"][1]) for item in cluster) / len(cluster),
            )
            if math.dist(point, cluster_point) <= tolerance:
                matching = cluster
                break
        if matching is None:
            projected_clusters.append([anchor])
        else:
            matching.append(anchor)
    deduplicated = []
    for cluster in projected_clusters:
        representative = max(cluster, key=lambda item: int(item.get("primitive_count", 0)))
        if len(cluster) == 1:
            deduplicated.append(representative)
            continue
        process_coordinate = [
            round(sum(float(item["process_coordinate"][axis]) for item in cluster) / len(cluster), 3)
            for axis in (0, 1)
        ]
        center = [
            round(sum(float(item["center"][axis]) for item in cluster) / len(cluster), 3)
            for axis in (0, 1)
        ]
        deduplicated.append(
            representative
            | {
                "center": center,
                "process_coordinate": process_coordinate,
                "source_drawing_indices": sorted({
                    int(index)
                    for item in cluster
                    for index in item.get("source_drawing_indices", [])
                }),
                "primitive_count": sum(int(item.get("primitive_count", 0)) for item in cluster),
                "projected_marker_cluster_count": len(cluster),
                "evidence": "scale-normalized-marker-cluster-on-strongest-design-stroke",
            }
        )
    return deduplicated


def _flow_arrow_path_candidates(
    page: fitz.Page,
    drawings: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recognize a complete inline flow-arrow path before inspecting its strokes.

    The Malaysia DWG printer emits an inline arrow as one long zig-zag path
    joining two collinear filled pipe bands.  Looking at each short stroke in
    isolation makes the arrow tip indistinguishable from a flange boundary.
    Preserve the PDF drawing grouping and suppress every stroke belonging to
    a path only when the full glyph bridges a short collinear process gap.
    """

    process_endpoints = [
        (segment_index, side, tuple(float(value) for value in segment[side]), segment)
        for segment_index, segment in enumerate(process_segments)
        for side in ("start", "end")
    ]
    result = []
    for drawing_index, drawing in enumerate(drawings):
        if drawing.get("fill") is not None or not _is_dark(drawing.get("color")):
            continue
        line_items = [item for item in drawing.get("items", []) if item and item[0] == "l"]
        if len(line_items) < 10:
            continue
        points = [
            _display_point(page, point)
            for item in line_items
            for point in (item[1], item[2])
        ]
        attached = [
            endpoint
            for endpoint in process_endpoints
            if min(math.dist(endpoint[2], point) for point in points) <= 0.8
        ]
        best = None
        for left, right in itertools.combinations(attached, 2):
            if left[0] == right[0]:
                continue
            separation = math.dist(left[2], right[2])
            if not 8.0 <= separation <= 16.5:
                continue
            left_angle = float(left[3]["angle_deg"])
            right_angle = float(right[3]["angle_deg"])
            gap_angle = _line_angle(left[2], right[2])
            if (
                _axial_angle_distance(left_angle, right_angle) > 3.0
                or _axial_angle_distance(gap_angle, left_angle) > 4.0
            ):
                continue
            radians = math.radians(left_angle)
            unit = (math.cos(radians), math.sin(radians))
            normal = (-unit[1], unit[0])
            along = [point[0] * unit[0] + point[1] * unit[1] for point in points]
            across = [point[0] * normal[0] + point[1] * normal[1] for point in points]
            along_span = max(along) - min(along)
            across_span = max(across) - min(across)
            if not (
                8.0 <= along_span <= 22.0
                and 2.5 <= across_span <= 12.0
                and along_span / max(across_span, 1e-9) >= 1.45
            ):
                continue
            candidate = {
                "drawing_index": drawing_index,
                "bbox": [
                    round(min(point[0] for point in points), 3),
                    round(min(point[1] for point in points), 3),
                    round(max(point[0] for point in points), 3),
                    round(max(point[1] for point in points), 3),
                ],
                "center": [
                    round(sum(point[0] for point in (left[2], right[2])) / 2.0, 3),
                    round(sum(point[1] for point in (left[2], right[2])) / 2.0, 3),
                ],
                "process_endpoint_coordinates": [list(left[2]), list(right[2])],
                "process_gap_length": round(separation, 3),
                "axis_angle_deg": round(left_angle, 3),
                "line_primitive_count": len(line_items),
                "evidence": "compound-zigzag-path-bridging-collinear-process-gap",
                "component_kind": "flow-arrow",
                "confidence": 0.99,
            }
            if best is None or candidate["process_gap_length"] < best["process_gap_length"]:
                best = candidate
        if best is not None:
            result.append(best)
    return result


def _component_boundary_candidates(
    page: fitz.Page,
    drawings: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
    excluded_drawing_indices: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Find dark component boundary strokes crossing the process route.

    DWG-to-PDF output does not consistently fill every weld dot.  Flange
    faces, tee body limits and some socket-weld component ends survive as
    short dark transverse strokes or small unfilled outlines instead.
    """

    if not process_segments:
        return []
    excluded_drawing_indices = excluded_drawing_indices or set()
    raw: list[dict[str, Any]] = []
    for drawing_index, drawing in enumerate(drawings):
        if drawing_index in excluded_drawing_indices:
            continue
        if not (_is_dark(drawing.get("color")) or _is_dark(drawing.get("fill"))):
            continue
        for item_index, item in enumerate(drawing.get("items", [])):
            if not item or item[0] != "l":
                continue
            start = _display_point(page, item[1])
            end = _display_point(page, item[2])
            length = math.dist(start, end)
            if not 2.5 <= length <= 18.0:
                continue
            midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            nearest = min(
                ((*_point_segment_projection(midpoint, segment), segment) for segment in process_segments),
                key=lambda value: value[0],
            )
            if nearest[0] > 3.2:
                continue
            angle_difference = _axial_angle_distance(_line_angle(start, end), float(nearest[3]["angle_deg"]))
            if angle_difference < 58.0:
                continue
            raw.append(
                {
                    "drawing_index": drawing_index,
                    "item_index": item_index,
                    "center": [round(float(value), 3) for value in nearest[1]],
                    "process_coordinate": [round(float(value), 3) for value in nearest[1]],
                    "source_stroke": {
                        "start": [round(float(value), 3) for value in start],
                        "end": [round(float(value), 3) for value in end],
                        "length": round(length, 3),
                        "angle_difference_deg": round(angle_difference, 3),
                    },
                    "nearest_process_segment": {
                        "drawing_index": nearest[3]["drawing_index"],
                        "item_index": nearest[3]["item_index"],
                        "start": nearest[3]["start"],
                        "end": nearest[3]["end"],
                        "stroke_width": nearest[3]["stroke_width"],
                    },
                    "confidence": 0.82,
                    "evidence": "dark-transverse-component-boundary-on-process-route",
                }
            )

        rect = drawing.get("rect")
        if drawing.get("fill") is not None or rect is None:
            continue
        box = _display_bbox(page, rect)
        box_width, box_height = box[2] - box[0], box[3] - box[1]
        if not (0.8 <= box_width <= 14.0 and 0.8 <= box_height <= 14.0):
            continue
        if not drawing.get("closePath") and not any(item and item[0] == "c" for item in drawing.get("items", [])):
            continue
        midpoint = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        nearest = min(
            ((*_point_segment_projection(midpoint, segment), segment) for segment in process_segments),
            key=lambda value: value[0],
        )
        if nearest[0] > 3.2:
            continue
        raw.append(
            {
                "drawing_index": drawing_index,
                "item_index": -1,
                "bbox": [round(value, 3) for value in box],
                "center": [round(float(value), 3) for value in nearest[1]],
                "process_coordinate": [round(float(value), 3) for value in nearest[1]],
                "nearest_process_segment": {
                    "drawing_index": nearest[3]["drawing_index"],
                    "item_index": nearest[3]["item_index"],
                    "start": nearest[3]["start"],
                    "end": nearest[3]["end"],
                    "stroke_width": nearest[3]["stroke_width"],
                },
                "confidence": 0.68,
                "evidence": "dark-unfilled-component-outline-on-process-route",
            }
        )

    clustered: list[list[dict[str, Any]]] = []
    for candidate in raw:
        point = candidate["process_coordinate"]
        cluster = next(
            (
                values
                for values in clustered
                if math.dist(point, values[0]["process_coordinate"]) <= 2.2
                and values[0]["evidence"] == candidate["evidence"]
            ),
            None,
        )
        if cluster is None:
            clustered.append([candidate])
        else:
            cluster.append(candidate)
    result = []
    for cluster in clustered:
        representative = max(cluster, key=lambda item: float(item["confidence"]))
        point = [
            round(sum(float(item["process_coordinate"][axis]) for item in cluster) / len(cluster), 3)
            for axis in range(2)
        ]
        result.append(
            representative
            | {
                "center": point,
                "process_coordinate": point,
                "primitive_count": len(cluster),
                "source_drawing_indices": sorted({int(item["drawing_index"]) for item in cluster}),
            }
        )
    return result


def _process_endpoint_graph(
    segments: list[dict[str, Any]], tolerance: float = 3.0
) -> tuple[list[list[float]], list[list[tuple[int, int]]]]:
    endpoints = [list(segment[side]) for segment in segments for side in ("start", "end")]
    groups: list[list[int]] = []
    for index, point in enumerate(endpoints):
        group = next(
            (values for values in groups if math.dist(point, endpoints[values[0]]) <= tolerance),
            None,
        )
        if group is None:
            groups.append([index])
        else:
            group.append(index)
    nodes = [
        [
            sum(float(endpoints[index][axis]) for index in group) / len(group)
            for axis in range(2)
        ]
        for group in groups
    ]
    incidents: list[list[tuple[int, int]]] = [[] for _ in groups]
    for node_index, group in enumerate(groups):
        for endpoint_index in group:
            incidents[node_index].append((endpoint_index // 2, endpoint_index % 2))
    return nodes, incidents


def _exclude_weak_interior_boundary_candidates(
    boundaries: list[dict[str, Any]], process_segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Reject isolated 1--2 stroke boundaries in the middle of a pipe run.

    A real outlined weld symbol is protected as a paired glyph, and a branch
    classifier may explicitly promote a boundary using component context.
    Everything else must lie at a process-segment endpoint; otherwise small
    dimension/annotation strokes can replace a degree-2 weld without changing
    abstract topology (RV910701 P128 #10/#16).
    """

    nodes, incidents = _process_endpoint_graph(process_segments)
    fixed_symbols = _fixed_weld_symbol_candidates(
        {"component_boundary_candidates": boundaries}
    )
    result = []
    for candidate in boundaries:
        if (
            candidate.get("evidence")
            != "dark-transverse-component-boundary-on-process-route"
            or int(candidate.get("primitive_count", 0)) > 2
            or candidate.get("branch_role")
            or candidate.get("component_kind") in {
                "branch-socket-weld",
                "branch-flange-weld",
                "olet-root",
                "component-gap-end",
            }
        ):
            result.append(candidate)
            continue
        point = candidate.get("process_coordinate", candidate["center"])
        nearest_node_index = (
            min(range(len(nodes)), key=lambda index: math.dist(point, nodes[index]))
            if nodes
            else None
        )
        near_endpoint = (
            nearest_node_index is not None
            and math.dist(point, nodes[nearest_node_index]) <= 3.5
        )
        # A tee/cross centre is a process-graph junction, not one of the
        # weldable component ends.  A weak transverse stroke at that centre
        # must not be protected merely because three route segments end there.
        at_process_junction = (
            near_endpoint
            and nearest_node_index is not None
            and len(incidents[nearest_node_index]) >= 3
        )
        part_of_fixed_symbol = any(
            math.dist(
                point,
                symbol.get("process_coordinate", symbol["center"]),
            )
            <= 4.2
            for symbol in fixed_symbols
        )
        if (near_endpoint and not at_process_junction) or part_of_fixed_symbol:
            result.append(candidate)
    return result


def _candidate_from_process_point(
    point: list[float], segment: dict[str, Any], evidence: str, component_kind: str, confidence: float
) -> dict[str, Any]:
    rounded = [round(float(value), 3) for value in point]
    return {
        "center": rounded,
        "process_coordinate": rounded,
        "nearest_process_segment": {
            "drawing_index": segment["drawing_index"],
            "item_index": segment["item_index"],
            "start": segment["start"],
            "end": segment["end"],
            "stroke_width": segment["stroke_width"],
        },
        "primitive_count": 0,
        "source_drawing_indices": [],
        "confidence": confidence,
        "component_kind": component_kind,
        "evidence": evidence,
    }


def _ordinary_gap_boundary_pairs(features: dict[str, Any]) -> list[list[dict[str, Any]]]:
    segments = features.get("strong_process_segments", [])
    target_segments = features.get("segments", segments)
    nodes, incidents = _process_endpoint_graph(segments)
    terminal_indices = [index for index, values in enumerate(incidents) if len(values) == 1]
    pairs = []
    for left_index, right_index in itertools.combinations(terminal_indices, 2):
        distance = math.dist(nodes[left_index], nodes[right_index])
        if not 3.0 < distance <= 14.5:
            continue
        left_segment_index = incidents[left_index][0][0]
        right_segment_index = incidents[right_index][0][0]
        if left_segment_index == right_segment_index:
            continue
        pairs.append(
            [
                _candidate_from_process_point(
                    nodes[left_index],
                    segments[left_segment_index],
                    "paired-short-process-gap-component-boundary",
                    "elbow",
                    0.84,
                ),
                _candidate_from_process_point(
                    nodes[right_index],
                    segments[right_segment_index],
                    "paired-short-process-gap-component-boundary",
                    "elbow",
                    0.84,
                ),
            ]
        )
    return sorted(pairs, key=lambda pair: math.dist(pair[0]["center"], pair[1]["center"]))


def _component_gap_endpoint_candidates(features: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover the two weldable pipe ends around an inline component body.

    A valve/flange assembly can survive PDF printing only as a medium-sized
    break between two collinear filled pipe bands.  Its welds are the band
    endpoints, even when no separate transverse stroke is emitted there.
    Shorter 8--16.5 unit gaps are reserved for the explicit flow-arrow glyph
    classifier and are deliberately excluded here.
    """

    segments = features.get("strong_process_segments", [])
    flow_arrow_endpoints = [
        list(point)
        for arrow in features.get("flow_arrow_candidates", [])
        for point in arrow.get("process_endpoint_coordinates", [])
    ]
    nodes, incidents = _process_endpoint_graph(segments)
    terminal_indices = [index for index, values in enumerate(incidents) if len(values) == 1]
    candidates = []
    for left_index, right_index in itertools.combinations(terminal_indices, 2):
        distance = math.dist(nodes[left_index], nodes[right_index])
        if not 16.5 < distance <= 36.0:
            continue
        left_segment_index = incidents[left_index][0][0]
        right_segment_index = incidents[right_index][0][0]
        if left_segment_index == right_segment_index:
            continue
        left_segment = segments[left_segment_index]
        right_segment = segments[right_segment_index]
        left_endpoint_side = incidents[left_index][0][1]
        right_endpoint_side = incidents[right_index][0][1]
        left_interior = left_segment["end" if left_endpoint_side == 0 else "start"]
        right_interior = right_segment["end" if right_endpoint_side == 0 else "start"]
        gap_vector = (
            float(nodes[right_index][0]) - float(nodes[left_index][0]),
            float(nodes[right_index][1]) - float(nodes[left_index][1]),
        )
        left_inward = (
            float(left_interior[0]) - float(nodes[left_index][0]),
            float(left_interior[1]) - float(nodes[left_index][1]),
        )
        right_inward = (
            float(right_interior[0]) - float(nodes[right_index][0]),
            float(right_interior[1]) - float(nodes[right_index][1]),
        )
        # The two pipe runs must extend away from the component gap.  Pairing
        # the outer ends of the same two flow-arrow-adjacent segments makes
        # them point toward one another and previously manufactured a false
        # 30--34 unit valve/flange gap.
        if (
            left_inward[0] * gap_vector[0] + left_inward[1] * gap_vector[1] >= 0.0
            or right_inward[0] * -gap_vector[0] + right_inward[1] * -gap_vector[1] >= 0.0
        ):
            continue
        # Flow arrows are classified from their compound vector path before
        # component gaps are generated.  Their two process endpoints are hard
        # exclusions even if another endpoint combination happens to satisfy
        # the medium-gap length window.
        if any(
            math.dist(node, arrow_endpoint) <= 2.0
            for node in (nodes[left_index], nodes[right_index])
            for arrow_endpoint in flow_arrow_endpoints
        ):
            continue
        gap_angle = _line_angle(tuple(nodes[left_index]), tuple(nodes[right_index]))
        if (
            _axial_angle_distance(
                float(left_segment["angle_deg"]), float(right_segment["angle_deg"])
            )
            > 3.0
            or _axial_angle_distance(gap_angle, float(left_segment["angle_deg"])) > 4.0
        ):
            continue
        pair_id = f"component-gap-{left_segment_index}-{right_segment_index}"
        for node_index, segment, partner in (
            (left_index, left_segment, nodes[right_index]),
            (right_index, right_segment, nodes[left_index]),
        ):
            candidates.append(
                _candidate_from_process_point(
                    nodes[node_index],
                    segment,
                    "paired-collinear-process-gap-component-boundary",
                    "component-gap-end",
                    0.94,
                )
                | {
                    "component_gap_pair_id": pair_id,
                    "component_gap_partner_coordinate": [round(float(value), 3) for value in partner],
                    "component_gap_length": round(distance, 3),
                }
            )
    return candidates


def _fragmented_transverse_boundary_candidates(
    page: fitz.Page,
    drawings: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
    *,
    distance_scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Aggregate separately emitted dash fragments into a port boundary."""

    if not process_segments:
        return []

    def line_intersection(
        left_start: tuple[float, float],
        left_end: tuple[float, float],
        right_start: list[float],
        right_end: list[float],
    ) -> list[float] | None:
        left_vector = (
            left_end[0] - left_start[0], left_end[1] - left_start[1]
        )
        right_vector = (
            float(right_end[0]) - float(right_start[0]),
            float(right_end[1]) - float(right_start[1]),
        )
        denominator = (
            left_vector[0] * right_vector[1]
            - left_vector[1] * right_vector[0]
        )
        if abs(denominator) <= 1e-9:
            return None
        offset = (
            float(right_start[0]) - left_start[0],
            float(right_start[1]) - left_start[1],
        )
        fraction = (
            offset[0] * right_vector[1] - offset[1] * right_vector[0]
        ) / denominator
        return [
            left_start[0] + fraction * left_vector[0],
            left_start[1] + fraction * left_vector[1],
        ]

    fragments = []
    for drawing_index, drawing in enumerate(drawings):
        if not (_is_dark(drawing.get("color")) or _is_dark(drawing.get("fill"))):
            continue
        for item_index, item in enumerate(drawing.get("items", [])):
            if not item or item[0] != "l":
                continue
            start = _display_point(page, item[1])
            end = _display_point(page, item[2])
            length = math.dist(start, end)
            if not 0.35 * distance_scale <= length < 2.5 * distance_scale:
                continue
            midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            nearest = min(
                ((*_point_segment_projection(midpoint, segment), segment) for segment in process_segments),
                key=lambda value: value[0],
            )
            if nearest[0] > 8.5 * distance_scale:
                continue
            angle = _line_angle(start, end)
            if _axial_angle_distance(angle, float(nearest[3]["angle_deg"])) < 58.0:
                continue
            intersection = line_intersection(
                start,
                end,
                nearest[3]["start"],
                nearest[3]["end"],
            )
            if intersection is None:
                continue
            fragments.append({
                "drawing_index": drawing_index,
                "item_index": item_index,
                "start": start,
                "end": end,
                "length": length,
                "angle_deg": angle,
                "process_coordinate": intersection,
                "process_segment": nearest[3],
            })

    clusters: list[list[dict[str, Any]]] = []
    for fragment in fragments:
        cluster = next(
            (
                values
                for values in clusters
                if math.dist(
                    fragment["process_coordinate"], values[0]["process_coordinate"]
                ) <= 2.6 * distance_scale
                and _axial_angle_distance(
                    float(fragment["angle_deg"]), float(values[0]["angle_deg"])
                ) <= 7.0
            ),
            None,
        )
        if cluster is None:
            clusters.append([fragment])
        else:
            cluster.append(fragment)

    result = []
    for cluster in clusters:
        if len(cluster) < 3 or sum(item["length"] for item in cluster) < 3.8 * distance_scale:
            continue
        process_point = [
            sum(item["process_coordinate"][axis] for item in cluster) / len(cluster)
            for axis in range(2)
        ]
        axis_angle = math.radians(float(cluster[0]["process_segment"]["angle_deg"]))
        normal = (-math.sin(axis_angle), math.cos(axis_angle))
        offsets = [
            (point[0] - process_point[0]) * normal[0]
            + (point[1] - process_point[1]) * normal[1]
            for item in cluster
            for point in (item["start"], item["end"])
        ]
        if min(offsets) > -1.0 * distance_scale or max(offsets) < 1.0 * distance_scale:
            continue
        if max(offsets) - min(offsets) < 5.0 * distance_scale:
            continue
        rounded = [round(float(value), 3) for value in process_point]
        result.append({
            "drawing_index": int(cluster[0]["drawing_index"]),
            "item_index": int(cluster[0]["item_index"]),
            "center": rounded,
            "process_coordinate": rounded,
            "primitive_count": len(cluster),
            "source_drawing_indices": sorted({int(item["drawing_index"]) for item in cluster}),
            "nearest_process_segment": cluster[0]["process_segment"],
            "confidence": 0.86,
            "evidence": "fragmented-dark-transverse-component-boundary-on-process-route",
        })
    return result


def _long_inline_component_boundary_candidates(
    page: fitz.Page,
    drawings: list[dict[str, Any]],
    features: dict[str, Any],
    *,
    distance_scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Recover internal weld ports inside a long outlined component corridor."""

    strong = features.get("strong_process_segments", [])
    segments = features.get("segments", [])
    if not strong or not segments:
        return []
    nodes, incidents = _process_endpoint_graph(strong)
    terminal_indices = [index for index, values in enumerate(incidents) if len(values) == 1]
    corridors = []
    for left_index, right_index in itertools.combinations(terminal_indices, 2):
        left, right = nodes[left_index], nodes[right_index]
        distance = math.dist(left, right)
        if not 36.0 * distance_scale < distance <= 320.0 * distance_scale:
            continue
        left_segment = strong[incidents[left_index][0][0]]
        right_segment = strong[incidents[right_index][0][0]]
        gap_angle = _line_angle(tuple(left), tuple(right))
        if (
            left_segment is right_segment
            or _axial_angle_distance(float(left_segment["angle_deg"]), float(right_segment["angle_deg"])) > 3.0
            or _axial_angle_distance(gap_angle, float(left_segment["angle_deg"])) > 4.0
        ):
            continue
        left_side, right_side = incidents[left_index][0][1], incidents[right_index][0][1]
        left_interior = left_segment["end" if left_side == 0 else "start"]
        right_interior = right_segment["end" if right_side == 0 else "start"]
        gap_vector = (right[0] - left[0], right[1] - left[1])
        left_inward = (left_interior[0] - left[0], left_interior[1] - left[1])
        right_inward = (right_interior[0] - right[0], right_interior[1] - right[1])
        if (
            left_inward[0] * gap_vector[0] + left_inward[1] * gap_vector[1] >= 0.0
            or right_inward[0] * -gap_vector[0] + right_inward[1] * -gap_vector[1] >= 0.0
        ):
            continue
        denominator = max(distance * distance, 1e-9)
        axial_support = []
        for segment in segments:
            if _axial_angle_distance(gap_angle, float(segment["angle_deg"])) > 6.0:
                continue
            midpoint = [
                (float(segment["start"][axis]) + float(segment["end"][axis])) / 2.0
                for axis in range(2)
            ]
            fraction = ((midpoint[0] - left[0]) * gap_vector[0] + (midpoint[1] - left[1]) * gap_vector[1]) / denominator
            perpendicular = abs(gap_vector[0] * (left[1] - midpoint[1]) - (left[0] - midpoint[0]) * gap_vector[1]) / max(distance, 1e-9)
            if 0.03 <= fraction <= 0.97 and perpendicular <= 10.0 * distance_scale:
                axial_support.append(segment)
        if len(axial_support) < 2 or sum(float(item.get("length", 0.0)) for item in axial_support) < 20.0 * distance_scale:
            continue
        corridors.append({
            "start": left,
            "end": right,
            "length": distance,
            "angle_deg": gap_angle,
            "stroke_width": max(float(left_segment.get("stroke_width", 0.0)), float(right_segment.get("stroke_width", 0.0))),
            "drawing_index": -1,
            "item_index": -1,
            "support_count": len(axial_support),
            "start_process_segment": left_segment,
            "end_process_segment": right_segment,
        })
    if not corridors:
        return []

    raw = _component_boundary_candidates(page, drawings, corridors)
    raw.extend(
        _fragmented_transverse_boundary_candidates(
            page, drawings, corridors, distance_scale=distance_scale
        )
    )
    result = []
    for corridor_index, corridor in enumerate(corridors):
        for side, segment_key in (
            ("start", "start_process_segment"),
            ("end", "end_process_segment"),
        ):
            point = [round(float(value), 3) for value in corridor[side]]
            result.append({
                "drawing_index": -1,
                "item_index": -1,
                "center": point,
                "process_coordinate": point,
                "primitive_count": 0,
                "source_drawing_indices": [],
                "nearest_process_segment": corridor[segment_key],
                "confidence": 0.9,
                "component_kind": "long-inline-component-port",
                "evidence": "supported-long-inline-component-corridor-end",
                "component_corridor_id": corridor_index,
                "component_corridor_endpoints": [corridor["start"], corridor["end"]],
                "component_corridor_length": round(float(corridor["length"]), 3),
                "component_corridor_axial_support_count": int(corridor["support_count"]),
            })
    for candidate in raw:
        point = candidate["process_coordinate"]
        corridor = min(
            corridors,
            key=lambda item: _point_segment_projection(tuple(point), item)[0],
        )
        along = math.dist(corridor["start"], point)
        if not 5.0 * distance_scale <= along <= corridor["length"] - 5.0 * distance_scale:
            continue
        grouped_neighbour = any(
            other is not candidate
            and math.dist(point, other["process_coordinate"]) <= 8.0 * distance_scale
            for other in raw
        )
        if (
            not str(candidate.get("evidence", "")).startswith("fragmented-")
            and int(candidate.get("primitive_count", 0)) < 2
            and not grouped_neighbour
        ):
            continue
        result.append(candidate | {
            "component_kind": "long-inline-component-port",
            "confidence": max(0.76, float(candidate.get("confidence", 0.0))),
            "evidence": "grouped-transverse-boundary-in-supported-long-inline-component-corridor",
            "component_corridor_endpoints": [corridor["start"], corridor["end"]],
            "component_corridor_length": round(float(corridor["length"]), 3),
            "component_corridor_axial_support_count": int(corridor["support_count"]),
        })
    unique = []
    for candidate in sorted(result, key=lambda value: tuple(value["process_coordinate"])):
        if not any(math.dist(candidate["process_coordinate"], item["process_coordinate"]) <= 2.2 * distance_scale for item in unique):
            unique.append(candidate)
    return unique


def _branch_run_boundaries(features: dict[str, Any]) -> list[dict[str, Any]]:
    segments = features.get("strong_process_segments", [])
    nodes, incidents = _process_endpoint_graph(segments)
    candidates = []
    for center_index, center_incidents in enumerate(incidents):
        if len(center_incidents) < 3:
            continue
        for segment_index, endpoint_side in center_incidents:
            other_point = list(segments[segment_index]["end" if endpoint_side == 0 else "start"])
            other_node = min(range(len(nodes)), key=lambda index: math.dist(nodes[index], other_point))
            arm_length = math.dist(nodes[center_index], nodes[other_node])
            # A printed tee body commonly appears as three short process
            # segments meeting at its geometric centre.  The welds are at the
            # outer end of every short arm, including drawing/continuation
            # terminals whose process degree is only one.
            if len(incidents[other_node]) < 2 and arm_length > 32.0:
                continue
            candidates.append(
                _candidate_from_process_point(
                    nodes[other_node],
                    segments[segment_index],
                    (
                        "tee-body-run-end-connected-to-pipe"
                        if len(incidents[other_node]) >= 2
                        else "tee-body-short-terminal-end"
                    ),
                    "branch",
                    0.88,
                )
            )
    unique = []
    for candidate in candidates:
        if not any(math.dist(candidate["center"], existing["center"]) <= 3.0 for existing in unique):
            unique.append(candidate)
    return unique


def _branch_root_intersection_candidates(features: dict[str, Any]) -> list[dict[str, Any]]:
    """Project a short branch terminal back to the intersected main run.

    The Malaysia drawings use an outlined olet/root-weld symbol between the
    end of the heavy branch stroke and a continuous heavy main stroke.  It is
    not a transverse boundary and therefore is invisible to the ordinary
    boundary detector.  The two process axes are stronger evidence: the
    branch terminal faces the main and its short extrapolation intersects the
    interior of that main segment.
    """

    segments = features.get("strong_process_segments", [])
    target_segments = features.get("segments", segments)
    nodes, incidents = _process_endpoint_graph(segments)
    candidates: list[dict[str, Any]] = []
    for terminal_index, terminal_incidents in enumerate(incidents):
        if len(terminal_incidents) != 1:
            continue
        branch_segment_index, endpoint_side = terminal_incidents[0]
        branch_segment = segments[branch_segment_index]
        terminal = nodes[terminal_index]
        other = list(branch_segment["end" if endpoint_side == 0 else "start"])
        outward = (other[0] - terminal[0], other[1] - terminal[1])
        for main_segment in target_segments:
            if (
                (
                    main_segment.get("drawing_index") == branch_segment.get("drawing_index")
                    and main_segment.get("item_index") == branch_segment.get("item_index")
                )
                or bool(main_segment.get("filled_path"))
                or float(main_segment.get("length", 0.0)) < 8.0
            ):
                continue
            axis_difference = _axial_angle_distance(
                float(branch_segment["angle_deg"]), float(main_segment["angle_deg"])
            )
            if not 25.0 <= axis_difference <= 90.0:
                continue
            main_start = tuple(float(value) for value in main_segment["start"])
            main_end = tuple(float(value) for value in main_segment["end"])
            main_vector = (main_end[0] - main_start[0], main_end[1] - main_start[1])
            denominator = outward[0] * main_vector[1] - outward[1] * main_vector[0]
            if abs(denominator) <= 1e-9:
                continue
            offset = (main_start[0] - terminal[0], main_start[1] - terminal[1])
            branch_fraction = (offset[0] * main_vector[1] - offset[1] * main_vector[0]) / denominator
            main_fraction = (offset[0] * outward[1] - offset[1] * outward[0]) / denominator
            projection = (
                terminal[0] + branch_fraction * outward[0],
                terminal[1] + branch_fraction * outward[1],
            )
            distance = math.dist(terminal, projection)
            # Dashed centreline strokes often end exactly at the component
            # symbol, so allow a small extension beyond an individual dash.
            if not 3.0 < distance <= 32.0 or not -0.65 <= main_fraction <= 1.65:
                continue
            # Extending an individual thin/dashed centreline is useful when a
            # dash ends at an OLET symbol.  A heavy process band, however,
            # already carries its physical extent: projecting beyond its end
            # turns an elbow's two tangent legs into a fictitious OLET root
            # (RV910701 P128 #16).  Require a real interior intersection for
            # those strong routes.
            if float(main_segment.get("stroke_width", 0.0)) >= 0.9 and not (
                0.0 <= main_fraction <= 1.0
            ):
                continue
            # The visible branch stroke must extend away from the projected
            # main-run point, rather than merely passing close to that run.
            if branch_fraction >= 0.0:
                continue
            # Thin dashed main runs occur in this project's vertical view.
            # Accept such a target only when several collinear vector strokes
            # support the same axis, which rejects isolated dimension lines.
            collinear_length = 0.0
            for supporting in target_segments:
                if _axial_angle_distance(
                    float(main_segment["angle_deg"]), float(supporting["angle_deg"])
                ) > 3.0:
                    continue
                support_midpoint = (
                    (float(supporting["start"][0]) + float(supporting["end"][0])) / 2.0,
                    (float(supporting["start"][1]) + float(supporting["end"][1])) / 2.0,
                )
                line_dx = main_end[0] - main_start[0]
                line_dy = main_end[1] - main_start[1]
                perpendicular_distance = abs(
                    line_dx * (main_start[1] - support_midpoint[1])
                    - (main_start[0] - support_midpoint[0]) * line_dy
                ) / max(1e-12, math.hypot(line_dx, line_dy))
                if perpendicular_distance > 2.0:
                    continue
                collinear_length += float(supporting.get("length", 0.0))
            if float(main_segment.get("stroke_width", 0.0)) < 0.9 and collinear_length < 55.0:
                continue
            candidate = _candidate_from_process_point(
                list(projection),
                main_segment,
                "branch-axis-to-main-run-root-weld",
                "olet-root",
                0.93,
            )
            candidate["branch_terminal_coordinate"] = [round(float(value), 3) for value in terminal]
            candidate["main_axis_angle_deg"] = round(float(main_segment["angle_deg"]), 3)
            candidate["logical_process_axis_angle_deg"] = round(float(main_segment["angle_deg"]), 3)
            candidate["branch_process_segment"] = {
                "drawing_index": branch_segment["drawing_index"],
                "item_index": branch_segment["item_index"],
                "start": branch_segment["start"],
                "end": branch_segment["end"],
            }
            candidates.append(candidate)

    # Several parallel thin lines can satisfy the geometric test (dimension
    # extension lines beside the actual dashed pipe axis). For each visible
    # branch terminal retain the closest supported intersection.
    by_terminal: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        group = next(
            (
                values for values in by_terminal
                if math.dist(
                    candidate["branch_terminal_coordinate"],
                    values[0]["branch_terminal_coordinate"],
                ) <= 3.0
            ),
            None,
        )
        if group is None:
            by_terminal.append([candidate])
        else:
            group.append(candidate)
    closest = [
        min(
            values,
            key=lambda candidate: math.dist(
                candidate["process_coordinate"], candidate["branch_terminal_coordinate"]
            ),
        )
        for values in by_terminal
    ]
    unique: list[dict[str, Any]] = []
    for candidate in sorted(closest, key=lambda value: tuple(value["process_coordinate"])):
        if not any(math.dist(candidate["process_coordinate"], item["process_coordinate"]) <= 4.0 for item in unique):
            unique.append(candidate)
    return unique


def _thin_main_axis_boundary_candidates(
    page: fitz.Page,
    drawings: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    root_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find weld/component limits on a dashed main axis discovered by roots."""

    thin_axes: list[dict[str, Any]] = []
    for root in root_candidates:
        origin = root["process_coordinate"]
        axis_angle = float(root["main_axis_angle_deg"])
        for segment in segments:
            if _axial_angle_distance(axis_angle, float(segment["angle_deg"])) > 3.0:
                continue
            midpoint = (
                (float(segment["start"][0]) + float(segment["end"][0])) / 2.0,
                (float(segment["start"][1]) + float(segment["end"][1])) / 2.0,
            )
            radians = math.radians(axis_angle)
            axis = (math.cos(radians), math.sin(radians))
            perpendicular_distance = abs(
                axis[0] * (float(origin[1]) - midpoint[1])
                - (float(origin[0]) - midpoint[0]) * axis[1]
            )
            if perpendicular_distance <= 2.0 and segment not in thin_axes:
                thin_axes.append(segment)
    if not thin_axes:
        return []

    result = []
    for candidate in _component_boundary_candidates(page, drawings, thin_axes):
        point = candidate["process_coordinate"]
        if any(math.dist(point, root["process_coordinate"]) <= 4.0 for root in root_candidates):
            continue
        nearest_root = min(root_candidates, key=lambda root: math.dist(point, root["process_coordinate"]))
        result.append(
            candidate
            | {
                "component_kind": "thin-main-boundary",
                "confidence": max(0.84, float(candidate.get("confidence", 0.0))),
                "evidence": "thin-main-axis-" + str(candidate["evidence"]),
                "logical_process_axis_angle_deg": float(nearest_root["main_axis_angle_deg"]),
            }
        )
    unique: list[dict[str, Any]] = []
    for candidate in sorted(result, key=lambda value: tuple(value["process_coordinate"])):
        if not any(math.dist(candidate["process_coordinate"], item["process_coordinate"]) <= 2.2 for item in unique):
            unique.append(candidate)
    return unique


def _terminal_component_extension_candidates(
    page: fitz.Page,
    drawings: list[dict[str, Any]],
    strong_segments: list[dict[str, Any]],
    *,
    extension_length: float = 42.0,
) -> list[dict[str, Any]]:
    """Recover a flange/reducer weld just beyond a terminated heavy stroke."""

    nodes, incidents = _process_endpoint_graph(strong_segments)
    virtual_segments: list[dict[str, Any]] = []
    terminal_coordinates: list[list[float]] = []
    for node_index, node_incidents in enumerate(incidents):
        if len(node_incidents) != 1:
            continue
        segment_index, endpoint_side = node_incidents[0]
        segment = strong_segments[segment_index]
        terminal = nodes[node_index]
        other = list(segment["end" if endpoint_side == 0 else "start"])
        outward = (terminal[0] - other[0], terminal[1] - other[1])
        length = math.hypot(*outward)
        if length <= 1e-9:
            continue
        unit = (outward[0] / length, outward[1] / length)
        extension_end = [
            terminal[0] + unit[0] * extension_length,
            terminal[1] + unit[1] * extension_length,
        ]
        virtual_segment = {
            "drawing_index": -1,
            "item_index": -1,
            "start": terminal,
            "end": extension_end,
            "length": extension_length,
            "angle_deg": _line_angle(tuple(terminal), tuple(extension_end)),
            "stroke_width": float(segment.get("stroke_width", 0.0)),
        }
        virtual_segments.append(virtual_segment)
        terminal_coordinates.append([round(float(value), 3) for value in terminal])
    if not virtual_segments:
        return []

    # Scan the page once for all virtual extensions, then retain paired/outlined
    # boundaries. Single transverse strokes are usually dimension ticks.
    by_extension: list[list[dict[str, Any]]] = [[] for _ in virtual_segments]
    for candidate in _component_boundary_candidates(page, drawings, virtual_segments):
        point = candidate["process_coordinate"]
        extension_index = min(
            range(len(virtual_segments)),
            key=lambda index: _point_segment_projection(tuple(point), virtual_segments[index])[0],
        )
        distance = math.dist(terminal_coordinates[extension_index], point)
        if 5.0 <= distance <= extension_length:
            by_extension[extension_index].append(candidate)

    candidates: list[dict[str, Any]] = []
    for extension_index, raw_candidates in enumerate(by_extension):
        groups: list[list[dict[str, Any]]] = []
        for candidate in sorted(raw_candidates, key=lambda value: tuple(value["process_coordinate"])):
            group = next(
                (
                    values for values in groups
                    if any(
                        math.dist(candidate["process_coordinate"], member["process_coordinate"]) <= 4.8
                        for member in values
                    )
                ),
                None,
            )
            if group is None:
                groups.append([candidate])
            else:
                group.append(candidate)
        for group in groups:
            if len(group) < 2:
                continue
            points = [candidate["process_coordinate"] for candidate in group]
            maximum_span = max(math.dist(left, right) for left in points for right in points)
            if maximum_span > 12.0:
                continue
            center = [
                round(sum(float(point[axis]) for point in points) / len(points), 3)
                for axis in range(2)
            ]
            representative = max(group, key=lambda value: float(value.get("confidence", 0.0)))
            candidates.append(
                representative
                | {
                    "center": center,
                    "process_coordinate": center,
                    "component_kind": "terminal-component-leaf",
                    "confidence": 0.92,
                    "evidence": "terminal-axis-extension-paired-component-boundary",
                    "terminal_extension_source_coordinate": terminal_coordinates[extension_index],
                    "symbol_boundary_count": len(group),
                    "symbol_span": round(maximum_span, 3),
                }
            )
    unique: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda value: tuple(value["process_coordinate"])):
        if not any(math.dist(candidate["process_coordinate"], item["process_coordinate"]) <= 2.2 for item in unique):
            unique.append(candidate)
    return unique


def external_reference_terminal_candidates(
    page: fitz.Page,
    features: dict[str, Any],
    *,
    maximum_glyph_distance: float = 8.0,
) -> list[dict[str, Any]]:
    """Find component-bearing process terminals for identity-gated OCR.

    This detector is deliberately *not* part of the ordinary weld-anchor
    pool.  It supplies crop centres and possible anchors only when an IDF/PCF
    external pipeline/equipment identity is also recognized nearby.  The
    narrow gate lets us recover terminal flange/nozzle welds whose compact
    outline interrupts the heavy process stroke, without making flow arrows
    or dimension ticks generally eligible as welds.

    A candidate requires a degree-one endpoint of a heavy process segment and
    a compact closed polygon with at least five edges immediately attached to
    it.  Filled triangles used as flow arrows fail the edge-count gate; text
    outlines fail the process-terminal proximity gate.
    """

    strong_segments = list(features.get("strong_process_segments", []))
    if not strong_segments:
        return []
    drawings = page.get_drawings()
    nodes, incidents = _process_endpoint_graph(strong_segments)
    endpoint_sources: list[dict[str, Any]] = []
    for node_index, node_incidents in enumerate(incidents):
        if len(node_incidents) != 1:
            continue
        segment_index, endpoint_side = node_incidents[0]
        segment = strong_segments[segment_index]
        endpoint_sources.append(
            {
                "terminal": [float(value) for value in nodes[node_index]],
                "other": list(segment["end" if endpoint_side == 0 else "start"]),
                "node_index": node_index,
                "source_kind": "strong-process-degree-one-terminal",
            }
        )
    # Some design systems place a terminal nozzle/flange just inside the
    # title-block exclusion band.  Its pipe stroke is still unmistakably
    # heavy but is intentionally absent from ``strong_process_segments``.
    # Recover only endpoints of long heavy vector strokes; the attached
    # compact-glyph gate below remains mandatory.
    for drawing_index, drawing in enumerate(drawings):
        if float(drawing.get("width") or 0.0) < 2.4:
            continue
        for item_index, item in enumerate(drawing.get("items", [])):
            if not item or item[0] != "l":
                continue
            start = [float(item[1].x), float(item[1].y)]
            end = [float(item[2].x), float(item[2].y)]
            if math.dist(start, end) < 15.0:
                continue
            endpoint_sources.extend(
                [
                    {
                        "terminal": start,
                        "other": end,
                        "node_index": None,
                        "source_kind": "page-heavy-stroke-endpoint",
                        "source_drawing_index": drawing_index,
                        "source_item_index": item_index,
                    },
                    {
                        "terminal": end,
                        "other": start,
                        "node_index": None,
                        "source_kind": "page-heavy-stroke-endpoint",
                        "source_drawing_index": drawing_index,
                        "source_item_index": item_index,
                    },
                ]
            )
    result: list[dict[str, Any]] = []
    for endpoint in endpoint_sources:
        terminal = endpoint["terminal"]
        compact_polygons = []
        for drawing_index, drawing in enumerate(drawings):
            rect = drawing.get("rect")
            if rect is None:
                continue
            span = max(float(rect.width), float(rect.height))
            if not 3.0 <= span <= 18.0:
                continue
            rectangle_distance = math.hypot(
                max(float(rect.x0) - terminal[0], 0.0, terminal[0] - float(rect.x1)),
                max(float(rect.y0) - terminal[1], 0.0, terminal[1] - float(rect.y1)),
            )
            if rectangle_distance > maximum_glyph_distance:
                continue
            line_items = [
                item for item in drawing.get("items", []) if item and item[0] == "l"
            ]
            if len(line_items) < 5:
                continue
            first_start = line_items[0][1]
            last_end = line_items[-1][2]
            if math.dist(
                [float(first_start.x), float(first_start.y)],
                [float(last_end.x), float(last_end.y)],
            ) > 1.2:
                continue
            compact_polygons.append(
                {
                    "drawing_index": drawing_index,
                    "drawing_type": drawing.get("type"),
                    "filled": drawing.get("fill") is not None,
                    "edge_count": len(line_items),
                    "bbox": [
                        round(float(rect.x0), 3),
                        round(float(rect.y0), 3),
                        round(float(rect.x1), 3),
                        round(float(rect.y1), 3),
                    ],
                    "distance_to_process_terminal": round(rectangle_distance, 3),
                }
            )
        if not compact_polygons:
            continue
        # At least one filled polygon or two coincident outline polygons are
        # required.  This excludes an isolated outlined annotation character.
        filled_count = sum(bool(item["filled"]) for item in compact_polygons)
        if filled_count == 0 and len(compact_polygons) < 2:
            continue
        other = endpoint["other"]
        result.append(
            {
                "center": [round(value, 3) for value in terminal],
                "process_coordinate": [round(value, 3) for value in terminal],
                "component_kind": "external-reference-component-terminal",
                "candidate_tier": "identity-gated-only",
                "confidence": 0.97,
                "evidence": "heavy-process-terminal-with-compact-closed-component-glyph",
                "process_terminal_node_index": endpoint.get("node_index"),
                "terminal_source_kind": endpoint["source_kind"],
                "source_drawing_index": endpoint.get("source_drawing_index"),
                "source_item_index": endpoint.get("source_item_index"),
                "process_inward_angle_deg": round(
                    _line_angle(tuple(terminal), tuple(other)), 3
                ),
                "component_glyphs": compact_polygons,
            }
        )
    unique: list[dict[str, Any]] = []
    for candidate in sorted(result, key=lambda value: tuple(value["process_coordinate"])):
        if not any(
            math.dist(candidate["process_coordinate"], item["process_coordinate"])
            <= 2.2
            for item in unique
        ):
            unique.append(candidate)
    return unique


def _classify_branch_component_boundaries(
    boundaries: list[dict[str, Any]], root_candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Tag the socket and first credible flange boundary on each branch.

    Compound flow-arrow paths have already been removed.  Remaining isolated
    low-detail strokes can still be annotations, so a terminal flange must be
    supported either by a recovered component gap end or by a sufficiently
    rich component outline.  Later credible boundaries remain available for
    a second flange weld on an inline valve assembly.
    """

    groups: list[dict[str, Any]] = []
    for root_index, root in enumerate(root_candidates):
        root_point = root["process_coordinate"]
        branch = root.get("branch_process_segment", {})
        if not branch:
            continue
        branch_points = [branch.get("start"), branch.get("end")]
        other = max(branch_points, key=lambda point: math.dist(root_point, point))
        vector = (float(other[0]) - root_point[0], float(other[1]) - root_point[1])
        length = math.hypot(*vector)
        if length <= 1e-9:
            continue
        unit = (vector[0] / length, vector[1] / length)
        ranked = []
        for candidate in boundaries:
            point = candidate.get("process_coordinate", candidate["center"])
            relative = (float(point[0]) - root_point[0], float(point[1]) - root_point[1])
            along = relative[0] * unit[0] + relative[1] * unit[1]
            perpendicular = abs(relative[0] * unit[1] - relative[1] * unit[0])
            if 5.0 <= along <= 180.0 and perpendicular <= 3.2:
                ranked.append((along, candidate))
        if len(ranked) < 2:
            continue
        ranked.sort(key=lambda value: value[0])
        deduplicated = []
        for along, candidate in ranked:
            quality = (
                3
                if candidate.get("evidence")
                == "paired-collinear-process-gap-component-boundary"
                else 2
                if int(candidate.get("primitive_count", 0)) >= 4
                else 1
            )
            if deduplicated and abs(along - deduplicated[-1][0]) <= 3.0:
                if quality > deduplicated[-1][2]:
                    deduplicated[-1] = (along, candidate, quality)
                continue
            deduplicated.append((along, candidate, quality))
        if len(deduplicated) < 2:
            continue
        socket_along, socket_candidate, _socket_quality = deduplicated[0]
        flange_entry = next(
            (
                entry
                for entry in deduplicated[1:]
                if entry[2] >= 2
            ),
            None,
        )
        if flange_entry is None:
            continue
        flange_along, flange_candidate, _flange_quality = flange_entry
        group_id = f"branch-{root_index}"
        root_member = dict(
            root,
            branch_group_id=group_id,
            branch_group_role="root-weld",
            branch_root_index=root_index,
        )
        socket_member = dict(
            socket_candidate,
            component_kind="branch-socket-weld",
            branch_role="socket-weld-after-olet",
            branch_group_id=group_id,
            branch_group_role="socket-weld",
            branch_root_index=root_index,
            branch_along_distance=round(float(socket_along), 3),
            confidence=max(0.93, float(socket_candidate.get("confidence", 0.0))),
        )
        flange_member = dict(
            flange_candidate,
            component_kind="branch-flange-weld",
            branch_role="terminal-flange-weld",
            branch_group_id=group_id,
            branch_group_role="terminal-flange-weld",
            branch_root_index=root_index,
            branch_along_distance=round(float(flange_along), 3),
            confidence=max(0.93, float(flange_candidate.get("confidence", 0.0))),
        )
        groups.append(
            {
                "group_id": group_id,
                "root_index": root_index,
                "members": [root_member, socket_member, flange_member],
                "root_to_terminal_distance": round(
                    math.dist(root_point, root.get("branch_terminal_coordinate", root_point)), 3
                ),
                "downstream_candidate_count": len(deduplicated),
                "branch_axis_angle_deg": round(
                    _line_angle(tuple(root_point), tuple(other)), 3
                ),
                "downstream_span": round(float(flange_along - socket_along), 3),
            }
        )
        socket_candidate.update(
            {
                "component_kind": "branch-socket-weld",
                "branch_role": "socket-weld-after-olet",
                "branch_root_index": root_index,
                "confidence": max(0.93, float(socket_candidate.get("confidence", 0.0))),
            }
        )
        flange_candidate.update(
            {
                "component_kind": "branch-flange-weld",
                "branch_role": "terminal-flange-weld",
                "branch_root_index": root_index,
                "confidence": max(0.93, float(flange_candidate.get("confidence", 0.0))),
            }
        )
        for along, candidate, quality in deduplicated[1:]:
            if along >= flange_along:
                # Preserve the opposite side of a valve/flange assembly as a
                # normal supplemental flange candidate.
                continue
            if quality < 2:
                candidate["branch_role"] = "interior-flow-or-annotation-symbol"
    return groups


def _swapped_thin_branch_groups(
    groups: list[dict[str, Any]], segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Create OLET hypotheses when the thin branch/main roles were reversed.

    Root detection starts from a heavy terminal and projects to any supported
    crossing axis. On small-bore branches the heavy terminal can actually be
    the main run while the true branch is a thin vector axis. Preserve both
    interpretations and let IDF topology choose the complete three-weld group.
    """

    result = []
    for group in groups:
        root = dict(group["members"][0])
        root_point = root.get("process_coordinate", root["center"])
        thin_axis_angle = float(root.get("main_axis_angle_deg", 0.0))
        heavy_axis_angle = float(group.get("branch_axis_angle_deg", 0.0))
        radians = math.radians(thin_axis_angle)
        axis = (math.cos(radians), math.sin(radians))
        extents = []
        for segment in segments:
            if _axial_angle_distance(thin_axis_angle, float(segment["angle_deg"])) > 3.0:
                continue
            for endpoint in (segment["start"], segment["end"]):
                relative = (
                    float(endpoint[0]) - float(root_point[0]),
                    float(endpoint[1]) - float(root_point[1]),
                )
                along = relative[0] * axis[0] + relative[1] * axis[1]
                perpendicular = abs(relative[0] * axis[1] - relative[1] * axis[0])
                if perpendicular <= 2.5 and 10.0 <= abs(along) <= 90.0:
                    extents.append(along)
        if not extents:
            continue
        positive = max((value for value in extents if value > 0.0), default=0.0)
        negative = min((value for value in extents if value < 0.0), default=0.0)
        terminal_along = positive if positive >= abs(negative) else negative
        if abs(terminal_along) < 24.0:
            continue
        direction = 1.0 if terminal_along > 0.0 else -1.0
        socket_distance = min(9.0, max(6.0, abs(terminal_along) * 0.22))
        socket_point = [
            round(float(root_point[coordinate]) + axis[coordinate] * direction * socket_distance, 3)
            for coordinate in range(2)
        ]
        flange_point = [
            round(float(root_point[coordinate]) + axis[coordinate] * terminal_along, 3)
            for coordinate in range(2)
        ]
        group_id = str(group["group_id"]) + "-swapped"
        swapped_root = root | {
            "branch_group_id": group_id,
            "branch_group_role": "root-weld",
            "logical_process_axis_angle_deg": round(heavy_axis_angle, 3),
            "main_axis_angle_deg": round(heavy_axis_angle, 3),
            "orientation_variant": "swapped-thin-branch",
        }
        swapped_root.pop("branch_terminal_coordinate", None)
        socket = {
            "center": socket_point,
            "process_coordinate": socket_point,
            "component_kind": "branch-socket-weld",
            "branch_role": "socket-weld-after-olet",
            "branch_group_id": group_id,
            "branch_group_role": "socket-weld",
            "terminal_extension_source_coordinate": list(root_point),
            "confidence": 0.86,
            "evidence": "swapped-thin-branch-socket-weld",
            "orientation_variant": "swapped-thin-branch",
        }
        flange = {
            "center": flange_point,
            "process_coordinate": flange_point,
            "component_kind": "branch-flange-weld",
            "branch_role": "terminal-flange-weld",
            "branch_group_id": group_id,
            "branch_group_role": "terminal-flange-weld",
            "terminal_extension_source_coordinate": socket_point,
            "confidence": 0.84,
            "evidence": "swapped-thin-branch-terminal-weld",
            "orientation_variant": "swapped-thin-branch",
        }
        result.append(
            {
                "group_id": group_id,
                "base_group_id": group["group_id"],
                "root_index": group.get("root_index"),
                "members": [swapped_root, socket, flange],
                "root_to_terminal_distance": group.get("root_to_terminal_distance"),
                "downstream_candidate_count": 3,
                "branch_axis_angle_deg": round(thin_axis_angle, 3),
                "downstream_span": round(abs(terminal_along) - socket_distance, 3),
                "orientation_variant": "swapped-thin-branch",
            }
        )
    return result


def _anchor_kind_counts(idf: dict[str, Any]) -> dict[str, int]:
    counts = {"flange": 0, "branch": 0, "ordinary": 0}
    seen = set()
    for weld in idf.get("welds", []):
        key = str(weld.get("weld_key"))
        if key in seen:
            continue
        seen.add(key)
        types = set(weld.get("adjacent_component_types", []))
        if "flange" in types:
            counts["flange"] += 1
        elif "branch" in types or "olet" in types:
            counts["branch"] += 1
        else:
            counts["ordinary"] += 1
    return counts


def _compound_weld_path_signature(
    lines: list[tuple[tuple[float, float], tuple[float, float]]],
    *,
    snap_tolerance: float = 0.12,
) -> dict[str, Any] | None:
    """Recognise one complete Malaysia design-weld glyph path.

    AutoCAD emits the outlined hexagonal weld marker and its internal strokes
    as one path.  Across every orientation currently observed that path has
    twelve line primitives, eleven vertices, ten degree-2 vertices and one
    degree-4 crossing.  This graph signature is invariant under rotation,
    reflection and uniform scale and, unlike isolated transverse strokes,
    cannot be confused with a flange face or flow arrow.
    """

    if len(lines) != 12:
        return None
    vertices: list[list[float]] = []
    edges: list[tuple[int, int]] = []

    def vertex_index(point: tuple[float, float]) -> int:
        for index, vertex in enumerate(vertices):
            if math.dist(point, vertex) <= snap_tolerance:
                return index
        vertices.append([float(point[0]), float(point[1])])
        return len(vertices) - 1

    for start, end in lines:
        left = vertex_index(start)
        right = vertex_index(end)
        if left == right:
            return None
        edges.append((left, right))
    if len(vertices) != 11:
        return None
    degrees = [0] * len(vertices)
    for left, right in edges:
        degrees[left] += 1
        degrees[right] += 1
    if sorted(degrees) != [2] * 10 + [4]:
        return None
    xs = [point[0] for point in vertices]
    ys = [point[1] for point in vertices]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    short_span = min(width, height)
    long_span = max(width, height)
    # The same block is anisotropically foreshortened by AutoCAD when its
    # insertion axis follows an isometric 30-degree run.  On the Malaysia 05
    # sheets the short span is then about 2.48 pt (drawing paths 849/922),
    # while vertical instances remain about 3.87 pt wide.  The graph above is
    # the discriminating signature; use scale/aspect bounds only as a guard
    # against tiny text and large annotation paths.
    if not (
        2.2 <= short_span <= 7.5
        and 3.5 <= long_span <= 9.0
        and long_span / max(short_span, 1e-9) <= 2.25
    ):
        return None
    return {
        "vertex_count": len(vertices),
        "edge_count": len(edges),
        "degree_histogram": {"2": 10, "4": 1},
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "center": [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0],
        "short_span": short_span,
        "long_span": long_span,
        "aspect_ratio": long_span / max(short_span, 1e-9),
    }


def _compound_weld_symbol_candidates(
    page: fitz.Page,
    drawings: list[dict[str, Any]],
    process_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract rotation-invariant complete weld glyphs from PDF vector paths."""

    width = float(page.rect.width)
    height = float(page.rect.height)
    result: list[dict[str, Any]] = []
    for drawing_index, drawing in enumerate(drawings):
        if drawing.get("fill") is not None or not _is_dark(drawing.get("color")):
            continue
        raw_lines = [item for item in drawing.get("items", []) if item and item[0] == "l"]
        if len(raw_lines) != 12 or len(raw_lines) != len(drawing.get("items", [])):
            continue
        lines = [
            (_display_point(page, item[1]), _display_point(page, item[2]))
            for item in raw_lines
        ]
        signature = _compound_weld_path_signature(lines)
        if signature is None:
            continue
        center = signature["center"]
        if not _inside_roi((center[0], center[1]), width, height, DEFAULT_ROI):
            continue
        nearest = None
        for segment in process_segments:
            distance, projection, along = _point_segment_projection(center, segment)
            ranked = (distance, projection, along, segment)
            if nearest is None or ranked[0] < nearest[0]:
                nearest = ranked
        # The pipe line may stop at the compound marker, so tolerate the
        # marker half-width plus a small PDF quantisation allowance.
        if nearest is not None and nearest[0] <= 4.5:
            process_coordinate = nearest[1]
            route_distance = float(nearest[0])
        else:
            process_coordinate = center
            route_distance = math.inf if nearest is None else float(nearest[0])
        result.append(
            {
                "center": [round(float(value), 3) for value in center],
                "process_coordinate": [round(float(value), 3) for value in process_coordinate],
                "bbox": [round(float(value), 3) for value in signature["bbox"]],
                "primitive_count": 12,
                "source_drawing_indices": [drawing_index],
                "confidence": 1.0,
                "component_kind": "weld-symbol",
                "evidence": "compound-hexagonal-design-weld-symbol",
                "glyph_validated": True,
                "glyph_signature": {
                    "path_count": 1,
                    "line_count": 12,
                    "vertex_count": 11,
                    "degree_histogram": {"2": 10, "4": 1},
                    "short_span": round(float(signature["short_span"]), 3),
                    "long_span": round(float(signature["long_span"]), 3),
                    "aspect_ratio": round(float(signature["aspect_ratio"]), 3),
                },
                "process_route_distance": (
                    round(route_distance, 3) if math.isfinite(route_distance) else None
                ),
            }
        )
    return sorted(result, key=lambda candidate: tuple(candidate["process_coordinate"]))


def _fixed_weld_symbol_candidates(features: dict[str, Any]) -> list[dict[str, Any]]:
    """Recover this design institute's outlined weld symbol.

    On the Malaysia ISO sheets a physical weld is drawn as a small outlined
    band crossing the heavy process route. Vector extraction sees the two
    sides of that outline as two nearby transverse component boundaries. A
    filled elbow corner can also look like a small dark marker, so the paired
    transverse outline is the stronger project-specific weld-location
    evidence.
    """

    compound = [
        dict(candidate)
        for candidate in features.get("compound_weld_symbol_candidates", [])
    ]
    if compound:
        return sorted(compound, key=lambda candidate: tuple(candidate["process_coordinate"]))

    boundaries = [
        candidate
        for candidate in features.get("component_boundary_candidates", [])
        if candidate.get("evidence") == "dark-transverse-component-boundary-on-process-route"
    ]
    groups: list[list[dict[str, Any]]] = []
    for candidate in boundaries:
        point = candidate.get("process_coordinate", candidate["center"])
        matching = [
            group
            for group in groups
            if any(
                math.dist(point, member.get("process_coordinate", member["center"])) <= 4.8
                for member in group
            )
        ]
        if not matching:
            groups.append([candidate])
            continue
        group = matching[0]
        group.append(candidate)
        # Preserve single-link clustering if a third stroke joins two groups.
        for extra in matching[1:]:
            group.extend(extra)
            groups.remove(extra)

    result = []
    for group in groups:
        if len(group) < 2:
            continue
        points = [member.get("process_coordinate", member["center"]) for member in group]
        maximum_span = max(math.dist(left, right) for left in points for right in points)
        # Separate weld symbols on the same short component remain much farther
        # apart (about 14 PDF units in the current corpus). This limit accepts
        # the occasional third outline stroke without merging two welds.
        if maximum_span > 7.2:
            continue
        center = [
            round(sum(float(point[axis]) for point in points) / len(points), 3)
            for axis in range(2)
        ]
        representative = min(
            group,
            key=lambda member: math.dist(
                center, member.get("process_coordinate", member["center"])
            ),
        )
        result.append(
            representative
            | {
                "center": center,
                "process_coordinate": center,
                "primitive_count": sum(int(member.get("primitive_count", 0)) for member in group),
                "source_drawing_indices": sorted(
                    {
                        int(index)
                        for member in group
                        for index in member.get("source_drawing_indices", [])
                    }
                ),
                "symbol_member_coordinates": points,
                "symbol_boundary_count": len(group),
                "symbol_span": round(maximum_span, 3),
                "confidence": 0.97,
                "component_kind": "weld-symbol",
                "evidence": "paired-transverse-design-weld-symbol",
                "glyph_validated": False,
                "candidate_tier": "provisional-boundary-inference",
            }
        )
    return sorted(result, key=lambda candidate: tuple(candidate["process_coordinate"]))


def _angle_error(left: float, right: float) -> float:
    difference = abs(left - right) % 360.0
    return min(difference, 360.0 - difference)


def _direction_guided_ordinary_boundaries(
    idf: dict[str, Any], features: dict[str, Any], primary: list[dict[str, Any]], count: int
) -> list[dict[str, Any]]:
    """Extend a proven primary chain with PDF boundary evidence at its ends."""

    if count <= 0 or len(primary) < 2:
        return []
    from .topology_match import anchor_chain_orders, build_process_graph, project_direction

    graph = build_process_graph(features | {"weld_anchor_candidates": primary})
    paper_orders = anchor_chain_orders(graph)
    weld_orders = idf.get("weld_graph", {}).get("chain_orders", [])
    if not paper_orders or not weld_orders:
        return []
    weld_by_key = {str(weld["weld_key"]): weld for weld in idf.get("welds", [])}
    north_arrow = str(idf.get("drawing_viewpoint", {}).get("northArrow", "top-right-boxed"))

    def angle(vector: tuple[float, float]) -> float:
        return math.degrees(math.atan2(vector[1], vector[0])) % 360.0

    best: tuple[float, list[str], list[int], int] | None = None
    for weld_order in weld_orders:
        if len(weld_order) < len(primary):
            continue
        for paper_order in paper_orders:
            for start_index in range(len(weld_order) - len(primary) + 1):
                errors = []
                for offset in range(len(primary) - 1):
                    left_weld = weld_by_key[str(weld_order[start_index + offset])]
                    right_weld = weld_by_key[str(weld_order[start_index + offset + 1])]
                    projected = project_direction(
                        [
                            float(right_weld["engineering_coordinate"][axis])
                            - float(left_weld["engineering_coordinate"][axis])
                            for axis in range(3)
                        ],
                        north_arrow,
                    )
                    left = primary[paper_order[offset]].get("process_coordinate", primary[paper_order[offset]]["center"])
                    right = primary[paper_order[offset + 1]].get(
                        "process_coordinate", primary[paper_order[offset + 1]]["center"]
                    )
                    errors.append(_angle_error(angle(projected), angle((right[0] - left[0], right[1] - left[1]))))
                score = sum(errors) / max(1, len(errors))
                candidate = (score, [str(key) for key in weld_order], list(paper_order), start_index)
                if best is None or candidate[0] < best[0]:
                    best = candidate
    if best is None or best[0] > 8.0:
        return []

    _, weld_order, paper_order, start_index = best
    segments = features.get("strong_process_segments", [])
    nodes, _incidents = _process_endpoint_graph(segments)
    pool = []
    for raw in list(features.get("component_boundary_candidates", [])) + list(
        features.get("terminal_extension_candidates", [])
    ):
        point = raw.get("process_coordinate", raw["center"])
        if not nodes or min(math.dist(point, node) for node in nodes) > 3.5:
            continue
        if any(math.dist(point, anchor.get("process_coordinate", anchor["center"])) <= 18.0 for anchor in primary):
            continue
        pool.append(raw | {"component_kind": "elbow", "confidence": max(0.86, float(raw.get("confidence", 0.0)))})

    added: list[dict[str, Any]] = []

    def choose(current: list[float], desired_angle: float) -> dict[str, Any] | None:
        ranked = []
        for candidate in pool:
            if candidate in added:
                continue
            point = candidate.get("process_coordinate", candidate["center"])
            distance = math.dist(current, point)
            if distance <= 8.0:
                continue
            error = _angle_error(desired_angle, angle((point[0] - current[0], point[1] - current[1])))
            if error > 12.0:
                continue
            endpoint_distance = min(math.dist(point, node) for node in nodes)
            ranked.append((error, endpoint_distance, distance, candidate))
        return min(ranked, key=lambda item: item[:3])[3] if ranked else None

    after_count = len(weld_order) - (start_index + len(primary))
    current = list(primary[paper_order[-1]].get("process_coordinate", primary[paper_order[-1]]["center"]))
    for offset in range(min(count, after_count)):
        left_weld = weld_by_key[weld_order[start_index + len(primary) - 1 + offset]]
        right_weld = weld_by_key[weld_order[start_index + len(primary) + offset]]
        projected = project_direction(
            [
                float(right_weld["engineering_coordinate"][axis])
                - float(left_weld["engineering_coordinate"][axis])
                for axis in range(3)
            ],
            north_arrow,
        )
        candidate = choose(current, angle(projected))
        if candidate is None:
            break
        candidate = candidate | {"evidence": "idf-direction-guided-" + str(candidate["evidence"])}
        added.append(candidate)
        current = list(candidate.get("process_coordinate", candidate["center"]))

    before_count = start_index
    current = list(primary[paper_order[0]].get("process_coordinate", primary[paper_order[0]]["center"]))
    before_added = []
    for offset in range(min(count - len(added), before_count)):
        right_index = start_index - offset
        left_weld = weld_by_key[weld_order[right_index - 1]]
        right_weld = weld_by_key[weld_order[right_index]]
        projected = project_direction(
            [
                float(right_weld["engineering_coordinate"][axis])
                - float(left_weld["engineering_coordinate"][axis])
                for axis in range(3)
            ],
            north_arrow,
        )
        candidate = choose(current, (angle(projected) + 180.0) % 360.0)
        if candidate is None:
            break
        candidate = candidate | {"evidence": "idf-direction-guided-" + str(candidate["evidence"])}
        before_added.append(candidate)
        current = list(candidate.get("process_coordinate", candidate["center"]))
    return list(reversed(before_added)) + added


def _select_flange_boundaries(
    idf: dict[str, Any],
    features: dict[str, Any],
    selected: list[dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    from .topology_match import project_direction

    segments = features.get("strong_process_segments", [])
    nodes, incidents = _process_endpoint_graph(segments)
    candidates = []
    for raw in features.get("component_boundary_candidates", []):
        point = raw.get("process_coordinate", raw["center"])
        if any(math.dist(point, anchor.get("process_coordinate", anchor["center"])) <= 4.0 for anchor in selected):
            continue
        node_index = min(range(len(nodes)), key=lambda index: math.dist(nodes[index], point)) if nodes else None
        if node_index is None or math.dist(nodes[node_index], point) > 3.5:
            continue
        # A degree-3 node is the tee centre, not a flange face.  Tee run-end
        # anchors are supplied separately from the component topology.
        if len(incidents[node_index]) >= 3:
            continue
        enriched = raw | {"component_kind": "flange", "process_node_degree": len(incidents[node_index])}
        if any(
            math.dist(point, existing.get("process_coordinate", existing["center"])) <= 1.2
            for existing in candidates
        ):
            continue
        candidates.append(enriched)
    if not candidates:
        return []

    weld_by_key = {str(weld["weld_key"]): weld for weld in idf.get("welds", [])}
    flange_welds = [
        weld for weld in weld_by_key.values() if "flange" in set(weld.get("adjacent_component_types", []))
    ]
    graph_edges = idf.get("weld_graph", {}).get("edges", [])
    north_arrow = str(idf.get("drawing_viewpoint", {}).get("northArrow", "top-right-boxed"))

    def rank(candidate: dict[str, Any]) -> tuple[float, float, float, float]:
        point = candidate.get("process_coordinate", candidate["center"])
        nearest_selected = min(
            selected,
            key=lambda anchor: math.dist(point, anchor.get("process_coordinate", anchor["center"])),
            default=None,
        )
        distance_to_selected = (
            math.dist(point, nearest_selected.get("process_coordinate", nearest_selected["center"]))
            if nearest_selected
            else math.inf
        )
        direction_error = 180.0
        if flange_welds and nearest_selected:
            flange = flange_welds[0]
            edge = next(
                (edge for edge in graph_edges if str(flange["weld_key"]) in [str(key) for key in edge["weld_keys"]]),
                None,
            )
            if edge:
                neighbour_key = next(str(key) for key in edge["weld_keys"] if str(key) != str(flange["weld_key"]))
                neighbour = weld_by_key.get(neighbour_key)
                if neighbour:
                    delta = [
                        float(neighbour["engineering_coordinate"][axis])
                        - float(flange["engineering_coordinate"][axis])
                        for axis in range(3)
                    ]
                    projected = project_direction(delta, north_arrow)
                    paper = (
                        nearest_selected.get("process_coordinate", nearest_selected["center"])[0] - point[0],
                        nearest_selected.get("process_coordinate", nearest_selected["center"])[1] - point[1],
                    )
                    direction_error = _angle_error(
                        math.degrees(math.atan2(projected[1], projected[0])),
                        math.degrees(math.atan2(paper[1], paper[0])),
                    )
        fingerprints = " ".join(
            value for weld in flange_welds for value in weld.get("adjacent_component_fingerprints", [])
        ).upper()
        primitive_count = float(candidate.get("primitive_count", 0))
        if "FLWN" in fingerprints:
            style_preference = distance_to_selected
        else:
            style_preference = -primitive_count
        return direction_error, style_preference, -float(candidate.get("process_node_degree", 0)), distance_to_selected

    return sorted(candidates, key=rank)[:count]


def augment_weld_anchor_candidates(
    features: dict[str, Any],
    idf: dict[str, Any] | None = None,
    *,
    fixed_symbol_policy: str = "complete-signature",
    marker_policy: str = "strong-process-projection",
) -> dict[str, Any]:
    """Add only the component-boundary evidence needed by the IDF weld topology."""

    if marker_policy not in {"strong-process-projection", "all-compact-filled"}:
        raise ValueError(f"Unsupported formal marker policy: {marker_policy}")
    legacy_primary = [
        dict(anchor, confidence=float(anchor.get("confidence", 1.0)))
        for anchor in features.get("weld_anchor_candidates", [])
    ]
    if marker_policy == "all-compact-filled":
        # Some design institutes draw elbow centre-lines as Bezier curves.
        # Those curves do not enter the line-only strong-process graph, while
        # their compact filled weld marker remains an authoritative physical
        # root.  A project profile may therefore promote the already size,
        # colour and drawing-ROI gated marker directly.
        legacy_primary = [
            {
                **dict(marker),
                "process_coordinate": list(marker["center"]),
                "confidence": 0.96,
                "evidence": "compact-filled-weld-marker-project-signature",
            }
            for marker in features.get("marker_candidates", [])
        ]
    fixed_symbols = [dict(anchor) for anchor in features.get("fixed_weld_symbol_candidates", [])]
    if not fixed_symbols:
        fixed_symbols = _fixed_weld_symbol_candidates(features)
    if idf is None:
        return features | {
            "weld_anchor_candidates": legacy_primary,
            "raw_filled_marker_anchor_candidate_count": len(legacy_primary),
            "fixed_weld_symbol_candidate_count": len(fixed_symbols),
            "primary_weld_anchor_candidate_count": len(legacy_primary),
            "augmented_weld_anchor_candidate_count": len(legacy_primary),
            "anchor_source_strategy": "legacy-filled-marker",
            "formal_marker_policy": marker_policy,
        }

    expected = _anchor_kind_counts(idf)
    weld_site_count = int(idf.get("weld_site_count", 0))
    # The paired outline is used on some flange/tee boundaries as well as
    # ordinary elbow welds. Treat it as a physical-weld symbol first; IDF
    # component semantics are used only to fill the remaining positions.
    if fixed_symbol_policy not in {"complete-signature", "partial-evidence"}:
        raise ValueError(f"Unsupported fixed symbol policy: {fixed_symbol_policy}")
    use_fixed_symbols = (
        fixed_symbol_policy == "complete-signature"
        and bool(fixed_symbols)
        and len(fixed_symbols) <= weld_site_count
    )
    if fixed_symbol_policy == "partial-evidence":
        primary = list(fixed_symbols)
        for candidate in legacy_primary:
            point = candidate.get("process_coordinate", candidate["center"])
            if not any(
                math.dist(point, item.get("process_coordinate", item["center"])) <= 4.0
                for item in primary
            ):
                primary.append(candidate)
        primary = primary[:weld_site_count]
    else:
        primary = fixed_symbols if use_fixed_symbols else legacy_primary
    semantic_replacements: list[dict[str, Any]] = []
    if fixed_symbol_policy == "partial-evidence" and primary:
        distance_scale = float(features.get("vector_scale_profile", {}).get("threshold_scale", 1.0))
        strong_endpoints = [
            tuple(segment[side])
            for segment in features.get("strong_process_segments", [])
            for side in ("start", "end")
        ]
        for terminal in features.get("continuation_terminal_candidates", []):
            terminal_point = terminal.get("process_coordinate", terminal["center"])
            if any(
                math.dist(terminal_point, item.get("process_coordinate", item["center"])) <= 12.0 * distance_scale
                for item in primary
            ):
                continue
            pairs = [
                (math.dist(
                    left.get("process_coordinate", left["center"]),
                    right.get("process_coordinate", right["center"]),
                ), left_index, right_index)
                for left_index, left in enumerate(primary)
                for right_index, right in enumerate(primary[left_index + 1 :], start=left_index + 1)
            ]
            close = [pair for pair in pairs if pair[0] <= 28.0 * distance_scale]
            if not close:
                continue
            _, left_index, right_index = min(close)
            def endpoint_residual(index: int) -> float:
                point = primary[index].get("process_coordinate", primary[index]["center"])
                return min((math.dist(point, endpoint) for endpoint in strong_endpoints), default=math.inf)
            removed_index = max((left_index, right_index), key=endpoint_residual)
            removed = primary.pop(removed_index)
            primary.append(terminal)
            semantic_replacements.append(
                {
                    "removed": removed.get("process_coordinate", removed["center"]),
                    "added": terminal_point,
                    "rule": "continuation-terminal-replaces-close-elbow-centre-candidate",
                }
            )
    strategy = (
        str(primary[0].get("evidence"))
        if use_fixed_symbols and primary
        else ("mixed-partial-vector-evidence" if fixed_symbol_policy == "partial-evidence" else "legacy-filled-marker")
    )

    if len(primary) >= weld_site_count:
        return features | {
            "weld_anchor_candidates": primary,
            "raw_filled_marker_anchor_candidate_count": len(legacy_primary),
            "fixed_weld_symbol_candidate_count": len(fixed_symbols),
            "primary_weld_anchor_candidate_count": len(primary),
            "augmented_weld_anchor_candidate_count": len(primary),
            "anchor_source_strategy": strategy,
            "formal_marker_policy": marker_policy,
            "anchor_augmentation": {
                "expected_kind_counts": expected,
                "unfilled_kind_counts": {"ordinary": 0, "branch": 0, "flange": 0},
                "added_count": 0,
                "replaced_filled_marker_count": len(legacy_primary) if use_fixed_symbols else 0,
                "added_evidence": [],
                "semantic_replacements": semantic_replacements,
            },
        }

    selected = list(primary)
    primary_for_ordinary = min(len(primary), expected["ordinary"])
    remaining_primary = len(primary) - primary_for_ordinary
    missing = {
        "ordinary": max(0, expected["ordinary"] - primary_for_ordinary),
        "branch": max(0, expected["branch"] - min(expected["branch"], remaining_primary)),
        "flange": expected["flange"],
    }

    # Once the fixed project symbol has been detected, an isolated component
    # boundary is not sufficient evidence for another ordinary weld. This
    # prevents elbow tangent/corner points from being reintroduced.
    ordinary_candidates = (
        []
        if use_fixed_symbols
        else _direction_guided_ordinary_boundaries(idf, features, primary, missing["ordinary"])
    )
    ordinary_candidates = ordinary_candidates[: max(0, weld_site_count - len(selected))]
    selected.extend(ordinary_candidates)
    missing["ordinary"] = max(0, missing["ordinary"] - len(ordinary_candidates))

    branch_candidates = [
        candidate
        for candidate in _branch_run_boundaries(features)
        if not any(math.dist(candidate["center"], anchor.get("process_coordinate", anchor["center"])) <= 3.0 for anchor in selected)
    ]
    branch_candidates = branch_candidates[: min(missing["branch"], max(0, weld_site_count - len(selected)))]
    selected.extend(branch_candidates)
    missing["branch"] = max(0, missing["branch"] - len(branch_candidates))

    flange_candidates = _select_flange_boundaries(
        idf,
        features,
        selected,
        min(missing["flange"], max(0, weld_site_count - len(selected))),
    )
    selected.extend(flange_candidates)
    missing["flange"] = max(0, missing["flange"] - len(flange_candidates))

    return features | {
        "weld_anchor_candidates": selected,
        "raw_filled_marker_anchor_candidate_count": len(legacy_primary),
        "fixed_weld_symbol_candidate_count": len(fixed_symbols),
        "primary_weld_anchor_candidate_count": len(primary),
        "augmented_weld_anchor_candidate_count": len(selected),
        "anchor_source_strategy": strategy,
        "formal_marker_policy": marker_policy,
        "anchor_augmentation": {
            "expected_kind_counts": expected,
            "unfilled_kind_counts": missing,
            "added_count": len(selected) - len(primary),
            "replaced_filled_marker_count": len(legacy_primary) if use_fixed_symbols else 0,
            "added_evidence": [anchor.get("evidence") for anchor in selected[len(primary) :]],
            "semantic_replacements": semantic_replacements,
        },
    }


def analyze_page(page: fitz.Page, *, source: str = "", page_number: int | None = None) -> dict[str, Any]:
    width = float(page.rect.width)
    height = float(page.rect.height)
    scale_profile = estimate_vector_scale(page)
    distance_scale = scale_profile.threshold_scale
    segments: list[dict[str, Any]] = []
    marker_candidates: list[dict[str, Any]] = []
    drawings = page.get_drawings()

    for drawing_index, drawing in enumerate(drawings):
        stroke_width = float(drawing.get("width") or 0.0)
        fill = drawing.get("fill")
        band_centerline = _filled_band_centerline(page, drawing, drawing_index, width, height)
        if band_centerline is not None:
            segments.append(band_centerline)
        if band_centerline is None and fill is not None and _is_dark(fill) and drawing.get("rect") is not None:
            box = _display_bbox(page, drawing["rect"])
            box_width = box[2] - box[0]
            box_height = box[3] - box[1]
            midpoint = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
            if (
                0.8 * distance_scale <= box_width <= 14.0 * distance_scale
                and 0.8 * distance_scale <= box_height <= 14.0 * distance_scale
                and _inside_roi(midpoint, width, height, DEFAULT_ROI)
            ):
                marker_candidates.append(
                    {
                        "drawing_index": drawing_index,
                        "bbox": [round(value, 3) for value in box],
                        "center": [round(value, 3) for value in midpoint],
                    }
                )

        for item_index, item in enumerate(drawing.get("items", [])):
            if not item or item[0] != "l":
                continue
            start = _display_point(page, item[1])
            end = _display_point(page, item[2])
            length = math.dist(start, end)
            midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if not _inside_roi(midpoint, width, height, DEFAULT_ROI):
                continue
            # Very long lines are almost always frame/table boundaries.  The
            # lower cutoff rejects most vectorized text glyph fragments.
            if length < 8.0 * distance_scale or length > width * 0.55:
                continue
            segments.append(
                {
                    "drawing_index": drawing_index,
                    "item_index": item_index,
                    "start": [round(start[0], 3), round(start[1], 3)],
                    "end": [round(end[0], 3), round(end[1], 3)],
                    "length": round(length, 3),
                    "angle_deg": round(_line_angle(start, end), 3),
                    "stroke_width": round(stroke_width, 3),
                    "dashes": str(drawing.get("dashes") or ""),
                    "filled_path": fill is not None,
                    "derived_from_filled_band": False,
                }
            )

    direction_segments = [
        segment
        for segment in segments
        if not segment["filled_path"] and segment["length"] >= 12.0 * distance_scale
    ]
    strong_process_segments = _strong_process_segments(segments)
    weld_anchor_candidates = _weld_anchor_candidates(
        strong_process_segments,
        marker_candidates,
        distance_scale=distance_scale,
    )
    flow_arrow_candidates = _flow_arrow_path_candidates(page, drawings, strong_process_segments)
    component_boundary_candidates = _component_boundary_candidates(
        page,
        drawings,
        strong_process_segments,
        {int(candidate["drawing_index"]) for candidate in flow_arrow_candidates},
    )
    component_boundary_candidates.extend(
        _component_gap_endpoint_candidates(
            {
                "strong_process_segments": strong_process_segments,
                "flow_arrow_candidates": flow_arrow_candidates,
            }
        )
    )
    component_boundary_candidates.extend(
        _long_inline_component_boundary_candidates(
            page,
            drawings,
            {
                "segments": segments,
                "strong_process_segments": strong_process_segments,
            },
            distance_scale=distance_scale,
        )
    )
    branch_root_candidates = _branch_root_intersection_candidates(
        {
            "segments": segments,
            "strong_process_segments": strong_process_segments,
        }
    )
    thin_main_candidates = _thin_main_axis_boundary_candidates(
        page, drawings, segments, branch_root_candidates
    )
    terminal_extension_candidates = _terminal_component_extension_candidates(
        page, drawings, strong_process_segments
    )
    continuation_terminal_candidates = _continuation_terminal_candidates(
        page, segments, width, height, distance_scale
    )
    branch_anchor_groups = _classify_branch_component_boundaries(
        component_boundary_candidates, branch_root_candidates
    )
    branch_anchor_groups.extend(
        _swapped_thin_branch_groups(branch_anchor_groups, segments)
    )
    # A branch classifier has enough route context to identify weak strokes
    # that sit between the socket and the first credible flange boundary.
    # Keep this as a hard semantic exclusion: later exhaustive topology search
    # must not reintroduce a known flow/annotation glyph merely to satisfy the
    # requested anchor count.
    component_boundary_candidates = [
        candidate
        for candidate in component_boundary_candidates
        if candidate.get("branch_role") != "interior-flow-or-annotation-symbol"
    ]
    component_boundary_candidates = _exclude_weak_interior_boundary_candidates(
        component_boundary_candidates, strong_process_segments
    )
    component_boundary_candidates.extend(branch_root_candidates)
    component_boundary_candidates.extend(thin_main_candidates)
    component_boundary_candidates.extend(continuation_terminal_candidates)
    compound_weld_symbol_candidates = _compound_weld_symbol_candidates(
        page, drawings, strong_process_segments
    )
    fixed_weld_symbol_candidates = _fixed_weld_symbol_candidates(
        {
            "compound_weld_symbol_candidates": compound_weld_symbol_candidates,
            "component_boundary_candidates": component_boundary_candidates,
        }
    )
    return {
        "source": source,
        "page_number": page_number if page_number is not None else page.number + 1,
        "pdf_rotation": int(page.rotation),
        "display_size": [round(width, 3), round(height, 3)],
        "vector_scale_profile": scale_profile.to_dict(),
        "drawing_count": len(drawings),
        "text_word_count": len(page.get_text("words")),
        "segments": segments,
        "direction_segment_count": len(direction_segments),
        "dominant_directions": _direction_peaks(direction_segments),
        "marker_candidates": marker_candidates,
        "strong_process_segments": strong_process_segments,
        "weld_anchor_candidates": weld_anchor_candidates,
        "component_boundary_candidates": component_boundary_candidates,
        "terminal_extension_candidates": terminal_extension_candidates,
        "continuation_terminal_candidates": continuation_terminal_candidates,
        "flow_arrow_candidates": flow_arrow_candidates,
        "branch_anchor_groups": branch_anchor_groups,
        "compound_weld_symbol_candidates": compound_weld_symbol_candidates,
        "fixed_weld_symbol_candidates": fixed_weld_symbol_candidates,
        "warnings": [
            "marker_candidates are raw small dark paths and still include vectorized glyphs",
            "weld_anchor_candidates use a PD02 design-ISO strongest-stroke rule that needs cross-project validation",
            "filled-band centreline recovery supports zero-width DWG-to-PDF process polygons",
            "compound inline flow-arrow paths are excluded before component-boundary extraction",
            "formal weld anchors require a complete 12-line compound glyph path",
        ],
    }


def analyze_pdf_page(path: str | Path, page_index: int = 0) -> dict[str, Any]:
    pdf_path = Path(path)
    with fitz.open(pdf_path) as document:
        if not 0 <= page_index < document.page_count:
            raise IndexError(f"PDF page index {page_index} outside 0..{document.page_count - 1}: {pdf_path}")
        return analyze_page(document[page_index], source=str(pdf_path), page_number=page_index + 1)


def page_summary(features: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in features.items() if key not in {"segments", "marker_candidates", "strong_process_segments", "weld_anchor_candidates", "component_boundary_candidates", "compound_weld_symbol_candidates", "fixed_weld_symbol_candidates", "terminal_extension_candidates", "branch_anchor_groups", "flow_arrow_candidates"}} | {
        "segment_count": len(features.get("segments", [])),
        "marker_candidate_count": len(features.get("marker_candidates", [])),
        "strong_process_segment_count": len(features.get("strong_process_segments", [])),
        "weld_anchor_candidate_count": len(features.get("weld_anchor_candidates", [])),
        "weld_anchor_candidates": features.get("weld_anchor_candidates", []),
        "component_boundary_candidate_count": len(features.get("component_boundary_candidates", [])),
        "terminal_extension_candidate_count": len(features.get("terminal_extension_candidates", [])),
        "flow_arrow_candidate_count": len(features.get("flow_arrow_candidates", [])),
        "flow_arrow_candidates": features.get("flow_arrow_candidates", []),
        "branch_anchor_group_count": len(features.get("branch_anchor_groups", [])),
        "fixed_weld_symbol_candidate_count": len(features.get("fixed_weld_symbol_candidates", [])),
        "compound_weld_symbol_candidate_count": len(features.get("compound_weld_symbol_candidates", [])),
        "page_role_candidate": page_role_candidate(features),
    }


def page_role_candidate(features: dict[str, Any]) -> str:
    """Separate likely piping sheets from BOM/table continuation sheets.

    This is deliberately conservative and only a candidate classification.
    The fixed drawing ROI excludes most material tables; a page with almost no
    remaining direction-bearing geometry is therefore unlikely to be a piping
    sheet.
    """

    direction_count = int(features.get("direction_segment_count", 0))
    marker_count = len(features.get("marker_candidates", []))
    return "piping" if direction_count >= 12 or marker_count >= 3 else "non-piping-continuation"


def write_diagnostic_svg(features: dict[str, Any], output_path: str | Path) -> None:
    width, height = features["display_size"]
    lines: list[str] = []
    for segment in features.get("segments", []):
        color_index = int(round(float(segment["angle_deg"]) / 22.5)) % len(DIRECTION_COLORS)
        color = DIRECTION_COLORS[color_index]
        opacity = 0.75 if segment["length"] >= 20 else 0.30
        stroke_width = max(0.45, min(float(segment["stroke_width"]), 2.2))
        lines.append(
            '<line x1="{:.3f}" y1="{:.3f}" x2="{:.3f}" y2="{:.3f}" '
            'stroke="{}" stroke-width="{:.3f}" opacity="{:.2f}" />'.format(
                *segment["start"], *segment["end"], color, stroke_width, opacity
            )
        )
    markers = [
        '<circle cx="{:.3f}" cy="{:.3f}" r="3" fill="none" stroke="#ff00aa" stroke-width="0.8" />'.format(
            *candidate["center"]
        )
        for candidate in features.get("marker_candidates", [])
    ]
    anchors = [
        '<circle cx="{:.3f}" cy="{:.3f}" r="5" fill="none" stroke="#0066ff" stroke-width="1.4" />'.format(
            *candidate["center"]
        )
        for candidate in features.get("weld_anchor_candidates", [])
    ]
    label = html.escape(f"{features.get('source', '')} page {features.get('page_number', '')}")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">\n'
        '<rect width="100%" height="100%" fill="white" />\n'
        f'<text x="12" y="20" font-family="sans-serif" font-size="10" fill="#222">{label}</text>\n'
        + "\n".join(lines + markers + anchors)
        + "\n</svg>\n"
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg, encoding="utf-8")
