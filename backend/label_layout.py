"""Collision-aware marker layout shared by parsed jobs and tutorial generation."""

from __future__ import annotations

import math
from typing import Any, Callable

REGULAR_LEADER_MULTIPLIER = 12.0
EMERGENCY_LEADER_MULTIPLIER = 18.0
MAX_REPAIR_PASSES = 3
REOPTIMIZE_FRACTION = 0.2
LONG_PIPE_MIN_MARKERS = 4
LONG_PIPE_ANGLE_TOLERANCE = math.pi / 24
REFERENCE_DIAMETER = 56.7
FIXED_LENGTH_FACTORS = (1.25, 1.55, 1.9, 2.35, 2.9, 3.6, 4.5)
PERIMETER_LENGTH_FACTORS = tuple(value / REFERENCE_DIAMETER for value in (120, 150, 180, 220, 270, 330, 400, 500, 620))


class _SpatialIndex:
    def __init__(self, values: list[Any], bounds, cell_size: float = 100.0) -> None:
        self.values = values
        self.cell_size = cell_size
        self.cells: dict[tuple[int, int], list[int]] = {}
        for index, value in enumerate(values):
            left, top, right, bottom = bounds(value)
            for column in range(math.floor(left / cell_size), math.floor(right / cell_size) + 1):
                for row in range(math.floor(top / cell_size), math.floor(bottom / cell_size) + 1):
                    self.cells.setdefault((column, row), []).append(index)

    def __iter__(self):
        return iter(self.values)

    def query(self, left: float, top: float, right: float, bottom: float) -> list[Any]:
        indexes: set[int] = set()
        for column in range(math.floor(left / self.cell_size), math.floor(right / self.cell_size) + 1):
            for row in range(math.floor(top / self.cell_size), math.floor(bottom / self.cell_size) + 1):
                indexes.update(self.cells.get((column, row), ()))
        return [self.values[index] for index in indexes]


def _overlap(a: dict[str, float], b: dict[str, float], padding: float = 0) -> bool:
    return a["left"] < b["right"] + padding and a["right"] > b["left"] - padding and a["top"] < b["bottom"] + padding and a["bottom"] > b["top"] - padding


def _cross(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a, b) -> bool:
    return _cross(a[0], a[1], b[0]) * _cross(a[0], a[1], b[1]) < 0 and _cross(b[0], b[1], a[0]) * _cross(b[0], b[1], a[1]) < 0


def _segment_to_segment_distance(first, second) -> float:
    if _segments_intersect(first, second):
        return 0.0
    return min(
        _point_to_segment_distance(first[0], second),
        _point_to_segment_distance(first[1], second),
        _point_to_segment_distance(second[0], first),
        _point_to_segment_distance(second[1], first),
    )


def _trim_segment_start(segment, distance: float):
    length = math.hypot(segment[1][0] - segment[0][0], segment[1][1] - segment[0][1])
    if not length or length <= distance:
        return segment
    ratio = distance / length
    return ((segment[0][0] + (segment[1][0] - segment[0][0]) * ratio, segment[0][1] + (segment[1][1] - segment[0][1]) * ratio), segment[1])


def _segment_to_rectangle_distance(segment, rectangle) -> float:
    if _segment_intersects_rectangle(segment, rectangle):
        return 0.0
    corners = [(rectangle["left"], rectangle["top"]), (rectangle["right"], rectangle["top"]), (rectangle["right"], rectangle["bottom"]), (rectangle["left"], rectangle["bottom"])]
    return min(_segment_to_segment_distance(segment, (corners[index], corners[(index + 1) % 4])) for index in range(4))


def _segment_intersects_rectangle(segment, rectangle) -> bool:
    if any(rectangle["left"] <= p[0] <= rectangle["right"] and rectangle["top"] <= p[1] <= rectangle["bottom"] for p in segment):
        return True
    corners = [(rectangle["left"], rectangle["top"]), (rectangle["right"], rectangle["top"]), (rectangle["right"], rectangle["bottom"]), (rectangle["left"], rectangle["bottom"])]
    return any(_segments_intersect(segment, (corners[i], corners[(i + 1) % 4])) for i in range(4))


def _point_to_segment_distance(point, segment) -> float:
    dx, dy = segment[1][0] - segment[0][0], segment[1][1] - segment[0][1]
    length_squared = dx * dx + dy * dy
    if not length_squared:
        return math.hypot(point[0] - segment[0][0], point[1] - segment[0][1])
    ratio = max(0.0, min(1.0, ((point[0] - segment[0][0]) * dx + (point[1] - segment[0][1]) * dy) / length_squared))
    return math.hypot(point[0] - (segment[0][0] + ratio * dx), point[1] - (segment[0][1] + ratio * dy))


def _axis_angle(angle: float) -> float:
    normalized = math.atan2(math.sin(angle), math.cos(angle))
    if normalized < -math.pi / 2:
        normalized += math.pi
    if normalized >= math.pi / 2:
        normalized -= math.pi
    return normalized


def _axis_difference(first: float, second: float) -> float:
    difference = _angle_difference(first, second)
    return min(difference, abs(math.pi - difference))


def _family(angle: float) -> str:
    if abs(math.cos(angle)) > 0.999:
        return "horizontal"
    if abs(math.sin(angle)) > 0.999:
        return "vertical"
    return "diagonal"


def _leader_connection(anchor, center, width: float, height: float):
    dx, dy = anchor[0] - center[0], anchor[1] - center[1]
    if not dx and not dy:
        return center
    factors = []
    if abs(dx) > 1e-9:
        factors.append((width / 2) / abs(dx))
    if abs(dy) > 1e-9:
        factors.append((height / 2) / abs(dy))
    factor = min(factors)
    return center[0] + dx * factor, center[1] + dy * factor


