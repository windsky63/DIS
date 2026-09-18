"""Validated project/reference adapters; contractor roots are annotation endpoints."""

from __future__ import annotations

import math
import re
from typing import Any

import fitz
import numpy as np

try:
    from .iso_weld_matcher.dual_pdf_topology import PdfWeldCallout
except ImportError:
    from iso_weld_matcher.dual_pdf_topology import PdfWeldCallout


DEFAULT_REFERENCE_RULES = [
    {"id": "ep3d", "name": "EP3D 出图", "kind": "ep3d", "options": {}},
    {"id": "contractor", "name": "施工单位出图", "kind": "contractor", "options": {}},
]
CONTRACTOR_DEFAULTS = {
    "labelPattern": r"[A-Z]*\d+(?:[-.]\d+)*", "maximumFrameGap": 24.0,
    "minimumFrameDiameter": 6.0, "maximumFrameDiameter": 72.0,
    "minimumLeaderLength": 4.0, "maximumLeaderLength": 250.0,
    "segmentGap": 3.0, "maximumLeaderSegments": 8,
    "leaderColor": "any", "redMinimum": 0.8, "otherColorMaximum": 0.3,
}


def normalize_reference_rules(config: dict[str, Any]) -> dict[str, Any]:
    supplied = config.get("referenceRules", DEFAULT_REFERENCE_RULES)
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= 64:
        raise ValueError("项目必须配置 1 至 64 个出图来源规则")
    rules, ids = [], set()
    for raw in supplied:
        if not isinstance(raw, dict):
            raise ValueError("出图规则必须为对象")
        rule_id = str(raw.get("id") or "").strip()
        kind = str(raw.get("kind") or "")
        if not rule_id or len(rule_id) > 100 or rule_id in ids or kind not in {"ep3d", "contractor"}:
            raise ValueError("出图规则 ID 必须唯一，且类型必须为 EP3D 或施工单位")
        ids.add(rule_id)
        if raw.get("options") is not None and not isinstance(raw["options"], dict):
            raise ValueError("出图规则参数必须为对象")
        options = {**(CONTRACTOR_DEFAULTS if kind == "contractor" else {}), **(raw.get("options") or {})}
        pattern = str(options.get("labelPattern") or (r"(?:F|FS|T)\d+" if kind == "ep3d" else CONTRACTOR_DEFAULTS["labelPattern"]))
        if len(pattern) > 120:
            raise ValueError("出图编号表达式不能超过 120 个字符")
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"出图编号表达式无效：{exc}") from exc
        options["labelPattern"] = pattern
        if kind == "contractor":
            bounds = {
                "maximumFrameGap": (0, 200), "minimumFrameDiameter": (1, 200),
                "maximumFrameDiameter": (1, 300), "minimumLeaderLength": (0.5, 300),
                "maximumLeaderLength": (1, 1000), "segmentGap": (0, 30),
                "maximumLeaderSegments": (1, 20), "redMinimum": (0, 1), "otherColorMaximum": (0, 1),
            }
            for key, (low, high) in bounds.items():
                value = float(options[key])
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"施工出图参数 {key} 必须在 {low} 至 {high} 之间")
                options[key] = value
            if options["minimumFrameDiameter"] > options["maximumFrameDiameter"] or options["minimumLeaderLength"] > options["maximumLeaderLength"]:
                raise ValueError("施工出图参数的最小值不能大于最大值")
            if options["leaderColor"] not in {"any", "red"}:
                raise ValueError("施工引线颜色必须为 any 或 red")
        elif options.get("rootStrategy", "solid-dot-required") not in {"solid-dot-required", "leader-end-on-process", "symbol-or-process-end"}:
            raise ValueError("EP3D 焊口端点策略无效")
        rules.append({"id": rule_id, "name": str(raw.get("name") or rule_id)[:100], "kind": kind, "options": options})
    active = str(config.get("activeReferenceRuleId") or rules[0]["id"])
    assignments = config.get("referenceRuleAssignments") or {}
    if active not in ids or not isinstance(assignments, dict) or any(str(value) not in ids for value in assignments.values()):
        raise ValueError("默认规则或文件指定的出图规则不存在")
    return {"referenceRules": rules, "activeReferenceRuleId": active,
            "referenceRuleAssignments": {str(key): str(value) for key, value in assignments.items()}}


def reference_rule_for_slot(config: dict[str, Any], slot: int) -> dict[str, Any]:
    rule_id = config["referenceRuleAssignments"].get(str(slot), config["activeReferenceRuleId"])
    return next(rule for rule in config["referenceRules"] if rule["id"] == rule_id)


def _display(page: fitz.Page, point: fitz.Point) -> tuple[float, float]:
    mapped = point * page.rotation_matrix if page.rotation else point
    return float(mapped.x), float(mapped.y)


