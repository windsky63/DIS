"""Adapter around the existing idf-pipe-viewer topology implementation."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import copy
import os
import re
import sys
from collections import Counter
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Any


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED_PARSER_ROOT = _BACKEND_ROOT / "vendor" / "idf-pipe-viewer" / "scripts"
DEFAULT_REFERENCE_PARSER = Path(
    os.environ.get("IDF_REFERENCE_PARSER", _BUNDLED_PARSER_ROOT / "parse_idf_to_json.py")
)
DEFAULT_REFERENCE_PCF_PARSER = Path(
    os.environ.get("PCF_REFERENCE_PARSER", _BUNDLED_PARSER_ROOT / "parse_pcf_to_json.py")
)


def parser_availability() -> dict[str, dict[str, object]]:
    """Expose parser configuration without depending on a developer workstation path."""

    return {
        "idf": {
            "path": str(DEFAULT_REFERENCE_PARSER.resolve()),
            "available": DEFAULT_REFERENCE_PARSER.is_file(),
            "environmentVariable": "IDF_REFERENCE_PARSER",
        },
        "pcf": {
            "path": str(DEFAULT_REFERENCE_PCF_PARSER.resolve()),
            "available": DEFAULT_REFERENCE_PCF_PARSER.is_file(),
            "environmentVariable": "PCF_REFERENCE_PARSER",
        },
    }


WELDABLE_FLANGE_NEIGHBOUR_TYPES = {
    "pipe",
    "elbow",
    "branch",
    "olet",
    "reducer",
    "teed-reducer",
    "teed-elbow",
    "misc-component",
}


IDF_EXTERNAL_REFERENCE_KIND = {
    -30: "external-pipeline",
    -31: "equipment-nozzle",
}
IDF_REFERENCE_ROUTE_IDENTIFIERS = {
    35,
    36,
    40,
    41,
    42,
    45,
    46,
    47,
    55,
    60,
    61,
    62,
    70,
    71,
    72,
    75,
    76,
    80,
    81,
    82,
    85,
    86,
    87,
    88,
    90,
    91,
    92,
    93,
    95,
    96,
    100,
    105,
    120,
    125,
    132,
    133,
    136,
    137,
}


def _idf_ascii_text(path: Path) -> str:
    """Decode fixed-width IDF while preserving its ASCII identity fields."""

    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("latin-1", errors="replace")


def _idf_numeric_component_row(
    raw_line: str,
) -> tuple[int, list[float], list[float]] | None:
    tokens = raw_line.strip().split()
    if len(tokens) < 7 or not re.fullmatch(r"-?\d+", tokens[0]):
        return None
    try:
        identifier = int(tokens[0])
        start = [float(value) for value in tokens[1:4]]
        end = [float(value) for value in tokens[4:7]]
    except ValueError:
        return None
    return identifier, start, end


def _extract_idf_external_references(path: Path) -> list[dict[str, Any]]:
    """Recover ``-30`` pipeline and ``-31`` equipment/nozzle references.

    ISOGEN splits long identity strings over consecutive ``-1`` records.  The
    reference applies to the first following real route component.  Drawing
    symbols such as flow arrows and supports are skipped so a header-level
    reference resolves to the actual first pipe endpoint.
    """

    lines = _idf_ascii_text(path).splitlines()
    offsets: list[list[float]] = []
    current_offset = [0.0, 0.0, 0.0]
    for raw_line in lines:
        offsets.append(list(current_offset))
        row = _idf_numeric_component_row(raw_line)
        if row is None or row[0] != 300:
            continue
        values = row[1] if any(abs(value) > 1.0e-9 for value in row[1]) else row[2]
        current_offset = [float(value) * 100000.0 for value in values]

    references = []
    for index, raw_line in enumerate(lines):
        match = re.match(r"^\s*(-3[01])(?:\s+(.*?))?\s*$", raw_line)
        if not match:
            continue
        record_id = int(match.group(1))
        fragments = [str(match.group(2) or "").strip()]
        cursor = index + 1
        while cursor < len(lines):
            continuation = re.match(r"^\s*-1(?:\s+(.*?))?\s*$", lines[cursor])
            if not continuation:
                break
            fragments.append(str(continuation.group(1) or "").strip())
            cursor += 1
        identity = "".join(fragments).replace(" ", "").upper()
        if not identity:
            continue
        anchor_row = None
        anchor_index = None
        scan = cursor
        while scan < len(lines):
            if re.match(r"^\s*-3[01](?:\s|$)", lines[scan]):
                break
            if re.match(r"^\s*-20(?:\s|$)", lines[scan]):
                break
            row = _idf_numeric_component_row(lines[scan])
            if row is not None and row[0] in IDF_REFERENCE_ROUTE_IDENTIFIERS:
                raw_point = row[1]
                if any(abs(value) > 1.0e-9 for value in raw_point):
                    offset = offsets[scan]
                    anchor_row = (
                        row[0],
                        [raw_point[axis] + offset[axis] for axis in range(3)],
                    )
                    anchor_index = scan
                    break
            scan += 1
        references.append(
            {
                "reference_kind": IDF_EXTERNAL_REFERENCE_KIND[record_id],
                "reference_identity": identity,
                "record_id": record_id,
                "line_number": index + 1,
                "anchor_record_identifier": (
                    anchor_row[0] if anchor_row is not None else None
                ),
                "anchor_line_number": (
                    anchor_index + 1 if anchor_index is not None else None
                ),
                "engineering_coordinate": (
                    _engineering_coordinate(anchor_row[1])
                    if anchor_row is not None
                    else None
                ),
                "raw_engineering_coordinate": (
                    [round(float(value), 3) for value in anchor_row[1]]
                    if anchor_row is not None
                    else None
                ),
                "status": "coordinate-resolved" if anchor_row is not None else "coordinate-missing",
            }
        )
    return references


@lru_cache(maxsize=4)
def _load_parser(parser_path_text: str):
    parser_path = Path(parser_path_text).resolve()
    if not parser_path.exists():
        raise FileNotFoundError(f"Reference IDF parser not found: {parser_path}")
    module_name = f"idf_reference_parser_{hashlib.sha1(str(parser_path).encode('utf-8')).hexdigest()[:10]}"
    spec = importlib.util.spec_from_file_location(module_name, parser_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load reference IDF parser: {parser_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    parser_dir = str(parser_path.parent)
    inserted_path = parser_dir not in sys.path
    if inserted_path:
        sys.path.insert(0, parser_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted_path and parser_dir in sys.path:
            sys.path.remove(parser_dir)
    return module


def _engineering_coordinate(point: list[float] | None) -> list[float] | None:
    if not point:
        return None
    return [round(float(value) / 100.0, 3) for value in point]


def _component_fingerprint(component: dict[str, Any]) -> str:
    identifiers = component.get("identifiers") or [component.get("identifier")]
    fields = (
        component.get("type", ""),
        "/".join(str(value) for value in identifiers if value is not None),
        component.get("skey", ""),
        component.get("spec", ""),
        component.get("materialCode", ""),
    )
    return ":".join(str(value).strip() for value in fields)


def _component_semantic_type(component: dict[str, Any]) -> str:
    """Refine parser topology classes without changing their raw contract."""

    component_type = str(component.get("type", ""))
    subtype = str(component.get("subtype", "")).lower()
    skey = str(component.get("skey", "")).upper()
    identifiers = {int(value) for value in (component.get("identifiers") or []) if str(value).isdigit()}
    if component_type == "branch" and (
        subtype == "tee" or skey.startswith("TE") or bool(identifiers.intersection({45, 46, 47}))
    ):
        return "tee"
    if component_type == "teed-elbow":
        return "elbow"
    return component_type


def _stable_weld_record(weld: dict[str, Any], components_by_id: dict[str, dict[str, Any]], pipeline: str) -> dict[str, Any]:
    adjacent = sorted(
        _component_fingerprint(components_by_id[component_id])
        for component_id in weld.get("connectedComponentIds", [])
        if component_id in components_by_id
    )
    coordinate = _engineering_coordinate(weld.get("start") or weld.get("displayStart")) or []
    canonical = json.dumps(
        {
            "pipeline": pipeline,
            "coordinate": coordinate,
            "weld_type": weld.get("weldType", ""),
            "adjacent": adjacent,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "weld_key": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20],
        "canonical_identity": canonical,
        "display_number": weld.get("weldNo"),
        "weld_type": weld.get("weldType", ""),
        "nominal_bore_mm": float(weld.get("spec") or 0),
        "generated": bool(weld.get("generated")),
        "pipe_split_weld": bool(weld.get("pipeSplitWeld")),
        "pipe_group_id": str(weld.get("pipeGroupId") or ""),
        "pipe_split_index": int(weld.get("pipeSplitIndex") or 0),
        "generation_rule": str(weld.get("completionRule") or ""),
        "weld_location": weld.get("weldLocation", ""),
        "weld_point_type": weld.get("weldPointType", ""),
        "prefab_rule": weld.get("prefabRule", ""),
        "engineering_coordinate": coordinate,
        "adjacent_component_ids": list(weld.get("connectedComponentIds", [])),
        "adjacent_component_types": sorted(
            {
                str(components_by_id[component_id].get("type", ""))
                for component_id in weld.get("connectedComponentIds", [])
                if component_id in components_by_id
            }
        ),
        "adjacent_component_semantic_types": sorted(
            {
                _component_semantic_type(components_by_id[component_id])
                for component_id in weld.get("connectedComponentIds", [])
                if component_id in components_by_id
            }
        ),
        "adjacent_component_fingerprints": adjacent,
        "source_weld_id": str(weld.get("id", "")),
        "source_direction": [float(value) for value in (weld.get("direction") or [])],
        "evidence": "idf-explicit" if not weld.get("generated") else "idf-topology-derived",
    }


def _apply_uniform_pipe_split_rules(
    model: dict[str, Any],
    parser: Any,
    *,
    split_length_m: float,
    minimum_remainder_m: float,
    number_mode: str,
) -> dict[str, Any]:
    """Run the viewer's mature pipe splitter with a project-wide length rule.

    The reference parser keeps its options in a module global.  Preserve and
    restore that object so an opt-in split analysis cannot change later normal
    IDF parses in the same Python process.
    """

    if split_length_m <= 0:
        raise ValueError("pipe_split_length_m must be positive")
    if minimum_remainder_m < 0:
        raise ValueError("pipe_split_minimum_remainder_m cannot be negative")
    before = sum(bool(item.get("pipeSplitWeld")) for item in model.get("components", []))
    previous_options = parser.CURRENT_PARSE_OPTIONS
    options = copy.deepcopy(previous_options)
    # CS is the parser fallback. SS and N/A are explicitly included so this
    # research mode follows the requested uniform 12 m rule, independent of
    # the viewer's material stock-length defaults.
    options.pipe_split_lengths_m = {
        "CS": float(split_length_m),
        "SS": float(split_length_m),
        "N/A": float(split_length_m),
    }
    options.pipe_split_min_remainder_m = float(minimum_remainder_m)
    options.pipe_split_number_mode = str(number_mode)
    parser.CURRENT_PARSE_OPTIONS = options
    try:
        parser.apply_global_pipe_material_rules([model])
    finally:
        parser.CURRENT_PARSE_OPTIONS = previous_options
    after = sum(bool(item.get("pipeSplitWeld")) for item in model.get("components", []))
    return {
        "enabled": True,
        "implementation": "idf-pipe-viewer.apply_global_pipe_material_rules",
        "rule_scope": "uniform-project-rule",
        "split_length_m": float(split_length_m),
        "minimum_remainder_m": float(minimum_remainder_m),
        "number_mode": str(number_mode),
        "generated_pipe_split_weld_count": after - before,
    }


def _component_port_refs(model: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Collect unique external component ports by exact IDF/viewer coordinate.

    A zero-length flange is deliberately retained as a single port.  Some IDF
    producers place that port, its gasket face, and the adjoining pipe end at
    one coordinate; that is the case the reference parser's broad gasket guard
    cannot distinguish.
    """

    refs_by_coordinate: dict[str, list[dict[str, Any]]] = {}
    segments_by_component: dict[str, list[dict[str, Any]]] = {}
    for segment in model.get("segments", []):
        component_id = str(segment.get("componentId") or "")
        if component_id:
            segments_by_component.setdefault(component_id, []).append(segment)

    def remember(component: dict[str, Any], point: list[float] | None, role: str) -> None:
        key = _raw_coordinate_key(point)
        component_id = str(component.get("id") or "")
        if not key or not component_id:
            return
        entries = refs_by_coordinate.setdefault(key, [])
        if any(entry["component_id"] == component_id for entry in entries):
            return
        entries.append(
            {
                "component_id": component_id,
                "component_type": str(component.get("type") or ""),
                "role": role,
                "point": list(point or []),
                "component": component,
            }
        )

    for component in model.get("components", []):
        if component.get("type") in {"weld", "bolt", "support", "olet-marker"}:
            continue
        component_id = str(component.get("id") or "")
        segments = segments_by_component.get(component_id, [])
        nonzero_counts: Counter[str] = Counter()
        points_by_key: dict[str, list[float]] = {}
        for segment in segments:
            start, end = segment.get("start"), segment.get("end")
            if not start or not end:
                continue
            start_key, end_key = _raw_coordinate_key(start), _raw_coordinate_key(end)
            if start_key:
                points_by_key[start_key] = start
            if end_key:
                points_by_key[end_key] = end
            if start_key != end_key:
                nonzero_counts.update(key for key in (start_key, end_key) if key)
        external_keys = {key for key, count in nonzero_counts.items() if count == 1}
        if not external_keys:
            external_keys = set(points_by_key)
        for key in external_keys:
            remember(component, points_by_key[key], "external")
        # ETBW teed-elbows contain a reverse helper segment at the first
        # physical port. Degree counting consequently sees that port twice
        # and mistakes it for an internal vertex. The component's declared
        # start/end remain authoritative weldable ports.
        if component.get("type") == "teed-elbow":
            remember(component, component.get("start"), "start")
            remember(component, component.get("end"), "end")
        if not segments:
            remember(component, component.get("start"), "start")
            remember(component, component.get("end"), "end")
    return refs_by_coordinate