def _style(item: dict[str, Any]) -> dict[str, Any]:
    # Keep the server-side collision geometry identical to the actual defaults
    # used by frontend/defaultMarkerAppearance.js.  Component labels are
    # intentionally smaller than weld callouts.
    result = (
        {"shape": "rectangle", "frameSize": 20, "fontSize": 12}
        if item.get("componentType")
        else {"shape": "circle", "frameSize": 28, "fontSize": 15}
    )
    result.update(item.get("defaultMarkerStyle") or {})
    result.update(item.get("markerStyle") or {})
    return result


def _dimensions(item: dict[str, Any]) -> tuple[float, float, float]:
    style = _style(item)
    size = float(style.get("frameSize") or 28)
    font_size = float(style.get("fontSize") or 15)
    width = max(size * 1.35, 10 + len(str(item.get("number") or "?")) * font_size * 0.68) if style.get("shape") == "rectangle" else size
    return width, size, max(width, size)


def _angle_difference(a: float, b: float) -> float:
    return abs(math.atan2(math.sin(a - b), math.cos(a - b)))


def _unique_angles(values: list[float]) -> list[float]:
    result: list[float] = []
    for value in values:
        if not any(_angle_difference(value, other) < 0.035 for other in result):
            result.append(value)
    return result


def _parse_obstacles(page: dict[str, Any]):
    obstacles = page.get("layoutObstacles") or {}
    text = [{"left": float(box[0]), "top": float(box[1]), "right": float(box[2]), "bottom": float(box[3])} for box in obstacles.get("textRects") or [] if len(box) >= 4]
    pipes = [((float(segment.get("start", [0, 0])[0]), float(segment.get("start", [0, 0])[1])), (float(segment.get("end", [0, 0])[0]), float(segment.get("end", [0, 0])[1]))) for segment in obstacles.get("processSegments") or []]
    graphics = [((float(segment.get("start", [0, 0])[0]), float(segment.get("start", [0, 0])[1])), (float(segment.get("end", [0, 0])[0]), float(segment.get("end", [0, 0])[1]))) for segment in obstacles.get("graphicSegments") or []]
    text_index = _SpatialIndex(text, lambda rectangle: (rectangle["left"], rectangle["top"], rectangle["right"], rectangle["bottom"]))
    graphic_index = _SpatialIndex(graphics, lambda segment: (
        min(segment[0][0], segment[1][0]), min(segment[0][1], segment[1][1]),
        max(segment[0][0], segment[1][0]), max(segment[0][1], segment[1][1]),
    ), cell_size=64.0)
    return text_index, pipes, graphic_index


def _ray_distance_to_page(x: float, y: float, dx: float, dy: float, width: float, height: float) -> float:
    distances = []
    if dx > 1e-6:
        distances.append((width - x) / dx)
    elif dx < -1e-6:
        distances.append(-x / dx)
    if dy > 1e-6:
        distances.append((height - y) / dy)
    elif dy < -1e-6:
        distances.append(-y / dy)
    return min((value for value in distances if value >= 0), default=0)


def _nearest_pipe(item, pipes):
    anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
    return min(((index, pipe, _point_to_segment_distance(anchor, pipe)) for index, pipe in enumerate(pipes)), key=lambda value: value[2], default=None)


def _build_long_pipe_group_map(items, pipes, page_width: float, page_height: float):
    candidates = []
    for index, item in enumerate(items):
        nearest = _nearest_pipe(item, pipes)
        _, _, frame = _dimensions(item)
        if not nearest or nearest[2] > max(18, frame * 0.9):
            continue
        pipe = nearest[1]
        angle = _axis_angle(math.atan2(pipe[1][1] - pipe[0][1], pipe[1][0] - pipe[0][0]))
        candidates.append({"item": item, "index": index, "x": float(item.get("x") or 0), "y": float(item.get("y") or 0), "axisAngle": angle})
    clusters: list[dict[str, Any]] = []
    for candidate in candidates:
        matching = None
        for cluster in clusters:
            if _axis_difference(cluster["axisAngle"], candidate["axisAngle"]) > LONG_PIPE_ANGLE_TOLERANCE:
                continue
            normal = (-math.sin(cluster["axisAngle"]), math.cos(cluster["axisAngle"]))
            offset = abs((candidate["x"] - cluster["anchorX"]) * normal[0] + (candidate["y"] - cluster["anchorY"]) * normal[1])
            if offset <= max(18, _dimensions(candidate["item"])[2] * 0.9):
                matching = cluster
                break
        if matching:
            matching["items"].append(candidate)
        else:
            clusters.append({"axisAngle": candidate["axisAngle"], "anchorX": candidate["x"], "anchorY": candidate["y"], "items": [candidate]})
    result: dict[int, dict[str, Any]] = {}
    group_index = 0
    for cluster in clusters:
        if len(cluster["items"]) < LONG_PIPE_MIN_MARKERS:
            continue
        tangent = (math.cos(cluster["axisAngle"]), math.sin(cluster["axisAngle"]))
        normal = (-tangent[1], tangent[0])
        ordered = sorted(cluster["items"], key=lambda item: item["x"] * tangent[0] + item["y"] * tangent[1])
        projections = [item["x"] * tangent[0] + item["y"] * tangent[1] for item in ordered]
        average_frame = sum(_dimensions(item["item"])[2] for item in ordered) / len(ordered)
        if projections[-1] - projections[0] < max(average_frame * 4, 80):
            continue
        center_x = sum(item["x"] for item in ordered) / len(ordered)
        center_y = sum(item["y"] for item in ordered) / len(ordered)
        positive_space = _ray_distance_to_page(center_x, center_y, normal[0], normal[1], page_width, page_height)
        negative_space = _ray_distance_to_page(center_x, center_y, -normal[0], -normal[1], page_width, page_height)
        preferred_side = 1 if positive_space >= negative_space else -1
        lane_ends = [float("-inf"), float("-inf")]
        for ordered_index, item in enumerate(ordered):
            frame = _dimensions(item["item"])[2]
            minimum_gap = frame + 2
            projection = projections[ordered_index]
            eligible = [lane for lane, end in enumerate(lane_ends) if projection - end >= minimum_gap]
            lane = eligible[0] if eligible else (0 if lane_ends[0] <= lane_ends[1] else 1)
            required_projection = max(projection, lane_ends[lane] + minimum_gap)
            lane_ends[lane] = required_projection
            result[id(item["item"])] = {
                "groupId": f"long-pipe-{group_index + 1}", "axisAngle": cluster["axisAngle"],
                "tangent": tangent, "normal": normal, "preferredSide": preferred_side,
                "lane": lane, "orderedIndex": ordered_index, "groupSize": len(ordered),
                "tangentShift": required_projection - projection,
            }
        group_index += 1
    return result