def extract_contractor_callouts(page: fitz.Page, options: dict[str, Any]) -> list[PdfWeldCallout]:
    """Resolve red circle labels to possibly detached leader ends, without pipe gates."""
    options = {**CONTRACTOR_DEFAULTS, **options}
    pattern = re.compile(options["labelPattern"], re.IGNORECASE)
    frames, lines = [], []
    drawings = page.get_drawings()
    red = lambda color: color is not None and len(color) >= 3 and color[0] >= options["redMinimum"] and max(color[1:3]) <= options["otherColorMaximum"]
    for drawing_index, drawing in enumerate(drawings):
        items = drawing.get("items", [])
        curve_points = [_display(page, point) for item in items if item[0] == "c" for point in item[1:5]]
        linear_points = [_display(page, point) for item in items if item[0] == "l" for point in item[1:3]]
        points = curve_points or linear_points
        if red(drawing.get("color")) and points and (len(curve_points) >= 12 or len(linear_points) >= 24):
            coords = np.asarray(points)
            low, high = coords.min(axis=0), coords.max(axis=0)
            width, height = high - low
            if width and height and 0.8 <= width / height <= 1.25 and options["minimumFrameDiameter"] <= min(width, height) <= max(width, height) <= options["maximumFrameDiameter"]:
                # Cubic controls are not samples of the circle itself. For a
                # polyline, fit its vertices so rectangles cannot become frames.
                circular = bool(curve_points)
                if not circular:
                    center = (low + high) / 2
                    radii = np.linalg.norm(coords - center, axis=1)
                    circular = float(np.std(radii) / max(np.mean(radii), 1e-6)) < 0.08
                if circular:
                    frames.append({"bbox": tuple([*low, *high]), "center": tuple((low + high) / 2), "index": drawing_index})
        if drawing.get("fill") is not None or float(drawing.get("width") or 0) > 1.5:
            continue
        color = drawing.get("color")
        if options["leaderColor"] == "red" and not red(color):
            continue
        if color is None or (not red(color) and max(color[:3]) > 0.5):
            continue
        for item_index, item in enumerate(items):
            if item[0] != "l":
                continue
            start, end = _display(page, item[1]), _display(page, item[2])
            length = math.dist(start, end)
            if options["minimumLeaderLength"] <= length <= options["maximumLeaderLength"]:
                lines.append({"start": start, "end": end, "length": length,
                              "key": (drawing_index, item_index), "red": red(color)})

    def inside(point, frame):
        box, center = frame["bbox"], frame["center"]
        return ((point[0] - center[0]) / ((box[2] - box[0]) / 2)) ** 2 + ((point[1] - center[1]) / ((box[3] - box[1]) / 2)) ** 2 <= 1.15

    def distance(point, box):
        return math.hypot(max(box[0] - point[0], 0, point[0] - box[2]), max(box[1] - point[1], 0, point[1] - box[3]))

    words = [(str(word[4]), tuple(_display(page, fitz.Point(word[0], word[1])) + _display(page, fitz.Point(word[2], word[3])))) for word in page.get_text("words")]
    lines = [line for line in lines if not any(inside(line["start"], frame) and inside(line["end"], frame) for frame in frames)]
    used, found = set(), []
    for frame in frames:
        enclosed = [(text, box) for text, box in words if inside(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), frame)]
        label = "".join(text for text, _ in sorted(enclosed, key=lambda item: (round(item[1][1] / 4), item[1][0]))).strip().upper()
        if not label or not pattern.fullmatch(label):
            continue
        candidates = []
        for line in lines:
            if line["key"] in used:
                continue
            for near, far in ((line["start"], line["end"]), (line["end"], line["start"])):
                gap = distance(near, frame["bbox"])
                if gap <= options["maximumFrameGap"] and distance(far, frame["bbox"]) >= gap + line["length"] * 0.4:
                    candidates.append((gap, not line["red"], abs(line["key"][0] - frame["index"]), line, near, far))
        if not candidates:
            continue
        gap, _, _, first, near, endpoint = min(candidates, key=lambda item: item[:3])
        path = {first["key"]}
        previous = near
        for _ in range(int(options["maximumLeaderSegments"]) - 1):
            next_lines = []
            for line in lines:
                if line["key"] in used | path:
                    continue
                for attach, far in ((line["start"], line["end"]), (line["end"], line["start"])):
                    joint_gap = math.dist(endpoint, attach)
                    if joint_gap > options["segmentGap"] or any(inside(far, other) for other in frames):
                        continue
                    left = np.asarray(endpoint) - previous
                    right = np.asarray(far) - attach
                    cosine = float(np.dot(left, right) / max(np.linalg.norm(left) * np.linalg.norm(right), 1e-6))
                    if cosine >= -0.05:
                        next_lines.append((joint_gap, -cosine, line, far))
            if not next_lines:
                break
            _, _, line, far = min(next_lines, key=lambda item: item[:2])
            previous, endpoint = endpoint, far
            path.add(line["key"])
        used.update(path)
        found.append(PdfWeldCallout(
            label=label, label_bbox=frame["bbox"], label_center=frame["center"],
            weld_point=tuple(endpoint), leader_start=tuple(near), leader_end=tuple(endpoint),
            extraction_method="contractor-red-circle-detached-leader-authoritative-endpoint",
            extraction_confidence=round(max(0.65, 0.94 - gap / max(options["maximumFrameGap"], 1) * 0.15), 3),
        ))
    return found