def _direction_from_component_port(component: dict[str, Any], point: list[float]) -> list[float] | None:
    candidates = []
    for start, end in (
        (component.get("start"), component.get("end")),
        *((segment.get("start"), segment.get("end")) for segment in component.get("segments") or []),
    ):
        if not start or not end or start == end:
            continue
        if _raw_coordinate_key(start) == _raw_coordinate_key(point):
            candidates.append([float(end[i]) - float(start[i]) for i in range(3)])
        elif _raw_coordinate_key(end) == _raw_coordinate_key(point):
            candidates.append([float(start[i]) - float(end[i]) for i in range(3)])
    if not candidates:
        return None
    vector = candidates[0]
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length > 1e-9 else None


def _complete_gasket_coincident_flange_welds(
    model: dict[str, Any], parser: Any
) -> dict[str, Any]:
    """Recover weldable flange back-side ports hidden by a coincident gasket.

    ``idf-pipe-viewer`` intentionally suppresses automatic welding for every
    connection node that contains a gasket.  That is correct for a normal
    flange face, but loses a weld when an IDF encodes a zero-length weldable
    flange whose pipe side and gasket side share the same coordinate.
    """

    refs_by_coordinate = _component_port_refs(model)
    existing_weld_coordinates = {
        _raw_coordinate_key(component.get("start") or component.get("displayStart"))
        for component in model.get("components", [])
        if component.get("type") == "weld"
    }
    additions: list[dict[str, Any]] = []
    audited_nodes: list[dict[str, Any]] = []
    for coordinate, refs in sorted(refs_by_coordinate.items()):
        if coordinate in existing_weld_coordinates:
            continue
        types = {ref["component_type"] for ref in refs}
        if "gasket" not in types or "flange" not in types:
            continue
        flanges = [ref for ref in refs if ref["component_type"] == "flange"]
        neighbours = [
            ref for ref in refs
            if ref["component_type"] in WELDABLE_FLANGE_NEIGHBOUR_TYPES
        ]
        for flange_ref in flanges:
            flange = flange_ref["component"]
            flange_skey = str(flange.get("skey") or "").upper()
            weld_type = str(parser.infer_component_weld_type(flange) or "").lower()
            if weld_type not in {"bw", "sw", "scw", "so"}:
                continue
            compatible = [
                ref for ref in neighbours
                if ref["component_id"] != flange_ref["component_id"]
                and (
                    not float(flange.get("spec") or 0)
                    or not float(ref["component"].get("spec") or 0)
                    or abs(float(flange.get("spec") or 0) - float(ref["component"].get("spec") or 0)) < 1e-6
                )
            ]
            if not compatible:
                continue
            neighbour_ref = sorted(
                compatible,
                key=lambda ref: (
                    0 if ref["component_type"] == "pipe" else 1,
                    ref["component_id"],
                ),
            )[0]
            point = flange_ref["point"]
            component_ids = sorted([flange_ref["component_id"], neighbour_ref["component_id"]])
            addition = {
                "id": f"port-completion-weld:{coordinate}:{flange_ref['component_id']}",
                "lineNumber": 0,
                "identifier": 120,
                "type": "weld",
                "generated": True,
                "completionRule": "gasket-coincident-weldable-flange-port",
                "referenceParserGap": True,
                "start": list(point),
                "end": None,
                "spec": float(flange.get("spec") or neighbour_ref["component"].get("spec") or 0),
                "outerDiameterMm": float(flange.get("outerDiameterMm") or 0),
                "materialIndex": 0,
                "materialCode": "",
                "materialDescription": "端口覆盖审计补生成焊缝",
                "skey": "WELD",
                "sourceFlangeSkey": flange_skey,
                "weldType": weld_type,
                "connectedComponentIds": component_ids,
                "direction": _direction_from_component_port(neighbour_ref["component"], point),
                "noMaterialFlag": False,
                "pipeOpeningWeldNoMaterial": False,
                "quantity": 1,
                "raw": "",
            }
            additions.append(addition)
            audited_nodes.append(
                {
                    "coordinate": _engineering_coordinate(point),
                    "flange_component_id": flange_ref["component_id"],
                    "flange_skey": flange_skey,
                    "neighbour_component_id": neighbour_ref["component_id"],
                    "neighbour_type": neighbour_ref["component_type"],
                    "weld_type": weld_type,
                    "reason": "reference auto-weld skipped entire coordinate because gasket is coincident",
                }
            )

    if additions:
        model.setdefault("components", []).extend(additions)
        model.setdefault("symbolComponents", []).extend(additions)
        parser.assign_weld_numbers(
            model["components"],
            model.get("segments", []),
            default_pipeline_id=parser.get_model_pipeline_id(model, "UNKNOWN"),
            default_pipeline_name=model.get("pipelineName") or model.get("fileName") or "UNKNOWN",
        )
    return {
        "rule": "gasket-coincident-weldable-flange-port",
        "added_weld_count": len(additions),
        "added_weld_ids": [component["id"] for component in additions],
        "audited_nodes": audited_nodes,
    }