def _candidate_positions(item, page_width, page_height, pipes, placed, emergency=False, angles=None, distance_factors=None, source="relaxed-direction"):
    anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
    original = (float(item.get("labelX", anchor[0])), float(item.get("labelY", anchor[1])))
    width, height, frame = _dimensions(item)
    half_width, half_height = width / 2, height / 2
    base = math.hypot(half_width, half_height) + 12
    current_angle = math.atan2(original[1] - anchor[1], original[0] - anchor[0])
    nearest = min(((pipe, _point_to_segment_distance(anchor, pipe)) for pipe in pipes), key=lambda pair: pair[1], default=None)
    normal_angles: list[float] = []
    if nearest and nearest[1] < max(24, frame * 1.5):
        pipe_angle = math.atan2(nearest[0][1][1] - nearest[0][0][1], nearest[0][1][0] - nearest[0][0][0])
        normal_angles = [pipe_angle - math.pi / 2, pipe_angle + math.pi / 2]
    if angles is None:
        if emergency:
            angles = [-math.pi + index * 2 * math.pi / 72 for index in range(72)]
        else:
            horizontal = math.pi if anchor[0] < page_width / 2 else 0
            vertical = -math.pi / 2 if anchor[1] < page_height / 2 else math.pi / 2
            angles = [vertical + (-math.pi / 4 if horizontal == math.pi else math.pi / 4), vertical, horizontal, current_angle, *normal_angles, 0, math.pi / 2, math.pi, -math.pi / 2, math.pi / 4, 3 * math.pi / 4, -3 * math.pi / 4, -math.pi / 4]
    angles = _unique_angles(list(angles))
    maximum = frame * (EMERGENCY_LEADER_MULTIPLIER if emergency else REGULAR_LEADER_MULTIPLIER)
    factors = distance_factors or ((13.5, 15, 16.5, 18) if emergency else FIXED_LENGTH_FACTORS)
    distances = [frame * factor for factor in factors]
    distances = sorted({max(base, min(maximum, distance)) for distance in distances if distance <= maximum + 1e-6})
    tracks_x = sorted({round(entry["x"], 3) for entry in placed}, key=lambda value: abs(value - anchor[0]))[:4]
    tracks_y = sorted({round(entry["y"], 3) for entry in placed}, key=lambda value: abs(value - anchor[1]))[:4]
    track_grid = max(4.0, frame * 28 / REFERENCE_DIAMETER)
    seen: set[tuple[int, int]] = set()
    for distance in distances:
        for direction_index, angle in enumerate(angles):
            raw_x, raw_y = anchor[0] + math.cos(angle) * distance, anchor[1] + math.sin(angle) * distance
            direction_family = _family(angle)
            if direction_family == "horizontal":
                grid_y = round(raw_y / track_grid) * track_grid
                variants = [(raw_x, raw_y, 2), *((raw_x, track, 0) for track in tracks_y if abs(track - raw_y) <= track_grid * 2), (raw_x, grid_y, 1), (raw_x, grid_y - track_grid, 1), (raw_x, grid_y + track_grid, 1)]
            elif direction_family == "vertical":
                grid_x = round(raw_x / track_grid) * track_grid
                variants = [(raw_x, raw_y, 2), *((track, raw_y, 0) for track in tracks_x if abs(track - raw_x) <= track_grid * 2), (grid_x, raw_y, 1), (grid_x - track_grid, raw_y, 1), (grid_x + track_grid, raw_y, 1)]
            else:
                diagonal_grid = track_grid / 2
                variants = [(round(raw_x / diagonal_grid) * diagonal_grid, round(raw_y / diagonal_grid) * diagonal_grid, 1), (raw_x, raw_y, 2)]
            for candidate_x, candidate_y, source_penalty in variants:
                bounds = item.get("_layoutBounds") or {"left": 0, "top": 0, "right": page_width, "bottom": page_height}
                min_x, max_x = float(bounds["left"])+half_width+5, float(bounds["right"])-half_width-5
                min_y, max_y = float(bounds["top"])+half_height+5, float(bounds["bottom"])-half_height-5
                if min_x > max_x or min_y > max_y:
                    continue
                x, y = max(min_x, min(max_x, candidate_x)), max(min_y, min(max_y, candidate_y))
                actual_distance = math.hypot(x - anchor[0], y - anchor[1])
                if actual_distance > maximum + 1e-6:
                    continue
                key = (round(x * 10), round(y * 10))
                if key in seen:
                    continue
                seen.add(key)
                rectangle = {"left": x - half_width, "right": x + half_width, "top": y - half_height, "bottom": y + half_height}
                connection = _leader_connection(anchor, (x, y), width, height)
                yield {"x": x, "y": y, "rectangle": rectangle, "leader": (anchor, connection), "distance": actual_distance, "angle": angle, "direction": direction_index, "sourcePenalty": source_penalty, "currentAngle": current_angle, "source": source, "family": _family(angle), "reusedTrack": source_penalty == 0, "trackOffset": math.hypot(x - raw_x, y - raw_y), "pipeAngle": nearest and math.atan2(nearest[0][1][1] - nearest[0][0][1], nearest[0][1][0] - nearest[0][0][0])}


def _special_candidate(item, page_width, page_height, x, y, source, **meta):
    width, height, frame = _dimensions(item)
    anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
    half_width, half_height = width / 2, height / 2
    bounds = item.get("_layoutBounds") or {"left": 0, "top": 0, "right": page_width, "bottom": page_height}
    min_x, max_x = float(bounds["left"])+half_width+5, float(bounds["right"])-half_width-5
    min_y, max_y = float(bounds["top"])+half_height+5, float(bounds["bottom"])-half_height-5
    if min_x > max_x or min_y > max_y:
        return None
    x, y = max(min_x, min(max_x, x)), max(min_y, min(max_y, y))
    distance = math.hypot(x - anchor[0], y - anchor[1])
    if distance > frame * REGULAR_LEADER_MULTIPLIER + 1e-6:
        return None
    angle = math.atan2(y - anchor[1], x - anchor[0])
    candidate = {
        "x": x, "y": y, "rectangle": {"left": x - half_width, "right": x + half_width, "top": y - half_height, "bottom": y + half_height},
        "leader": (anchor, _leader_connection(anchor, (x, y), width, height)), "distance": distance,
        "angle": angle, "direction": int(meta.pop("rank", 0)), "sourcePenalty": 0,
        "currentAngle": angle, "source": source, "family": _family(angle), "reusedTrack": False, "trackOffset": 0,
    }
    candidate.update(meta)
    return candidate


def _local_pipe_band_candidates(item, page_width, page_height, pipe):
    _, _, frame = _dimensions(item)
    anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
    angle = math.atan2(pipe[1][1] - pipe[0][1], pipe[1][0] - pipe[0][0])
    tangent, normal = (math.cos(angle), math.sin(angle)), (-math.sin(angle), math.cos(angle))
    rank = 0
    for band_factor in (1.35, 1.65, 2.0, 2.4, 2.9, 3.5, 4.25):
        for shift_factor in (0, -0.56, 0.56, -1.13, 1.13, -1.7, 1.7, -2.25, 2.25, -3.1, 3.1, -3.95, 3.95):
            for side in (-1, 1):
                x = anchor[0] + normal[0] * side * frame * band_factor + tangent[0] * frame * shift_factor
                y = anchor[1] + normal[1] * side * frame * band_factor + tangent[1] * frame * shift_factor
                candidate = _special_candidate(item, page_width, page_height, x, y, "local-pipe-band", rank=rank, bandDistance=frame * band_factor, tangentShift=frame * shift_factor, bandSide=side, pipeAngle=angle, perpendicularDeviation=math.atan2(abs(shift_factor), band_factor))
                rank += 1
                if candidate:
                    yield candidate


def _long_pipe_candidates(item, page_width, page_height, group):
    _, _, frame = _dimensions(item)
    anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
    lane_base = 2.03 + group["lane"] * 1.13
    distances = sorted({1.35, 1.65, lane_base, lane_base + 0.42, lane_base + 0.88, 3.5, 4.25})
    base_shift = group["tangentShift"] / max(1, frame)
    shifts = list(dict.fromkeys((base_shift, 0, base_shift - 0.25, base_shift + 0.25, base_shift - 0.5, base_shift + 0.5)))
    rank = 0
    for side in (group["preferredSide"], -group["preferredSide"]):
        for distance in distances:
            for shift in shifts:
                x = anchor[0] + group["normal"][0] * side * frame * distance + group["tangent"][0] * frame * shift
                y = anchor[1] + group["normal"][1] * side * frame * distance + group["tangent"][1] * frame * shift
                candidate = _special_candidate(item, page_width, page_height, x, y, "long-pipe-group", rank=rank, bandDistance=frame * distance, tangentShift=frame * shift, longPipeGroupId=group["groupId"], longPipeLane=group["lane"], longPipeSide=side, preferredLongPipeSide=group["preferredSide"], pipeAngle=group["axisAngle"], perpendicularDeviation=math.atan2(abs(shift), distance))
                rank += 1
                if candidate:
                    yield candidate


def _perimeter_candidates(item, page_width, page_height, placed, focus_bounds):
    anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
    center = ((focus_bounds[0] + focus_bounds[2]) / 2, (focus_bounds[1] + focus_bounds[3]) / 2)
    radial = math.atan2(anchor[1] - center[1], anchor[0] - center[0])
    spoke_angles = [radial, radial - math.pi / 8, radial + math.pi / 8, radial - math.pi / 4, radial + math.pi / 4]
    yield from _candidate_positions(item, page_width, page_height, [], placed, angles=spoke_angles, distance_factors=PERIMETER_LENGTH_FACTORS, source="perimeter")