def _complete_exposed_weldable_component_ports(
    model: dict[str, Any], parser: Any, *, coordinate_tolerance_raw: float = 5.0
) -> dict[str, Any]:
    """Recover explicit weldable tee, teed-elbow and socket-olet ports.

    IDF coordinates are stored at 0.01 mm resolution. Some producers leave a
    one-unit mismatch between a pipe end and a tee port, while split drawings
    can leave the tee port or socket-olet branch side intentionally open. The
    mature viewer exposes all component segments but only auto-welds exact
    coincident nodes. Complete only component kinds whose free port is itself
    unambiguous weld evidence; ordinary free pipe ends remain untouched.
    """

    refs_by_coordinate = _component_port_refs(model)
    refs = [ref for values in refs_by_coordinate.values() for ref in values]
    existing_welds = [
        component for component in model.get("components", [])
        if component.get("type") == "weld"
    ]

    def near(left: list[float] | None, right: list[float] | None) -> bool:
        return bool(left and right and math.dist(left, right) <= coordinate_tolerance_raw)

    additions: list[dict[str, Any]] = []
    audited_nodes: list[dict[str, Any]] = []
    completed_keys: set[tuple[str, str]] = set()
    for ref in refs:
        component = ref["component"]
        component_id = ref["component_id"]
        component_type = str(component.get("type") or "")
        point = ref["point"]
        semantic = _component_semantic_type(component)
        is_tee_port = semantic == "tee" and str(component.get("skey") or "").upper().startswith("TE")
        is_teed_elbow_port = component_type == "teed-elbow" and str(
            component.get("skey") or ""
        ).upper().startswith("ET")
        is_socket_olet_free_end = (
            component_type == "olet"
            and _raw_coordinate_key(point) == _raw_coordinate_key(component.get("end"))
            and "SW" in str(component.get("materialDescription") or "").upper()
        )
        if not (is_tee_port or is_teed_elbow_port or is_socket_olet_free_end):
            continue
        if any(near(point, weld.get("start") or weld.get("displayStart")) for weld in existing_welds + additions):
            continue

        neighbours = sorted(
            {
                other["component_id"]: other
                for other in refs
                if other["component_id"] != component_id
                and near(point, other["point"])
                and other["component_type"] not in {"gasket", "support", "olet-marker"}
            }.values(),
            key=lambda item: item["component_id"],
        )
        component_ids = [component_id] + [item["component_id"] for item in neighbours]
        key = (_raw_coordinate_key(point) or "", "/".join(sorted(component_ids)))
        if key in completed_keys:
            continue
        completed_keys.add(key)
        weld_type = "sw" if is_socket_olet_free_end else str(
            parser.infer_component_weld_type(component) or "bw"
        ).lower()
        addition = {
            "id": f"port-completion-weld:{key[0]}:{component_id}",
            "lineNumber": int(component.get("lineNumber") or 0),
            "identifier": 120,
            "type": "weld",
            "generated": True,
            "completionRule": (
                "socket-olet-exposed-branch-port"
                if is_socket_olet_free_end
                else (
                    "teed-elbow-exposed-weldable-port"
                    if is_teed_elbow_port
                    else "tee-weldable-port-with-tolerant-join"
                )
            ),
            "referenceParserGap": True,
            "start": list(point),
            "end": None,
            "spec": float(component.get("branchSpec") or component.get("spec") or 0),
            "outerDiameterMm": float(component.get("outerDiameterMm") or 0),
            "materialIndex": 0,
            "materialCode": "",
            "materialDescription": "开放可焊端口覆盖审计补生成焊缝",
            "skey": "WELD",
            "weldType": weld_type,
            "connectedComponentIds": sorted(component_ids),
            "direction": _direction_from_component_port(component, point),
            "noMaterialFlag": False,
            "pipeOpeningWeldNoMaterial": False,
            "quantity": 1,
            "raw": "",
        }
        additions.append(addition)
        audited_nodes.append({
            "coordinate": _engineering_coordinate(point),
            "component_id": component_id,
            "component_semantic_type": semantic,
            "neighbour_component_ids": [item["component_id"] for item in neighbours],
            "weld_type": weld_type,
            "rule": addition["completionRule"],
            "coordinate_tolerance_mm": coordinate_tolerance_raw / 100.0,
        })

    if additions:
        model.setdefault("components", []).extend(additions)
        model.setdefault("symbolComponents", []).extend(additions)
        parser.assign_weld_numbers(
            model["components"],
            model.get("segments", []),
            default_pipeline_id=parser.get_model_pipeline_id(model, "UNKNOWN"),
            default_pipeline_name=model.get("pipelineName") or model.get("fileName") or "UNKNOWN",
        )
    return {
        "rule": "exposed-weldable-component-port-completion",
        "added_weld_count": len(additions),
        "added_weld_ids": [component["id"] for component in additions],
        "audited_nodes": audited_nodes,
        "coordinate_tolerance_mm": coordinate_tolerance_raw / 100.0,
    }