def _evaluate(candidate, item, entries, anchors, text_rectangles, pipes, graphics):
    rectangle, leader = candidate["rectangle"], candidate["leader"]
    label = anchor = leader_label = leader_cross = leader_anchor = 0
    _, _, frame = _dimensions(item)
    leader_label_gap = max(2.0, frame * 8 / REFERENCE_DIAMETER)
    leader_leader_gap = max(3.0, frame * 18 / REFERENCE_DIAMETER)
    trimmed_leader = _trim_segment_start(leader, frame * 20 / REFERENCE_DIAMETER)
    for entry in entries:
        if entry["id"] == item.get("id"):
            continue
        label += _overlap(rectangle, entry["rectangle"], 2)
        leader_label += _segment_to_rectangle_distance(leader, entry["rectangle"]) < leader_label_gap
        leader_label += _segment_to_rectangle_distance(entry["leader"], rectangle) < leader_label_gap
        other_trimmed = _trim_segment_start(entry["leader"], frame * 20 / REFERENCE_DIAMETER)
        leader_cross += _segments_intersect(leader, entry["leader"]) or _segment_to_segment_distance(trimmed_leader, other_trimmed) < leader_leader_gap
    for point_x, point_y, point_id in anchors:
        if point_id == item.get("id"):
            continue
        anchor += rectangle["left"] - 7 < point_x < rectangle["right"] + 7 and rectangle["top"] - 7 < point_y < rectangle["bottom"] + 7
        # Keep the line away from another anchor without making a dense anchor
        # cluster geometrically impossible to leave.
        leader_anchor += _point_to_segment_distance((point_x, point_y), leader) < 4
    nearby_text = text_rectangles.query(
        min(rectangle["left"], leader[0][0], leader[1][0]) - 1,
        min(rectangle["top"], leader[0][1], leader[1][1]) - 1,
        max(rectangle["right"], leader[0][0], leader[1][0]) + 1,
        max(rectangle["bottom"], leader[0][1], leader[1][1]) + 1,
    )
    text = sum(_overlap(rectangle, obstacle, leader_label_gap) for obstacle in nearby_text)
    pipe = sum(_segment_intersects_rectangle(segment, {"left": rectangle["left"] - 2, "right": rectangle["right"] + 2, "top": rectangle["top"] - 2, "bottom": rectangle["bottom"] + 2}) for segment in pipes)
    expanded_graphic = {"left": rectangle["left"] - 2, "right": rectangle["right"] + 2, "top": rectangle["top"] - 2, "bottom": rectangle["bottom"] + 2}
    nearby_graphics = graphics.query(
        min(expanded_graphic["left"], leader[0][0], leader[1][0]) - 6,
        min(expanded_graphic["top"], leader[0][1], leader[1][1]) - 6,
        max(expanded_graphic["right"], leader[0][0], leader[1][0]) + 6,
        max(expanded_graphic["bottom"], leader[0][1], leader[1][1]) + 6,
    )
    graphic = sum(_segment_intersects_rectangle(segment, expanded_graphic) for segment in nearby_graphics)
    graphic_clutter = sum(_segment_to_rectangle_distance(segment, rectangle) < 7 for segment in nearby_graphics)
    hard_groups = (label + anchor + text + pipe + graphic, leader_label + leader_cross)
    pipe_clearance = sum(_point_to_segment_distance((candidate["x"], candidate["y"]), segment) < max(8, (rectangle["bottom"] - rectangle["top"]) / 2 + 6) for segment in pipes)
    leader_text = sum(_segment_intersects_rectangle(leader, obstacle) for obstacle in nearby_text)
    leader_pipe_proximity = sum(_segment_to_segment_distance(trimmed_leader, segment) < max(3, frame * 10 / REFERENCE_DIAMETER) for segment in pipes)
    leader_graphic = sum(_segments_intersect(trimmed_leader, segment) for segment in nearby_graphics)
    source_penalty = {"long-pipe-group": -35, "local-pipe-band": -22, "segment-normal": -15, "nearby-free-space": -5, "perimeter": -8, "relaxed-direction": 0, "non-crossing-emergency": 40}.get(candidate.get("source"), 0)
    soft = candidate["distance"] + _angle_difference(candidate["angle"], candidate["currentAngle"]) * 5 + pipe_clearance * 25 + leader_pipe_proximity * 12 + leader_anchor * 12 + leader_text * 8 + leader_graphic * 3 + graphic_clutter * 10 + candidate["sourcePenalty"] + abs(candidate.get("trackOffset", 0)) * 2.4 + (0 if candidate.get("reusedTrack") else 3) + source_penalty + candidate["direction"] * 0.001
    hard_summary = {
        "leaderIntersection": leader_cross, "leaderLeader": leader_cross,
        "leaderLabel": leader_label, "labelLabel": label + anchor,
        "labelText": text, "labelGraphic": pipe + graphic, "pageBounds": 0,
        "collisionViolationCount": label + anchor + leader_label + leader_cross,
        "constraintViolationCount": text + pipe + graphic,
    }
    hard_summary["total"] = hard_summary["collisionViolationCount"] + hard_summary["constraintViolationCount"]
    return {
        "hard": hard_groups,
        "hardCount": sum(hard_groups),
        "hardDetail": {"label": label, "anchor": anchor, "text": text, "pipe": pipe, "graphic": graphic, "leaderLabel": leader_label, "leaderCross": leader_cross, "leaderAnchor": leader_anchor},
        "hardCollisionSummary": hard_summary,
        "collisionViolationCount": hard_summary["collisionViolationCount"],
        "constraintViolationCount": hard_summary["constraintViolationCount"],
        "visualClutter": graphic_clutter,
        "soft": soft,
    }


def _evaluate_candidates(candidates, item, placed, anchors, text_rectangles, pipes, graphics):
    result = []
    seen = set()
    for candidate in candidates:
        key = (round(candidate["x"], 1), round(candidate["y"], 1))
        if key in seen:
            continue
        seen.add(key)
        candidate.update(_evaluate(candidate, item, placed, anchors, text_rectangles, pipes, graphics))
        result.append(candidate)
    return result


def _choose_collision_free(candidates):
    collision_free = [candidate for candidate in candidates if candidate["hardCount"] == 0]
    return min(collision_free, key=lambda candidate: (candidate.get("visualClutter", 0), round(candidate["distance"], 6), candidate["soft"], candidate["direction"]), default=None)


def _choose_fallback(candidates):
    def key(candidate):
        summary = candidate["hardCollisionSummary"]
        circle = summary["labelGraphic"] + summary["labelText"] + summary["labelLabel"]
        return (summary["pageBounds"], circle, summary["leaderLabel"], summary["leaderLeader"], candidate["soft"], candidate["distance"], candidate["direction"])
    non_crossing = [candidate for candidate in candidates if not candidate["hardCollisionSummary"]["leaderIntersection"]]
    return min(non_crossing or candidates, key=key, default=None)


def _mark_selection(candidate, stage: str, fallback: bool = False):
    if candidate:
        candidate.update({"selectionStage": stage, "usedFallback": fallback, "isCollisionFree": candidate["hardCount"] == 0})
    return candidate


def _best_position(item, page_width, page_height, pipes, placed, anchors, text_rectangles, graphics, group=None, use_perimeter=False, focus_bounds=None):
    nearest = _nearest_pipe(item, pipes)
    all_evaluated = []
    if group:
        evaluated = _evaluate_candidates(_long_pipe_candidates(item, page_width, page_height, group), item, placed, anchors, text_rectangles, pipes, graphics)
        all_evaluated.extend(evaluated)
        compact = [candidate for candidate in evaluated if candidate.get("bandDistance", 0) <= _dimensions(item)[2] * 3.2 and candidate["distance"] <= _dimensions(item)[2] * 3.5]
        best = _choose_collision_free(compact)
        if best:
            return _mark_selection(best, "long-pipe-group")
    if use_perimeter and nearest:
        local = _evaluate_candidates(_local_pipe_band_candidates(item, page_width, page_height, nearest[1]), item, placed, anchors, text_rectangles, pipes, graphics)
        all_evaluated.extend(local)
        frame = _dimensions(item)[2]
        compact = [candidate for candidate in local if candidate.get("bandDistance", 0) <= frame * 3.2 and abs(candidate.get("tangentShift", 0)) <= frame * 1.7]
        if best := _choose_collision_free(compact):
            return _mark_selection(best, "local-pipe-band-compact")
        # A normal-only band can miss a nearby diagonal pocket of whitespace.
        # Sample a denser ring before accepting an extended / long leader.
        free_angles = [-math.pi + index * 2 * math.pi / 24 for index in range(24)]
        nearby = _evaluate_candidates(_candidate_positions(item, page_width, page_height, [], placed, angles=free_angles, source="nearby-free-space"), item, placed, anchors, text_rectangles, pipes, graphics)
        all_evaluated.extend(nearby)
        if best := _choose_collision_free([*local, *nearby]):
            return _mark_selection(best, "nearby-free-space" if best.get("source") == "nearby-free-space" else "local-pipe-band")
    if use_perimeter and focus_bounds:
        perimeter = _evaluate_candidates(_perimeter_candidates(item, page_width, page_height, placed, focus_bounds), item, placed, anchors, text_rectangles, pipes, graphics)
        all_evaluated.extend(perimeter)
        if best := _choose_collision_free(perimeter):
            return _mark_selection(best, "perimeter-distribution")
    if not use_perimeter:
        if nearest and nearest[2] < max(24, _dimensions(item)[2] * 1.5):
            pipe_angle = math.atan2(nearest[1][1][1] - nearest[1][0][1], nearest[1][1][0] - nearest[1][0][0])
            strict = _evaluate_candidates(_candidate_positions(item, page_width, page_height, pipes, placed, angles=[pipe_angle - math.pi / 2, pipe_angle + math.pi / 2], source="segment-normal"), item, placed, anchors, text_rectangles, pipes, graphics)
            all_evaluated.extend(strict)
            if best := _choose_collision_free(strict):
                return _mark_selection(best, "strict-perpendicular")
        relaxed = _evaluate_candidates(_candidate_positions(item, page_width, page_height, [], placed, source="relaxed-direction"), item, placed, anchors, text_rectangles, pipes, graphics)
        all_evaluated.extend(relaxed)
        if best := _choose_collision_free(relaxed):
            return _mark_selection(best, "relaxed-direction")
    emergency = _evaluate_candidates(_candidate_positions(item, page_width, page_height, pipes, placed, emergency=True, source="non-crossing-emergency"), item, placed, anchors, text_rectangles, pipes, graphics)
    all_evaluated.extend(emergency)
    if best := _choose_collision_free(emergency):
        return _mark_selection(best, "non-crossing-emergency")
    return _mark_selection(_choose_fallback(all_evaluated), "fallback-best-effort", True)


def _candidate_at(item, x: float, y: float):
    width, height, frame = _dimensions(item)
    anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
    bounds = item.get("_layoutBounds")
    if bounds and not (float(bounds["left"])+width/2+5 <= x <= float(bounds["right"])-width/2-5 and float(bounds["top"])+height/2+5 <= y <= float(bounds["bottom"])-height/2-5):
        return None
    distance = math.hypot(x - anchor[0], y - anchor[1])
    if distance > frame * EMERGENCY_LEADER_MULTIPLIER + 1e-6:
        return None
    angle = math.atan2(y - anchor[1], x - anchor[0])
    return {
        "x": x, "y": y,
        "rectangle": {"left": x - width / 2, "right": x + width / 2, "top": y - height / 2, "bottom": y + height / 2},
        "leader": (anchor, _leader_connection(anchor, (x, y), width, height)), "distance": distance, "angle": angle,
        "currentAngle": angle, "sourcePenalty": 0, "direction": 0, "source": "endpoint-swap",
        "family": _family(angle), "reusedTrack": False, "trackOffset": 0,
    }