def _apply_explicit_pcf_weld_types(
    source_path: Path,
    model: dict[str, Any],
    pcf_parser: Any,
) -> int:
    """Keep the standardized PCF ``WELD-TYPE`` field on round-trip import."""

    pcf_data = pcf_parser.load_pcf_structure(source_path)
    explicit_by_line = {
        int(component.get("pcfLineNumber") or 0): str(
            (component.get("attributes") or {}).get("WELD-TYPE") or ""
        ).strip().lower()
        for component in pcf_parser.parse_upper_components(pcf_data)
        if component.get("sourceType") == "WELD"
    }
    aliases = {
        "sc": "scw",
        "screwed": "scw",
        "set-on": "seton",
        "set_on": "seton",
    }
    applied = 0
    for component in model.get("components", []):
        if component.get("type") != "weld":
            continue
        explicit = explicit_by_line.get(int(component.get("pcfLineNumber") or 0), "")
        explicit = aliases.get(explicit, explicit)
        if explicit:
            component["weldType"] = explicit
            component["pcfExplicitWeldType"] = True
            applied += 1
    return applied


def _raw_coordinate_key(point: list[float] | None) -> str | None:
    if not point:
        return None
    return ",".join(f"{float(value):.3f}" for value in point)


def _infer_weld_component_ids(model: dict[str, Any]) -> dict[str, list[str]]:
    """Infer explicit-weld adjacency from coincident component ports.

    The reference parser already supplies ``connectedComponentIds`` for most
    generated welds, but many explicit IDF weld records omit it. Their 3D
    coordinate still coincides with the ports of the two welded components.
    """

    ignored_types = {"weld", "bolt", "gasket", "support", "olet-marker"}
    component_types = {
        str(component.get("id")): str(component.get("type", ""))
        for component in model.get("components", [])
        if component.get("id")
    }
    components_at_coordinate: dict[str, set[str]] = {}

    def add(component_id: str, point: list[float] | None) -> None:
        coordinate = _raw_coordinate_key(point)
        if coordinate and component_types.get(component_id) not in ignored_types:
            components_at_coordinate.setdefault(coordinate, set()).add(component_id)

    for segment in model.get("segments", []):
        component_id = str(segment.get("componentId", ""))
        add(component_id, segment.get("start"))
        add(component_id, segment.get("end"))
    for component in model.get("components", []):
        component_id = str(component.get("id", ""))
        add(component_id, component.get("start"))
        add(component_id, component.get("end"))
        add(component_id, component.get("branchPoint"))

    result: dict[str, list[str]] = {}
    for weld in model.get("components", []):
        if weld.get("type") != "weld":
            continue
        coordinate = _raw_coordinate_key(weld.get("start") or weld.get("displayStart"))
        adjacent = set(str(value) for value in (weld.get("connectedComponentIds") or []))
        if coordinate:
            adjacent.update(components_at_coordinate.get(coordinate, set()))
        result[str(weld.get("id", ""))] = sorted(adjacent)
    return result


def _component_route_networks(
    model: dict[str, Any],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Join components at non-weld ports into route networks.

    Flange/gasket/valve groups often contain several components between two
    physical welds. They must behave as one topological connection, while a
    coordinate carrying a physical weld must remain a graph cut.
    """

    ignored_types = {"weld", "bolt", "support", "olet-marker"}
    component_ids = sorted(
        str(component.get("id"))
        for component in model.get("components", [])
        if component.get("id") and str(component.get("type", "")) not in ignored_types
    )
    component_types = {
        str(component.get("id")): str(component.get("type") or "")
        for component in model.get("components", [])
        if component.get("id")
    }
    parent = {component_id: component_id for component_id in component_ids}

    def find(component_id: str) -> str:
        while parent[component_id] != component_id:
            parent[component_id] = parent[parent[component_id]]
            component_id = parent[component_id]
        return component_id

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    components_at_coordinate: dict[str, set[str]] = {}

    def add(component_id: str, point: list[float] | None) -> None:
        coordinate = _raw_coordinate_key(point)
        if coordinate and component_id in parent:
            components_at_coordinate.setdefault(coordinate, set()).add(component_id)

    for segment in model.get("segments", []):
        component_id = str(segment.get("componentId", ""))
        add(component_id, segment.get("start"))
        add(component_id, segment.get("end"))
    for component in model.get("components", []):
        component_id = str(component.get("id", ""))
        add(component_id, component.get("start"))
        add(component_id, component.get("end"))
        add(component_id, component.get("branchPoint"))

    weld_coordinates = {
        coordinate
        for component in model.get("components", [])
        if component.get("type") == "weld"
        for coordinate in [_raw_coordinate_key(component.get("start") or component.get("displayStart"))]
        if coordinate
    }
    completion_weld_coordinates = {
        coordinate
        for component in model.get("components", [])
        if component.get("type") == "weld" and component.get("completionRule")
        for coordinate in [_raw_coordinate_key(component.get("start") or component.get("displayStart"))]
        if coordinate
    }
    for coordinate, values in components_at_coordinate.items():
        if coordinate in weld_coordinates:
            # A zero-length weldable flange can place its pipe-side weld and
            # gasket face at the same coordinate.  The weld cuts pipe↔flange,
            # but flange↔gasket remains a mechanical route through the valve
            # assembly and must connect the welds on its two sides.
            if coordinate in completion_weld_coordinates:
                flanges = sorted(
                    component_id
                    for component_id in values
                    if component_types.get(component_id) == "flange"
                )
                gaskets = sorted(
                    component_id
                    for component_id in values
                    if component_types.get(component_id) == "gasket"
                )
                for flange in flanges:
                    for gasket in gaskets:
                        union(flange, gasket)
            continue
        values = sorted(values)
        for left, right in zip(values, values[1:]):
            union(left, right)

    groups: dict[str, list[str]] = {}
    for component_id in component_ids:
        groups.setdefault(find(component_id), []).append(component_id)
    network_members: dict[str, list[str]] = {}
    network_by_component: dict[str, str] = {}
    for members in groups.values():
        network_id = "route:" + hashlib.sha1("|".join(sorted(members)).encode("utf-8")).hexdigest()[:16]
        network_members[network_id] = sorted(members)
        for component_id in members:
            network_by_component[component_id] = network_id
    return network_by_component, network_members


def _attach_external_reference_frontiers(
    references: list[dict[str, Any]],
    model: dict[str, Any],
    welds: list[dict[str, Any]],
    network_by_component: dict[str, str],
    weld_graph: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Resolve an external identity to a unique source-topology frontier.

    ``-30`` pipeline references are attached to the first/last route element,
    so the route's sole exposed weld is the useful frontier.  ``-31`` records
    are different: ISOGEN writes them after a completed equipment branch and
    then emits a zero-length branch marker at the *root*.  A weld coincident
    with that marker is therefore the host/root weld, not the equipment-end
    weld shown beside the reference text on the ISO.  For that case we cross
    the two-port branch component and follow the unique simple path to its
    terminal weld.  Any fork, cycle, or non-unique component mate is retained
    as audit evidence and rejected as an absolute frontier.
    """

    components = {
        str(component.get("id")): component
        for component in model.get("components", [])
        if component.get("id")
    }
    graph_adjacency: dict[str, set[str]] = {}
    for edge in (weld_graph or {}).get("edges", []):
        keys = [str(value) for value in edge.get("weld_keys", [])]
        if len(keys) != 2:
            continue
        left, right = keys
        graph_adjacency.setdefault(left, set()).add(right)
        graph_adjacency.setdefault(right, set()).add(left)

    def terminal_path(
        root_key: str, branch_entry_key: str
    ) -> tuple[list[str], str]:
        path = [branch_entry_key]
        previous = root_key
        current = branch_entry_key
        visited = {root_key, branch_entry_key}
        while True:
            options = sorted(
                value
                for value in graph_adjacency.get(current, set())
                if value != previous
            )
            if not options:
                return path, "unique-terminal-weld"
            if len(options) != 1:
                return path, "ambiguous-branch-path"
            following = options[0]
            if following in visited:
                return path, "cyclic-branch-path"
            path.append(following)
            visited.add(following)
            previous, current = current, following

    result = []
    for reference in references:
        item = dict(reference)
        raw_coordinate = reference.get("raw_engineering_coordinate")
        if not raw_coordinate:
            item.update(
                {
                    "source_component_ids": [],
                    "source_route_ids": [],
                    "frontier_weld_keys": [],
                    "frontier_status": "reference-coordinate-missing",
                }
            )
            result.append(item)
            continue
        component_ids = []
        for component_id, component in components.items():
            for field in ("start", "end", "branchPoint"):
                point = component.get(field)
                if (
                    isinstance(point, (list, tuple))
                    and len(point) == 3
                    and math.dist(
                        [float(value) for value in point],
                        [float(value) for value in raw_coordinate],
                    )
                    <= 1.0
                ):
                    component_ids.append(component_id)
                    break
        route_ids = sorted(
            {
                network_by_component[component_id]
                for component_id in component_ids
                if component_id in network_by_component
            }
        )
        frontier_keys = sorted(
            {
                str(weld["weld_key"])
                for weld in welds
                if any(
                    network_by_component.get(str(component_id)) in route_ids
                    for component_id in weld.get("adjacent_component_ids", [])
                )
            }
        )
        weld_by_key = {str(weld["weld_key"]): weld for weld in welds}
        coordinate_matches = [
            key
            for key in frontier_keys
            if len(weld_by_key[key].get("engineering_coordinate", [])) == 3
            and math.dist(
                [
                    float(value)
                    for value in weld_by_key[key]["engineering_coordinate"]
                ],
                [float(value) / 100.0 for value in raw_coordinate],
            )
            <= 1.0
        ]
        selected_key = (
            coordinate_matches[0]
            if len(coordinate_matches) == 1
            else frontier_keys[0]
            if not coordinate_matches and len(frontier_keys) == 1
            else None
        )
        root_key = coordinate_matches[0] if len(coordinate_matches) == 1 else None
        component_mates = sorted(
            key for key in frontier_keys if key != root_key
        )
        branch_path: list[str] = []
        branch_path_status = None
        branch_entry_key = None
        if (
            reference.get("reference_kind") == "equipment-nozzle"
            and root_key is not None
            and len(component_mates) == 1
            and graph_adjacency
        ):
            branch_entry_key = component_mates[0]
            branch_path, branch_path_status = terminal_path(
                root_key, branch_entry_key
            )
            selected_key = (
                branch_path[-1]
                if branch_path_status == "unique-terminal-weld"
                else None
            )
        elif reference.get("reference_kind") == "equipment-nozzle":
            # A direct equipment weld without a two-port branch component is
            # still valid.  Multiple component mates, however, are not.
            branch_path_status = (
                "direct-coordinate-terminal"
                if root_key is not None and not component_mates
                else "equipment-branch-mate-not-unique"
            )
            if branch_path_status != "direct-coordinate-terminal":
                selected_key = None

        if reference.get("reference_kind") == "equipment-nozzle":
            frontier_status = (
                "unique-external-terminal-weld"
                if selected_key is not None
                else branch_path_status or "ambiguous-external-terminal"
            )
            selection_evidence = (
                "equipment branch root crosses one component mate and follows "
                "a unique simple weld path to the external terminal"
                if branch_path_status == "unique-terminal-weld"
                else "equipment reference coordinate is itself the unique terminal weld"
                if branch_path_status == "direct-coordinate-terminal"
                else None
            )
        else:
            frontier_status = (
                "unique-coordinate-weld"
                if len(coordinate_matches) == 1
                else "unique-first-weld"
                if not coordinate_matches and len(frontier_keys) == 1
                else "no-first-weld"
                if not frontier_keys
                else "ambiguous-first-weld"
            )
            selection_evidence = (
                "external reference engineering coordinate equals one source weld"
                if len(coordinate_matches) == 1
                else "reference route exposes exactly one source weld"
                if not coordinate_matches and len(frontier_keys) == 1
                else None
            )
        item.update(
            {
                "source_component_ids": sorted(component_ids),
                "source_route_ids": route_ids,
                "frontier_weld_keys": frontier_keys,
                "coordinate_matched_weld_keys": coordinate_matches,
                "reference_root_weld_key": root_key,
                "reference_component_mate_weld_keys": component_mates,
                "reference_branch_entry_weld_key": branch_entry_key,
                "reference_branch_weld_path": branch_path,
                "reference_branch_path_status": branch_path_status,
                "frontier_weld_key": selected_key,
                "frontier_status": frontier_status,
                "frontier_selection_evidence": selection_evidence,
            }
        )
        result.append(item)
    return result


def _weld_graph(
    welds: list[dict[str, Any]],
    network_by_component: dict[str, str] | None = None,
    network_members: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Connect welds that share a physical piping component.

    The graph deliberately contains no engineering length weight.  Coordinates
    are retained only so a 3D edge direction can be projected onto paper.
    """

    by_component: dict[str, list[str]] = {}
    weld_by_key = {str(weld["weld_key"]): weld for weld in welds}
    for weld in welds:
        for component_id in weld.get("adjacent_component_ids", []):
            component_id = str(component_id)
            route_id = (network_by_component or {}).get(component_id, component_id)
            by_component.setdefault(route_id, []).append(str(weld["weld_key"]))

    edge_components: dict[tuple[str, str], list[str]] = {}
    edge_rules: dict[tuple[str, str], str] = {}
    for component_id, keys in by_component.items():
        unique_keys = sorted(set(keys))
        pairs = list(combinations(unique_keys, 2))
        if len(unique_keys) >= 3:
            coordinates = {
                key: [float(value) for value in weld_by_key[key].get("engineering_coordinate", [])]
                for key in unique_keys
            }
            farthest_left, farthest_right = max(
                pairs,
                key=lambda pair: sum(
                    (coordinates[pair[1]][axis] - coordinates[pair[0]][axis]) ** 2
                    for axis in range(3)
                ),
            )
            origin = coordinates[farthest_left]
            axis_vector = [
                coordinates[farthest_right][axis] - origin[axis] for axis in range(3)
            ]
            denominator = sum(value * value for value in axis_vector)
            projections = {}
            residuals = []
            if denominator > 1e-12:
                for key, coordinate in coordinates.items():
                    fraction = sum(
                        (coordinate[axis] - origin[axis]) * axis_vector[axis]
                        for axis in range(3)
                    ) / denominator
                    projections[key] = fraction
                    projected = [origin[axis] + fraction * axis_vector[axis] for axis in range(3)]
                    residuals.append(
                        math.sqrt(
                            sum((coordinate[axis] - projected[axis]) ** 2 for axis in range(3))
                        )
                    )
            # A straight pipe with an interior olet/branch weld is a path, not
            # a clique. Connect only consecutive welds along its 3D axis.
            if projections and max(residuals, default=math.inf) <= 0.01:
                ordered = sorted(unique_keys, key=lambda key: projections[key])
                pairs = list(zip(ordered, ordered[1:]))
        for left, right in pairs:
            pair = tuple(sorted((left, right)))
            via_ids = (network_members or {}).get(component_id, [component_id])
            edge_components.setdefault(pair, []).extend(via_ids)
            edge_rules[pair] = (
                "consecutive-collinear-welds" if len(pairs) < len(list(combinations(unique_keys, 2))) else "shared-component"
            )

    edges = []
    adjacency: dict[str, set[str]] = {key: set() for key in weld_by_key}
    for (left, right), component_ids in sorted(edge_components.items()):
        adjacency[left].add(right)
        adjacency[right].add(left)
        edges.append(
            {
                "weld_keys": [left, right],
                "via_component_ids": sorted(component_ids),
                "connection_rule": edge_rules.get((left, right), "shared-component"),
                "delta_engineering": [
                    round(float(b) - float(a), 3)
                    for a, b in zip(
                        weld_by_key[left].get("engineering_coordinate", []),
                        weld_by_key[right].get("engineering_coordinate", []),
                    )
                ],
            }
        )

    chain_orders: list[list[str]] = []
    if weld_by_key and len(edges) == len(weld_by_key) - 1 and all(len(value) <= 2 for value in adjacency.values()):
        terminals = sorted(key for key, neighbours in adjacency.items() if len(neighbours) <= 1)
        if len(weld_by_key) == 1:
            chain_orders = [[next(iter(weld_by_key))]]
        elif len(terminals) == 2:
            order = []
            previous = None
            current: str | None = terminals[0]
            while current is not None:
                order.append(current)
                remaining = [value for value in adjacency[current] if value != previous]
                previous, current = current, (remaining[0] if remaining else None)
            if len(order) == len(weld_by_key):
                chain_orders = [order, list(reversed(order))]

    component_count = 0
    unseen = set(adjacency)
    while unseen:
        component_count += 1
        stack = [unseen.pop()]
        while stack:
            current = stack.pop()
            for neighbour in adjacency[current]:
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    stack.append(neighbour)
    is_forest = len(edges) == len(weld_by_key) - component_count

    return {
        "node_count": len(weld_by_key),
        "edge_count": len(edges),
        "edges": edges,
        "degrees": {key: len(value) for key, value in adjacency.items()},
        "connected_component_count": component_count,
        "is_forest": is_forest,
        "is_tree": bool(weld_by_key) and component_count == 1 and is_forest,
        "is_simple_chain": bool(chain_orders),
        "chain_orders": chain_orders,
        "weighting": "topology-and-direction-only; engineering length excluded",
    }


def _enrich_pipe_split_records(
    pipe_split_welds: list[dict[str, Any]],
    native_welds: list[dict[str, Any]],
    components_by_id: dict[str, dict[str, Any]],
) -> None:
    """Attach host-axis fractions and bracketing native weld identities."""

    group_pipes: dict[str, list[dict[str, Any]]] = {}
    for component in components_by_id.values():
        group_id = str(component.get("pipeGroupId") or "")
        if group_id and component.get("type") == "pipe":
            group_pipes.setdefault(group_id, []).append(component)

    for split in pipe_split_welds:
        group_id = str(split.get("pipe_group_id") or "")
        pipes = group_pipes.get(group_id, [])
        points = [
            [float(value) for value in point]
            for pipe in pipes
            for point in (pipe.get("start"), pipe.get("end"))
            if point
        ]
        coordinate = [float(value) * 100.0 for value in split.get("engineering_coordinate", [])]
        direction = [float(value) for value in split.get("source_direction", [])]
        magnitude = math.sqrt(sum(value * value for value in direction))
        if len(points) < 2 or len(coordinate) != 3 or magnitude <= 1e-12:
            split["virtual_location_status"] = "missing-host-axis"
            continue
        axis = [value / magnitude for value in direction]
        origin = points[0]

        def projection(point: list[float]) -> float:
            return sum((float(point[index]) - origin[index]) * axis[index] for index in range(3))

        minimum, maximum = min(map(projection, points)), max(map(projection, points))
        split_t = projection(coordinate)
        group_component_ids = {str(pipe.get("id")) for pipe in pipes}
        boundaries = []
        for weld in native_welds:
            if not group_component_ids.intersection(str(value) for value in weld.get("adjacent_component_ids", [])):
                continue
            raw = [float(value) * 100.0 for value in weld.get("engineering_coordinate", [])]
            if len(raw) == 3:
                weld_t = projection(raw)
                # Only end welds bracket a material pipe group. Interior olet
                # or branch-root welds may lie on the same axis but must not
                # become a virtual split boundary (viewer uses the same 40
                # raw-unit endpoint tolerance when numbering split welds).
                if min(abs(weld_t - minimum), abs(weld_t - maximum)) <= 40.0:
                    boundaries.append((weld_t, str(weld["weld_key"]), weld.get("display_number")))
        boundaries.sort()
        left = next((item for item in reversed(boundaries) if item[0] < split_t - 1e-6), None)
        right = next((item for item in boundaries if item[0] > split_t + 1e-6), None)
        span = maximum - minimum
        split.update(
            {
                "host_pipe_component_ids": sorted(group_component_ids),
                "source_pipe_component_ids": sorted(
                    {
                        str(pipe.get("sourcePipeComponentId") or pipe.get("id"))
                        for pipe in pipes
                    }
                ),
                "host_group_length_m": round(span / 100000.0, 6),
                "host_group_fraction": round((split_t - minimum) / span, 9) if span > 1e-9 else None,
                "left_native_weld_key": left[1] if left else None,
                "left_native_display_number": left[2] if left else None,
                "right_native_weld_key": right[1] if right else None,
                "right_native_display_number": right[2] if right else None,
                "bracket_fraction": (
                    round((split_t - left[0]) / (right[0] - left[0]), 9)
                    if left and right and right[0] - left[0] > 1e-9
                    else None
                ),
                "virtual_location_status": "bracketed-by-native-welds" if left and right else "open-ended-host-route",
            }
        )
        if left and right and left[2] not in (None, "") and right[2] not in (None, ""):
            split["source_display_number"] = split.get("display_number")
            split["display_number"] = f"{left[2]}/{right[2]}-{max(1, int(split.get('pipe_split_index') or 1))}"
            split["display_number_rule"] = "bracketing-native-welds-and-split-index"


def contract_pipe_split_welds(
    expanded_graph: dict[str, Any], pipe_split_weld_keys: set[str]
) -> dict[str, Any]:
    """Suppress virtual degree-two split nodes for physical ISO-glyph matching."""

    adjacency: dict[str, set[str]] = {
        str(key): set() for key in expanded_graph.get("degrees", {})
    }
    for edge in expanded_graph.get("edges", []):
        left, right = (str(value) for value in edge["weld_keys"])
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    native = sorted(set(adjacency) - set(pipe_split_weld_keys))
    contracted_edges: set[tuple[str, str]] = set()
    paths: list[dict[str, Any]] = []
    for start in native:
        stack = [(neighbor, start, []) for neighbor in adjacency.get(start, set())]
        seen_states: set[tuple[str, str]] = set()
        while stack:
            current, previous, virtual_path = stack.pop()
            state = (current, previous)
            if state in seen_states:
                continue
            seen_states.add(state)
            if current not in pipe_split_weld_keys:
                if current != start:
                    pair = tuple(sorted((start, current)))
                    contracted_edges.add(pair)
                    if start == pair[0]:
                        paths.append(
                            {"native_weld_keys": list(pair), "suppressed_pipe_split_weld_keys": virtual_path}
                        )
                continue
            for neighbor in adjacency.get(current, set()):
                if neighbor != previous:
                    stack.append((neighbor, current, virtual_path + [current]))
    degree = {key: 0 for key in native}
    for left, right in contracted_edges:
        degree[left] += 1
        degree[right] += 1
    component_count = 0
    unseen = set(native)
    contracted_adjacency = {key: set() for key in native}
    for left, right in contracted_edges:
        contracted_adjacency[left].add(right)
        contracted_adjacency[right].add(left)
    while unseen:
        component_count += 1
        stack = [unseen.pop()]
        while stack:
            for neighbour in contracted_adjacency[stack.pop()]:
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    stack.append(neighbour)
    chain_orders: list[list[str]] = []
    terminals = sorted(key for key, values in contracted_adjacency.items() if len(values) <= 1)
    if native and len(contracted_edges) == len(native) - 1 and len(terminals) == 2:
        order, previous, current = [], None, terminals[0]
        while current is not None:
            order.append(current)
            remaining = [value for value in contracted_adjacency[current] if value != previous]
            previous, current = current, (remaining[0] if remaining else None)
        if len(order) == len(native):
            chain_orders = [order, list(reversed(order))]
    return {
        "status": "ok",
        "node_count": len(native),
        "edge_count": len(contracted_edges),
        "nodes": native,
        "edges": [
            {
                "weld_keys": list(edge),
                "via_component_ids": [],
                "connection_rule": "pipe-split-nodes-contracted",
                "delta_engineering": [],
            }
            for edge in sorted(contracted_edges)
        ],
        "degrees": degree,
        "connected_component_count": component_count,
        "is_forest": len(contracted_edges) == len(native) - component_count,
        "is_tree": bool(native) and component_count == 1 and len(contracted_edges) == len(native) - 1,
        "is_simple_chain": bool(chain_orders),
        "chain_orders": chain_orders,
        "suppressed_node_count": len(pipe_split_weld_keys),
        "suppressed_paths": paths,
        "weighting": "topology only; generated pipe-split welds suppressed",
    }


def _terminal_nodes(model: dict[str, Any]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    points: dict[str, list[float]] = {}
    segment_types: dict[str, list[str]] = {}
    for segment in model.get("segments", []):
        if segment.get("type") in {"weld", "gasket", "bolt"}:
            continue
        for point in (segment.get("start"), segment.get("end")):
            if not point:
                continue
            key = ",".join(f"{float(value):.3f}" for value in point)
            counts[key] += 1
            points[key] = point
            segment_types.setdefault(key, []).append(str(segment.get("type", "")))
    return [
        {
            "engineering_coordinate": _engineering_coordinate(points[key]),
            "incident_segment_types": segment_types[key],
        }
        for key, count in counts.items()
        if count == 1
    ]


def load_complete_source_model(
    path: str | Path,
    idf_parser_path: str | Path = DEFAULT_REFERENCE_PARSER,
    pcf_parser_path: str | Path = DEFAULT_REFERENCE_PCF_PARSER,
    *,
    include_pipe_split_welds: bool = False,
    pipe_split_length_m: float = 12.0,
    pipe_split_minimum_remainder_m: float = 0.2,
    pipe_split_number_mode: str = "pipe-end",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse IDF/PCF with the viewer implementation and close audited port gaps."""

    source_path = Path(path).resolve()
    suffix = source_path.suffix.lower()
    if suffix == ".idf":
        parser = _load_parser(str(Path(idf_parser_path).resolve()))
        model = parser.parse_idf(source_path)
        parser_name = "idf-pipe-viewer.parse_idf"
        parser_path = Path(idf_parser_path).resolve()
    elif suffix == ".pcf":
        pcf_parser = _load_parser(str(Path(pcf_parser_path).resolve()))
        parser = pcf_parser.idf
        model = pcf_parser.parse_pcf(source_path)
        explicit_pcf_weld_type_count = _apply_explicit_pcf_weld_types(
            source_path, model, pcf_parser
        )
        parser_name = "idf-pipe-viewer.parse_pcf"
        parser_path = Path(pcf_parser_path).resolve()
    else:
        raise ValueError(f"Unsupported piping source type: {source_path.suffix or '<none>'}")

    reference_weld_count = sum(
        component.get("type") == "weld" for component in model.get("components", [])
    )
    completion = _complete_gasket_coincident_flange_welds(model, parser)
    exposed_port_completion = _complete_exposed_weldable_component_ports(model, parser)
    split_audit = {"enabled": False, "generated_pipe_split_weld_count": 0}
    if include_pipe_split_welds:
        split_audit = _apply_uniform_pipe_split_rules(
            model,
            parser,
            split_length_m=pipe_split_length_m,
            minimum_remainder_m=pipe_split_minimum_remainder_m,
            number_mode=pipe_split_number_mode,
        )
    completion.update(
        {
            "source_type": suffix.lstrip("."),
            "reference_parser": parser_name,
            "reference_parser_path": str(parser_path),
            "reference_parser_weld_count": reference_weld_count,
            "complete_weld_count": sum(
                component.get("type") == "weld" for component in model.get("components", [])
            ),
            "explicit_pcf_weld_type_count": (
                explicit_pcf_weld_type_count if suffix == ".pcf" else 0
            ),
            "pipe_split_generation": split_audit,
            "exposed_weldable_port_completion": exposed_port_completion,
        }
    )
    model["weldCompletionAudit"] = completion
    return model, completion


def _analyze_source_model(
    source_path: Path,
    model: dict[str, Any],
    completion: dict[str, Any],
) -> dict[str, Any]:
    pipeline = str(model.get("pipelineName") or source_path.stem)
    components = model.get("components", [])
    components_by_id = {str(component.get("id")): component for component in components if component.get("id")}
    inferred_weld_components = _infer_weld_component_ids(model)
    expanded_welds = [
        _stable_weld_record(
            component
            | {
                "connectedComponentIds": inferred_weld_components.get(
                    str(component.get("id", "")),
                    list(component.get("connectedComponentIds") or []),
                )
            },
            components_by_id,
            pipeline,
        )
        for component in components
        if component.get("type") == "weld"
    ]
    welds = [weld for weld in expanded_welds if not weld.get("pipe_split_weld")]
    pipe_split_welds = [weld for weld in expanded_welds if weld.get("pipe_split_weld")]
    welds_by_key: dict[str, list[dict[str, Any]]] = {}
    for weld in welds:
        welds_by_key.setdefault(str(weld["weld_key"]), []).append(weld)
    coincident_groups = [
        {
            "weld_key": key,
            "record_count": len(records),
            "display_numbers": [record.get("display_number") for record in records],
            "engineering_coordinate": records[0].get("engineering_coordinate"),
            "source_weld_ids": [record.get("source_weld_id") for record in records],
        }
        for key, records in welds_by_key.items()
        if len(records) > 1
    ]
    drawing_options = model.get("drawingOptions") or {}
    network_by_component, network_members = _component_route_networks(model)
    native_graph = _weld_graph(welds, network_by_component, network_members)
    external_references = (
        _attach_external_reference_frontiers(
            _extract_idf_external_references(source_path),
            model,
            welds,
            network_by_component,
            native_graph,
        )
        if source_path.suffix.lower() == ".idf"
        else []
    )
    expanded_graph = _weld_graph(expanded_welds, network_by_component, network_members)
    _enrich_pipe_split_records(pipe_split_welds, welds, components_by_id)
    contracted_graph = contract_pipe_split_welds(
        expanded_graph, {str(weld["weld_key"]) for weld in pipe_split_welds}
    )
    if pipe_split_welds:
        native_graph = contracted_graph
    return {
        "source": str(source_path),
        "source_type": source_path.suffix.lower().lstrip("."),
        "pipeline": pipeline,
        "component_count": len(components),
        "component_types": dict(Counter(str(component.get("type", "")) for component in components)),
        "segment_count": len(model.get("segments", [])),
        "weld_count": len(welds),
        "weld_site_count": len(welds_by_key),
        "coincident_weld_groups": coincident_groups,
        "weld_types": dict(Counter(str(weld.get("weld_type", "")) for weld in welds)),
        "welds": welds,
        "weld_graph": native_graph,
        "native_welds": welds,
        "native_weld_graph": native_graph,
        "pipe_split_weld_count": len(pipe_split_welds),
        "pipe_split_welds": pipe_split_welds,
        "expanded_weld_count": len(expanded_welds),
        "expanded_welds": expanded_welds,
        "expanded_weld_graph": expanded_graph,
        "contracted_native_weld_graph": contracted_graph,
        "terminal_nodes": _terminal_nodes(model),
        "drawing_viewpoint": drawing_options.get("viewpoint", {}),
        "drawing_split_markers": model.get("drawingSplitMarkers", []),
        "external_references": external_references,
        "weld_completion_audit": completion,
        "warnings": [
            "2D paper length must not be compared with IDF engineering length",
            "drawing viewpoint constrains but does not uniquely prove PDF north orientation",
            "coincident duplicate weld records are grouped as one physical weld site",
        ],
    }


def analyze_piping_source(
    path: str | Path,
    idf_parser_path: str | Path = DEFAULT_REFERENCE_PARSER,
    pcf_parser_path: str | Path = DEFAULT_REFERENCE_PCF_PARSER,
    *,
    include_pipe_split_welds: bool = False,
    pipe_split_length_m: float = 12.0,
    pipe_split_minimum_remainder_m: float = 0.2,
    pipe_split_number_mode: str = "pipe-end",
) -> dict[str, Any]:
    source_path = Path(path).resolve()
    model, completion = load_complete_source_model(
        source_path,
        idf_parser_path=idf_parser_path,
        pcf_parser_path=pcf_parser_path,
        include_pipe_split_welds=include_pipe_split_welds,
        pipe_split_length_m=pipe_split_length_m,
        pipe_split_minimum_remainder_m=pipe_split_minimum_remainder_m,
        pipe_split_number_mode=pipe_split_number_mode,
    )
    return _analyze_source_model(source_path, model, completion)


def analyze_idf(
    path: str | Path,
    parser_path: str | Path = DEFAULT_REFERENCE_PARSER,
    **options: Any,
) -> dict[str, Any]:
    idf_path = Path(path).resolve()
    if idf_path.suffix.lower() != ".idf":
        raise ValueError(f"analyze_idf expects an .idf file: {idf_path}")
    return analyze_piping_source(idf_path, idf_parser_path=parser_path, **options)


def contract_weld_graph_to_selected(
    source: dict[str, Any], selected_weld_keys: set[str]
) -> dict[str, Any]:
    """Contract unselected weld nodes while preserving selected connectivity.

    Construction tables often number only field/erection welds while the
    IDF/PCF topology contains additional shop welds. Those unnumbered welds
    remain part of stable source topology, but they must not force an equal
    formal-ISO anchor count. This view connects two selected welds when the
    source graph contains a path between them with no selected interior node.
    """

    selected = {str(key) for key in selected_weld_keys}
    weld_by_key = {
        str(weld["weld_key"]): weld for weld in source.get("welds", [])
        if str(weld["weld_key"]) in selected
    }
    selected &= set(weld_by_key)
    adjacency: dict[str, set[str]] = {}
    for edge in source.get("weld_graph", {}).get("edges", []):
        keys = [str(value) for value in edge.get("weld_keys", [])]
        if len(keys) != 2:
            continue
        adjacency.setdefault(keys[0], set()).add(keys[1])
        adjacency.setdefault(keys[1], set()).add(keys[0])

    contracted_pairs: set[tuple[str, str]] = set()
    for start in selected:
        stack = [(neighbour, start) for neighbour in adjacency.get(start, set())]
        visited = {start}
        while stack:
            current, previous = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            if current in selected:
                contracted_pairs.add(tuple(sorted((start, current))))
                continue
            for neighbour in adjacency.get(current, set()):
                if neighbour != previous:
                    stack.append((neighbour, current))

    edges = []
    degrees = {key: 0 for key in selected}
    for left, right in sorted(contracted_pairs):
        degrees[left] += 1
        degrees[right] += 1
        left_coordinate = weld_by_key[left].get("engineering_coordinate", [])
        right_coordinate = weld_by_key[right].get("engineering_coordinate", [])
        delta = (
            [float(right_coordinate[index]) - float(left_coordinate[index]) for index in range(3)]
            if len(left_coordinate) == 3 and len(right_coordinate) == 3 else []
        )
        edges.append(
            {
                "weld_keys": [left, right],
                "connection_rule": "unselected-source-welds-contracted",
                "delta_engineering": delta,
            }
        )

    components = []
    remaining = set(selected)
    reduced_adjacency = {key: set() for key in selected}
    for left, right in contracted_pairs:
        reduced_adjacency[left].add(right)
        reduced_adjacency[right].add(left)
    while remaining:
        seed = remaining.pop()
        component = {seed}
        queue = [seed]
        while queue:
            current = queue.pop()
            for neighbour in reduced_adjacency[current] & remaining:
                remaining.remove(neighbour)
                component.add(neighbour)
                queue.append(neighbour)
        components.append(component)

    chain_orders: list[list[str]] = []
    if len(components) == 1 and all(value <= 2 for value in degrees.values()):
        endpoints = sorted(key for key, degree in degrees.items() if degree == 1)
        if len(selected) == 1:
            chain_orders = [[next(iter(selected))]]
        elif len(endpoints) == 2:
            order = [endpoints[0]]
            previous = None
            while len(order) < len(selected):
                candidates = reduced_adjacency[order[-1]] - ({previous} if previous else set())
                if not candidates:
                    break
                previous, current = order[-1], sorted(candidates)[0]
                order.append(current)
            if len(order) == len(selected):
                chain_orders = [order, list(reversed(order))]

    graph = {
        "node_count": len(selected),
        "edge_count": len(edges),
        "edges": edges,
        "degrees": degrees,
        "connected_component_count": len(components),
        "is_forest": len(edges) <= len(selected) - len(components),
        "is_tree": len(components) == 1 and len(edges) == max(0, len(selected) - 1),
        "is_simple_chain": bool(chain_orders),
        "chain_orders": chain_orders,
        "weighting": "topology-and-direction-only; unselected source welds contracted",
    }
    welds = [weld_by_key[key] for key in sorted(selected)]
    return source | {
        "welds": welds,
        "weld_count": len(welds),
        "weld_site_count": len(welds),
        "weld_graph": graph,
        "selection_contract": {
            "source_weld_count": len(source.get("welds", [])),
            "selected_weld_count": len(welds),
            "rule": "paths with no selected interior weld are contracted",
        },
    }


def analyze_pcf(
    path: str | Path,
    parser_path: str | Path = DEFAULT_REFERENCE_PCF_PARSER,
    **options: Any,
) -> dict[str, Any]:
    pcf_path = Path(path).resolve()
    if pcf_path.suffix.lower() != ".pcf":
        raise ValueError(f"analyze_pcf expects a .pcf file: {pcf_path}")
    return analyze_piping_source(pcf_path, pcf_parser_path=parser_path, **options)