def _untangle_crossed_leaders(entries, anchors, text_rectangles, pipes, graphics) -> bool:
    """Swap compatible label endpoints when two straight leaders cross."""
    changed = False
    for first_index in range(len(entries)):
        for second_index in range(first_index + 1, len(entries)):
            first, second = entries[first_index], entries[second_index]
            if not _segments_intersect(first["leader"], second["leader"]):
                continue
            swapped_first = _candidate_at(first["item"], second["x"], second["y"])
            swapped_second = _candidate_at(second["item"], first["x"], first["y"])
            if not swapped_first or not swapped_second:
                continue
            swapped_first.update({"id": first["id"], "item": first["item"]})
            swapped_second.update({"id": second["id"], "item": second["item"]})
            others = [entry for index, entry in enumerate(entries) if index not in {first_index, second_index}]
            old_states = [_evaluate(first, first["item"], entries, anchors, text_rectangles, pipes, graphics), _evaluate(second, second["item"], entries, anchors, text_rectangles, pipes, graphics)]
            new_states = [_evaluate(swapped_first, first["item"], [*others, swapped_second], anchors, text_rectangles, pipes, graphics), _evaluate(swapped_second, second["item"], [*others, swapped_first], anchors, text_rectangles, pipes, graphics)]
            old_key = (sum(state["hard"][0] for state in old_states), sum(state["hard"][1] for state in old_states), sum(state["soft"] for state in old_states))
            new_key = (sum(state["hard"][0] for state in new_states), sum(state["hard"][1] for state in new_states), sum(state["soft"] for state in new_states))
            if new_key < old_key:
                swapped_first.update(new_states[0])
                swapped_second.update(new_states[1])
                swapped_first.update({"selectionStage": "endpoint-swap", "usedFallback": False, "isCollisionFree": new_states[0]["hardCount"] == 0})
                swapped_second.update({"selectionStage": "endpoint-swap", "usedFallback": False, "isCollisionFree": new_states[1]["hardCount"] == 0})
                entries[first_index], entries[second_index] = swapped_first, swapped_second
                changed = True
    return changed


def _congestion(item, anchors, text_rectangles, pipes) -> float:
    anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
    near_anchors = sum(point[2] != item.get("id") and math.hypot(point[0] - anchor[0], point[1] - anchor[1]) < 120 for point in anchors)
    near_text = sum(obstacle["left"] - 70 < anchor[0] < obstacle["right"] + 70 and obstacle["top"] - 70 < anchor[1] < obstacle["bottom"] + 70 for obstacle in text_rectangles)
    near_pipes = sum(_point_to_segment_distance(anchor, pipe) < 50 for pipe in pipes)
    return near_anchors * 8 + near_text * 3 + near_pipes


def reflow_label_positions(
    pages: list[dict[str, Any]],
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    totals = {"moved": 0, "placed": 0, "remainingCollisions": 0, "repairPasses": 0}
    page_list = pages or []
    for page_index, page in enumerate(page_list):
        items = [item for item in page.get("candidates") or [] if item.get("included", True) is not False]
        anchors = [(float(item.get("x") or 0), float(item.get("y") or 0), item.get("id")) for item in items]
        text_rectangles, pipes, graphics = _parse_obstacles(page)
        page_width, page_height = float(page.get("width") or 1), float(page.get("height") or 1)
        raw_region = (page.get("layoutObstacles") or {}).get("mainGraphicRegion") or {}
        layout_bounds = {"left": max(0.0, float(raw_region.get("left", 0))), "top": max(0.0, float(raw_region.get("top", 0))),
                         "right": min(page_width, float(raw_region.get("right", page_width))), "bottom": min(page_height, float(raw_region.get("bottom", page_height)))}
        for item in items:
            item["_layoutBounds"] = layout_bounds
        focus_bounds = (
            min((point[0] for point in anchors), default=0), min((point[1] for point in anchors), default=0),
            max((point[0] for point in anchors), default=0), max((point[1] for point in anchors), default=0),
        )
        use_perimeter = len(items) >= 8 and (focus_bounds[2] - focus_bounds[0] >= 160 or focus_bounds[3] - focus_bounds[1] >= 160)
        long_pipe_groups = _build_long_pipe_group_map(items, pipes, page_width, page_height)
        originals = {id(item): (float(item.get("labelX", item.get("x") or 0)), float(item.get("labelY", item.get("y") or 0))) for item in items}
        items.sort(key=lambda item: (-_congestion(item, anchors, text_rectangles, pipes), float(item.get("y") or 0), float(item.get("x") or 0)))
        entries: list[dict[str, Any]] = []
        for item in items:
            best = _best_position(item, page_width, page_height, pipes, entries, anchors, text_rectangles, graphics, long_pipe_groups.get(id(item)), use_perimeter, focus_bounds)
            if best:
                best.update({"id": item.get("id"), "item": item})
                entries.append(best)
        for repair_index in range(MAX_REPAIR_PASSES):
            untangled = _untangle_crossed_leaders(entries, anchors, text_rectangles, pipes, graphics)
            conflicted = [(state["hardCount"], entry) for entry in entries if (state := _evaluate(entry, entry["item"], entries, anchors, text_rectangles, pipes, graphics))["hardCount"]]
            if not conflicted:
                break
            totals["repairPasses"] = max(totals["repairPasses"], repair_index + 1)
            changed = untangled
            repair_count = max(1, math.ceil(len(entries) * REOPTIMIZE_FRACTION))
            for _, entry in sorted(conflicted, key=lambda pair: (-pair[0], -pair[1].get("soft", 0)))[:repair_count]:
                others = [other for other in entries if other is not entry]
                replacement = _best_position(entry["item"], page_width, page_height, pipes, others, anchors, text_rectangles, graphics, long_pipe_groups.get(id(entry["item"])), use_perimeter, focus_bounds)
                old_state = _evaluate(entry, entry["item"], others, anchors, text_rectangles, pipes, graphics)
                if replacement and (replacement["hard"], replacement["soft"]) < (old_state["hard"], old_state["soft"]):
                    replacement.update({"id": entry["id"], "item": entry["item"]})
                    entries[entries.index(entry)] = replacement
                    changed = True
            if not changed:
                break
        # Joint neighbourhood compaction: after greedy placement, revisit long
        # leaders with all neighbours present. Accept only lexicographically
        # better hard-collision / length / soft-score states.
        for _ in range(2):
            changed = False
            for entry in sorted(list(entries), key=lambda value: value["distance"], reverse=True):
                others = [other for other in entries if other is not entry]
                replacement = _best_position(entry["item"], page_width, page_height, pipes, others, anchors, text_rectangles, graphics, long_pipe_groups.get(id(entry["item"])), use_perimeter, focus_bounds)
                if not replacement:
                    continue
                old_state = _evaluate(entry, entry["item"], others, anchors, text_rectangles, pipes, graphics)
                if (replacement["hardCount"], replacement.get("visualClutter", 0), round(replacement["distance"], 6), replacement["soft"]) < (old_state["hardCount"], old_state.get("visualClutter", 0), round(entry["distance"], 6), old_state["soft"]):
                    replacement.update({"id": entry["id"], "item": entry["item"], "selectionStage": f"neighbourhood-{replacement.get('selectionStage', 'compact')}"})
                    entries[entries.index(entry)] = replacement
                    changed = True
            if not changed:
                break
        for entry in entries:
            item = entry["item"]
            final_state = _evaluate(entry, item, entries, anchors, text_rectangles, pipes, graphics)
            item.update({
                "labelX": entry["x"], "labelY": entry["y"], "labelXNorm": entry["x"] / page_width, "labelYNorm": entry["y"] / page_height,
                "layoutDiagnostics": {
                    "family": entry.get("family", ""), "source": entry.get("source", ""), "lineLength": entry["distance"],
                    "selectionStage": entry.get("selectionStage", "fallback-best-effort"), "usedFallback": entry.get("usedFallback", False),
                    "isCollisionFree": final_state["hardCount"] == 0, "hardCollisionCount": final_state["hardCount"],
                    "leaderIntersectionCount": final_state["hardCollisionSummary"]["leaderIntersection"],
                    "collisionViolationCount": final_state["collisionViolationCount"], "constraintViolationCount": final_state["constraintViolationCount"],
                    "hardCollisionSummary": final_state["hardCollisionSummary"], "penalty": final_state["soft"],
                    "reusedTrack": entry.get("reusedTrack", False), "trackOffset": entry.get("trackOffset", 0),
                    "longPipeGroupId": entry.get("longPipeGroupId", ""), "longPipeLane": entry.get("longPipeLane", -1),
                    "pipeAngleDegrees": round(math.degrees(entry["pipeAngle"]), 1) if isinstance(entry.get("pipeAngle"), (float, int)) else None,
                    "perpendicularDeviationDegrees": round(math.degrees(entry["perpendicularDeviation"]), 1) if isinstance(entry.get("perpendicularDeviation"), (float, int)) else None,
                },
            })
            totals["placed"] += 1
            if math.hypot(entry["x"] - originals[id(item)][0], entry["y"] - originals[id(item)][1]) > 2:
                totals["moved"] += 1
            totals["remainingCollisions"] += final_state["hardCount"]
            item.pop("_layoutBounds", None)
        if progress_callback:
            progress_callback(page_index + 1, len(page_list))
    return totals


def optimize_result_label_positions(
    result: dict[str, Any],
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    result.pop("labelLayout", None)
    return reflow_label_positions(result.get("pages") or [], progress_callback=progress_callback)


def inspect_result_label_positions(result: dict[str, Any]) -> dict[str, Any]:
    """Measure a saved layout without changing marker coordinates."""
    page_collisions: list[int] = []
    collision_details: list[dict[str, Any]] = []
    maximum_multiplier = 0.0
    for page in result.get("pages") or []:
        items = [item for item in page.get("candidates") or [] if item.get("included", True) is not False]
        anchors = [(float(item.get("x") or 0), float(item.get("y") or 0), item.get("id")) for item in items]
        text_rectangles, pipes, graphics = _parse_obstacles(page)
        entries = []
        for item in items:
            width, height, frame = _dimensions(item)
            anchor = (float(item.get("x") or 0), float(item.get("y") or 0))
            x, y = float(item.get("labelX", anchor[0])), float(item.get("labelY", anchor[1]))
            distance = math.hypot(x - anchor[0], y - anchor[1])
            maximum_multiplier = max(maximum_multiplier, distance / max(1, frame))
            entries.append({
                "id": item.get("id"), "item": item, "x": x, "y": y,
                "rectangle": {"left": x - width / 2, "right": x + width / 2, "top": y - height / 2, "bottom": y + height / 2},
                "leader": (anchor, _leader_connection(anchor, (x, y), width, height)), "distance": distance, "angle": 0,
                "currentAngle": 0, "sourcePenalty": 0, "direction": 0, "source": "saved-layout", "reusedTrack": False, "trackOffset": 0,
            })
        page_total = 0
        for entry in entries:
            state = _evaluate(entry, entry["item"], entries, anchors, text_rectangles, pipes, graphics)
            page_total += state["hardCount"]
            if state["hardCount"]:
                collision_details.append({"page": page.get("page"), "id": entry["id"], "hard": state["hard"], "detail": state["hardDetail"]})
        page_collisions.append(page_total)
    return {
        "remainingCollisions": sum(page_collisions),
        "pageCollisions": page_collisions,
        "collisionDetails": collision_details,
        "maxLeaderMultiplier": maximum_multiplier,
    }
