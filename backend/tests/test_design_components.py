from __future__ import annotations

import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import fitz

from backend.design_components import (
    DesignComponentSymbol,
    _line_primitives,
    _material_valve_callouts,
    _merge_support_sources,
    _valve_material_numbers,
    extract_design_component_symbols,
)
from backend.engine import _match_design_components_to_reference, analyze_documents


def _draw_design_components(page: fitz.Page) -> None:
    black = (0, 0, 0)
    # Process centreline. CAD exports commonly retain it as a filled/stroked
    # band behind the overlaid valve contours.
    page.draw_line((60, 170), (440, 170), color=black, width=2.0)

    # Design type 1: two triangles sharing their apex plus a complete T handle.
    page.draw_polyline([(150, 158), (170, 170), (150, 182)], color=black, width=0.8, closePath=True)
    page.draw_polyline([(190, 158), (170, 170), (190, 182)], color=black, width=0.8, closePath=True)
    page.draw_line((170, 170), (170, 148), color=black, width=0.8)
    page.draw_line((164, 148), (176, 148), color=black, width=0.8)

    # Design type 2: triangular body with an internal direction arrow.
    page.draw_polyline([(300, 150), (284, 178), (316, 178)], color=black, width=0.8, closePath=True)
    page.draw_line((292, 168), (307, 168), color=black, width=0.8)
    page.draw_line((307, 168), (302, 164), color=black, width=0.8)
    page.draw_line((307, 168), (302, 172), color=black, width=0.8)

    # One closed rectangular flange on each side, centred on the main run.
    page.draw_rect(fitz.Rect(264, 167, 280, 173), color=black, width=0.8)
    page.draw_rect(fitz.Rect(322, 167, 338, 173), color=black, width=0.8)

    # Support: two short parallel lines straddling the process centreline.
    page.draw_line((374, 164), (384, 164), color=black, width=0.8)
    page.draw_line((380, 176), (390, 176), color=black, width=0.8)

    # Right-side material table: the only two supported valve descriptions.
    page.insert_text((350, 52), "8", fontsize=8)
    page.insert_text((380, 52), "LIFT CHECK", fontsize=8)
    page.insert_text((350, 72), "9", fontsize=8)
    page.insert_text((380, 72), "GATE", fontsize=8)

    # Matching boxed material numbers and their authoritative arrow endpoints.
    for label, frame, target in (
        ("8", fitz.Rect(110, 100, 124, 112), (170, 170)),
        ("9", fitz.Rect(330, 100, 344, 112), (300, 170)),
    ):
        page.draw_rect(frame, color=black, width=0.8)
        page.insert_text((frame.x0 + 4, frame.y1 - 3), label, fontsize=8)
        start = (frame.x1, (frame.y0 + frame.y1) / 2)
        page.draw_line(start, target, color=black, width=0.8)
        page.draw_polyline(
            [target, (target[0] - 5, target[1] - 1), (target[0] - 3, target[1] - 5)],
            color=black, fill=black, width=0.2, closePath=True,
        )


class DesignComponentRecognitionTests(unittest.TestCase):
    def test_material_valve_leader_allows_frame_gap_and_short_interruption(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=300)
        for label, y in (("18", 42), ("19", 62)):
            page.insert_text((350, y), label, fontsize=8)
            page.insert_text((380, y), "BALL;", fontsize=8)

        def framed_number(label, origin):
            page.insert_text(origin, label, fontsize=8)
            text_box = page.search_for(label)[-1]
            frame = text_box + (-3, -3, 3, 3)
            page.draw_rect(frame, color=(0, 0, 0), width=0.7)
            return frame

        def arrow(target):
            page.draw_polyline(
                [target, (target[0] - 5, target[1] - 2), (target[0] - 4, target[1] + 3)],
                color=(0, 0, 0), fill=(0, 0, 0), width=0.2, closePath=True,
            )

        first_frame = framed_number("18", (70, 100))
        first_target = (190, 170)
        page.draw_line(
            (first_frame.x1 + 18, first_frame.y1 + 3), first_target,
            color=(0, 0, 0), width=0.7,
        )
        arrow(first_target)

        second_frame = framed_number("19", (210, 100))
        middle = (285, 145)
        page.draw_line(
            (second_frame.x1 + 3, second_frame.y1 + 2), middle,
            color=(0, 0, 0), width=0.7,
        )
        second_target = (350, 180)
        page.draw_line(
            (middle[0] + 2.5, middle[1] + 1.3), second_target,
            color=(0, 0, 0), width=0.7,
        )
        arrow(second_target)

        callouts = _material_valve_callouts(
            page, _line_primitives(page, page.get_drawings())
        )
        document.close()

        self.assertEqual(
            [(item["label"], tuple(round(value, 1) for value in item["target"])) for item in callouts],
            [("18", first_target), ("19", second_target)],
        )

    def test_ball_material_description_is_a_design_valve_type(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=240)
        page.insert_text((350, 52), "18", fontsize=8)
        page.insert_text((380, 52), "TRUNNION-BALL;", fontsize=8)

        material_numbers = _valve_material_numbers(page)
        document.close()

        self.assertEqual(material_numbers, {"18": "ball"})

    def test_diaphragm_material_description_is_a_design_valve_type(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=240)
        page.insert_text((350, 52), "21", fontsize=8)
        page.insert_text((380, 52), "DIAPHRAGM;", fontsize=8)

        material_numbers = _valve_material_numbers(page)
        document.close()

        self.assertEqual(material_numbers, {"21": "diaphragm"})

    def test_material_leader_gap_without_stem_bridge_is_not_widened(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=300)
        page.insert_text((350, 52), "20", fontsize=8)
        page.insert_text((380, 52), "BALL;", fontsize=8)
        page.insert_text((70, 100), "20", fontsize=8)
        text_box = page.search_for("20")[-1]
        frame = text_box + (-3, -3, 3, 3)
        page.draw_rect(frame, color=(0, 0, 0), width=0.7)
        target = (240, 170)
        page.draw_line((frame.x1 + 30, frame.y1 + 2), target, color=(0, 0, 0), width=0.7)
        page.draw_polyline(
            [target, (target[0] - 5, target[1] - 2), (target[0] - 4, target[1] + 3)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.2, closePath=True,
        )

        callouts = _material_valve_callouts(
            page, _line_primitives(page, page.get_drawings())
        )
        document.close()

        self.assertEqual(callouts, [])

    def test_labelled_support_merges_offset_geometry_from_300900_page_7(self) -> None:
        def support(center, bbox, evidence):
            return DesignComponentSymbol(
                component_type="support",
                symbol_variant="parallel-line-support",
                center=center,
                bbox=bbox,
                extraction_confidence=0.9,
                evidence=(evidence,),
                source_drawing_indices=(),
                ep3d_symbol_variant="parallel-line-support",
            )

        labelled = [support(
            (245.520004, 650.279053),
            (296.88, 657.119995, 321.959991, 667.919983),
            "design-native-s-number",
        )]
        duplicate_geometry = support(
            (233.219994, 637.079987),
            (227.279984, 626.879944, 239.160004, 647.280029),
            "two-short-parallel-lines",
        )
        distinct_geometry = support(
            (270.0, 608.76),
            (264.0, 602.0, 276.0, 615.0),
            "two-short-parallel-lines",
        )

        merged = _merge_support_sources(labelled, [duplicate_geometry, distinct_geometry])

        self.assertEqual(merged, [*labelled, distinct_geometry])

    def test_component_reference_matching_uses_topology_not_candidate_order(self) -> None:
        components = [
            {"componentType": "flange", "x": 78.0, "y": 10.0},
            {"componentType": "flange", "x": 20.0, "y": 10.0},
        ]
        welds = [
            {"x": 0.0, "y": 0.0, "referenceLabel": "W1", "referenceMatched": True},
            {"x": 100.0, "y": 0.0, "referenceLabel": "W2", "referenceMatched": True},
            {"x": 0.0, "y": 100.0, "referenceLabel": "W3", "referenceMatched": True},
        ]
        reference = {
            "file": "ep3d.pdf",
            "page": 1,
            "callouts": [
                SimpleNamespace(label="W1", weld_point=(0.0, 0.0)),
                SimpleNamespace(label="W2", weld_point=(100.0, 0.0)),
                SimpleNamespace(label="W3", weld_point=(0.0, 100.0)),
            ],
            "component_callouts": [
                SimpleNamespace(label="FL1", component_type="flange", component_point=(20.0, 10.0)),
                SimpleNamespace(label="FL2", component_type="flange", component_point=(78.0, 10.0)),
            ],
        }

        matched = _match_design_components_to_reference(components, welds, reference)

        self.assertEqual(matched, 2)
        self.assertEqual([item["referenceLabel"] for item in components], ["FL2", "FL1"])
        self.assertTrue(all(item["componentMatchMethod"] == "weld-relative-topology-one-to-one" for item in components))

    def test_support_chain_is_matched_monotonically_from_a_shared_weld_anchor(self) -> None:
        components = [
            {"componentType": "support", "x": 100.0, "y": 90.0},
            {"componentType": "support", "x": 100.0, "y": 30.0},
            {"componentType": "support", "x": 100.0, "y": 60.0},
        ]
        welds = [
            {"x": 100.0, "y": 0.0, "referenceLabel": "F1", "referenceMatched": True},
        ]
        reference = {
            "file": "ep3d.pdf",
            "page": 1,
            "callouts": [SimpleNamespace(label="F1", weld_point=(0.0, 0.0))],
            "component_callouts": [
                SimpleNamespace(label="SP2", component_type="support", component_point=(50.0, 10.0)),
                SimpleNamespace(label="SP3", component_type="support", component_point=(80.0, 10.0)),
                SimpleNamespace(label="SP1", component_type="support", component_point=(20.0, 10.0)),
            ],
        }

        matched = _match_design_components_to_reference(components, welds, reference)

        self.assertEqual(matched, 3)
        self.assertEqual([item["referenceLabel"] for item in components], ["SP3", "SP1", "SP2"])
        self.assertTrue(all(
            item["componentMatchMethod"] == "weld-anchored-support-chain-one-to-one"
            for item in components
        ))
        self.assertTrue(all(item["componentMatchAnchor"] == "F1" for item in components))

    def test_material_callouts_find_both_valves_two_flanges_and_support(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=340)
        _draw_design_components(page)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        by_type = {
            component_type: [item for item in symbols if item.component_type == component_type]
            for component_type in ("valve", "flange", "support")
        }
        self.assertEqual({key: len(value) for key, value in by_type.items()}, {
            "valve": 2, "flange": 2, "support": 1,
        })
        self.assertEqual(
            {item.symbol_variant for item in by_type["valve"]},
            {"lift-check-material-callout-valve", "gate-material-callout-valve"},
        )
        self.assertTrue(all(item.ep3d_symbol_variant == "material-callout-valve" for item in by_type["valve"]))
        self.assertTrue(all(item.symbol_variant == "closed-rectangle-flange" for item in by_type["flange"]))
        self.assertEqual({tuple(round(value, 1) for value in item.center) for item in by_type["valve"]}, {
            (170.0, 170.0), (300.0, 170.0),
        })

    def test_two_parallel_mixed_path_strokes_are_not_a_flange(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((80, 110), (220, 110), color=(0, 0, 0), width=2.0)
        page.draw_line((140, 94), (152, 94), color=(0, 0, 0), width=0.8)
        page.draw_polyline(
            [(140, 104), (152, 104), (146, 107)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.2, closePath=True,
        )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "flange" for item in symbols))

    def test_closed_rectangle_on_process_line_is_a_flange_without_weld_anchor(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        page.draw_polyline(
            [(147, 95), (153, 95), (153, 109), (147, 109)],
            color=(0, 0, 0), width=0.8, closePath=True,
        )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        flanges = [item for item in symbols if item.component_type == "flange"]
        self.assertEqual(len(flanges), 1)
        self.assertEqual(flanges[0].symbol_variant, "closed-rectangle-flange")
        self.assertIn("closed-four-sided-rectangle", flanges[0].evidence)
        self.assertIn("center-on-main-process-line", flanges[0].evidence)

    def test_socket_weld_square_brackets_are_not_a_flange(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        # Two open, inward-facing square brackets around the weld circle.
        for start, end in (
            ((143, 95), (143, 109)), ((143, 95), (147, 95)), ((143, 109), (147, 109)),
            ((157, 95), (157, 109)), ((153, 95), (157, 95)), ((153, 109), (157, 109)),
        ):
            page.draw_line(start, end, color=(0, 0, 0), width=0.8)

        symbols = extract_design_component_symbols(page, {
            "recognized_weld_occluders": [
                {"id": "socket-weld", "center": [150, 102], "bbox": [147, 99, 153, 105]},
            ],
        })
        document.close()

        self.assertFalse(any(item.component_type == "flange" for item in symbols))

    def test_closed_rectangle_away_from_process_line_is_not_a_flange(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        page.draw_rect(fitz.Rect(180, 96, 196, 102), color=(0, 0, 0), width=0.8)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "flange" for item in symbols))

    def test_thin_route_through_text_free_rectangle_repairs_interrupted_skeleton(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=300)
        # The strongest process stroke ends before the component assembly. A
        # long thin CAD centreline survives through the flange itself.
        page.draw_line((50, 150), (160, 150), color=(0, 0, 0), width=2.6)
        page.draw_line((250, 150), (330, 150), color=(0, 0, 0), width=0.84)
        page.draw_rect(fitz.Rect(273, 147, 289, 153), color=(0, 0, 0), width=0.72)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        flanges = [item for item in symbols if item.component_type == "flange"]
        self.assertEqual(len(flanges), 1)
        self.assertAlmostEqual(flanges[0].center[0], 281.0, delta=0.5)
        self.assertIn("center-on-main-process-line", flanges[0].evidence)

    def test_native_text_frame_is_not_promoted_by_a_long_leader(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=300)
        page.draw_line((80, 150), (360, 150), color=(0, 0, 0), width=0.84)
        frame = fitz.Rect(210, 146, 230, 154)
        page.draw_rect(frame, color=(0, 0, 0), width=0.72)
        page.insert_text((213, 153), "9", fontsize=7)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "flange" for item in symbols))

    def test_near_square_offset_frame_is_not_a_flange(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=300)
        # Even inside the broad double-line corridor, a near-square frame does
        # not satisfy the mandatory 2:1 flange aspect ratio.
        page.draw_line((80, 160), (360, 160), color=(0, 0, 0), width=1.8)
        page.draw_rect(fitz.Rect(210, 140, 226, 152), color=(0, 0, 0), width=0.84)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "flange" for item in symbols))

    def test_boxed_f_material_arrow_adds_one_flange_without_shape_duplicate(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=300)
        page.draw_line((80, 170), (360, 170), color=(0, 0, 0), width=1.8)
        page.draw_rect(fitz.Rect(208, 167, 224, 173), color=(0, 0, 0), width=0.72)
        frame = fitz.Rect(70, 70, 145, 84)
        page.draw_rect(frame, color=(0, 0, 0), width=0.72)
        page.insert_text((75, 81), "F6 G8 B10", fontsize=8)
        target = (216, 170)
        page.draw_line((145, 77), target, color=(0, 0, 0), width=0.72)
        page.draw_polyline(
            [target, (target[0] - 6, target[1] - 2), (target[0] - 4, target[1] + 4)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.2, closePath=True,
        )

        flanges = [
            item for item in extract_design_component_symbols(page, {})
            if item.component_type == "flange"
        ]
        document.close()

        self.assertEqual(len(flanges), 1)
        self.assertEqual(flanges[0].symbol_variant, "boxed-f-material-arrow-flange")
        self.assertAlmostEqual(flanges[0].center[0], target[0], delta=0.5)
        self.assertIn("boxed-assembly-text:F6 G8 B10", flanges[0].evidence)

    def test_300900_page_7_keeps_shape_flanges_and_adds_f_callouts_without_duplicates(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = root / "300900-260903" / "300900.pdf"
        if not source.exists():
            self.skipTest("可选真实设计图 300900 未安装")

        with fitz.open(source) as document:
            flanges = [
                item for item in extract_design_component_symbols(document[6], {})
                if item.component_type == "flange"
            ]

        expected = (
            (576.78, 290.97), (508.14, 530.34), (576.78, 322.68),
            (884.10, 579.54), (914.94, 597.24),
        )
        self.assertEqual(len(flanges), 8)
        self.assertTrue(all(
            any(math.dist(item.center, center) <= 12.0 for item in flanges)
            for center in expected
        ))
        self.assertFalse(any(
            math.dist(item.center, numeric_frame) <= 2.0
            for item in flanges
            for numeric_frame in ((350.01, 559.08), (966.72, 247.68))
        ))
        labelled = [
            item for item in flanges
            if item.symbol_variant == "boxed-f-material-arrow-flange"
        ]
        geometric = [
            item for item in flanges
            if item.symbol_variant == "closed-rectangle-flange"
        ]
        self.assertEqual((len(labelled), len(geometric)), (5, 3))
        self.assertFalse(any(
            math.dist(left.center, right.center) <= 12.0
            for left in labelled for right in geometric
        ))

    def test_300900_page_10_recovers_ball_13_through_stem_north_bridge(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = root / "300900-260903" / "300900.pdf"
        if not source.exists():
            self.skipTest("可选真实设计图 300900 未安装")

        with fitz.open(source) as document:
            symbols = extract_design_component_symbols(document[9], {})

        valves = [item for item in symbols if item.component_type == "valve"]
        valve_13 = next(
            item for item in valves
            if "valve-material-number:13" in item.evidence
        )
        self.assertIn("frame-text-leader-bridge:STEM NORTH", valve_13.evidence)
        self.assertAlmostEqual(valve_13.center[0], 925.4, delta=0.75)
        self.assertAlmostEqual(valve_13.center[1], 441.7, delta=0.75)

        labelled = [
            item for item in symbols
            if item.symbol_variant == "boxed-f-material-arrow-flange"
        ]
        geometric = [
            item for item in symbols
            if item.symbol_variant == "closed-rectangle-flange"
        ]
        self.assertEqual(len(labelled), 5)
        self.assertFalse(any(
            math.dist(left.center, right.center) <= 12.0
            for left in labelled for right in geometric
        ))

    def test_shape_alone_does_not_create_valve_with_internal_fill(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((50, 110), (250, 110), color=(0, 0, 0), width=2.0)
        page.draw_polyline([(120, 98), (140, 110), (120, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        page.draw_polyline([(160, 98), (140, 110), (160, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        page.draw_line((140, 110), (140, 88), color=(0, 0, 0), width=0.8)
        page.draw_line((134, 88), (146, 88), color=(0, 0, 0), width=0.8)
        page.draw_polyline(
            [(126, 105), (136, 110), (126, 115)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.4, closePath=True,
        )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_shape_alone_does_not_create_valve_with_edge_aligned_fill(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((50, 110), (250, 110), color=(0, 0, 0), width=2.0)
        page.draw_polyline([(120, 98), (140, 110), (120, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        page.draw_polyline([(160, 98), (140, 110), (160, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        page.draw_line((140, 110), (140, 88), color=(0, 0, 0), width=0.8)
        page.draw_line((134, 88), (146, 88), color=(0, 0, 0), width=0.8)
        # Common CAD export: the solid arrow fill is a second path exactly on
        # top of one outlined triangle, so none of its edges are inset.
        page.draw_polyline(
            [(120, 98), (140, 110), (120, 122)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.4, closePath=True,
        )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_closed_double_triangle_without_t_handle_is_not_a_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((50, 110), (250, 110), color=(0, 0, 0), width=2.0)
        page.draw_polyline([(120, 98), (140, 110), (120, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        page.draw_polyline([(160, 98), (140, 110), (160, 122)], color=(0, 0, 0), width=0.8, closePath=True)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_nested_outline_flow_arrow_is_not_a_material_callout_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        # Some CAD printers simulate a solid arrow by repeatedly stroking
        # nested triangles instead of setting the PDF fill attribute.
        for inset in (0.0, 1.0, 2.0, 3.0):
            page.draw_polyline(
                [(144 + inset, 82 + inset), (156 - inset, 82 + inset), (150, 102 - inset)],
                color=(0, 0, 0), width=0.85, closePath=True,
            )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_asymmetric_outline_arrow_is_not_a_material_callout_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        # Preserve the top tip while moving the lower corners inward.  This
        # mirrors CAD output where the de-duplicated candidate covers only a
        # narrow half of the complete nested arrow family.
        for inset in (0.0, 2.0, 4.0, 6.0):
            page.draw_polyline(
                [(150, 75), (142 + inset, 105 - inset), (158 - inset, 105 - inset)],
                color=(0, 0, 0), width=0.85, closePath=True,
            )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_longitudinally_disjoint_parallel_strokes_are_not_a_flange(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((140, 70), (140, 82), color=(0, 0, 0), width=0.8)
        page.draw_polyline(
            [(148, 94), (148, 106), (151, 100)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.2, closePath=True,
        )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "flange" for item in symbols))

    def test_tiny_drafting_fragments_are_not_a_double_triangle_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((50, 110), (250, 110), color=(0, 0, 0), width=2.0)
        page.draw_polyline([(149.4, 109.0), (150.0, 110.0), (149.4, 111.0)], color=(0, 0, 0), width=0.25, closePath=True)
        page.draw_polyline([(150.6, 109.0), (150.0, 110.0), (150.6, 111.0)], color=(0, 0, 0), width=0.25, closePath=True)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_recognized_weld_drawings_are_removed_before_valve_recognition(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        page.draw_polyline(
            [(140, 90), (150, 110), (140, 130)],
            color=(0, 0, 0), width=0.8, closePath=True,
        )
        page.draw_polyline(
            [(160, 90), (150, 110), (160, 130)],
            color=(0, 0, 0), width=0.8, closePath=True,
        )
        features = {
            "recognized_weld_occluders": [
                {
                    "id": "w1",
                    "center": [150, 110],
                    "bbox": [140, 90, 160, 130],
                    "source_drawing_indices": [1],
                    "modifier_drawing_indices": [2],
                },
            ],
        }

        symbols = extract_design_component_symbols(page, features)
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_open_double_triangle_outline_is_not_a_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        for start, end in (
            ((140, 90), (150, 110)), ((160, 90), (150, 110)),
            ((150, 110), (140, 130)), ((150, 110), (160, 130)),
        ):
            page.draw_line(start, end, color=(0, 0, 0), width=0.8)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_3518u_01_mainline_arrows_and_tiny_v7_are_excluded(self) -> None:
        root = Path(__file__).resolve().parents[2]
        target_page = None
        document = None
        for path in root.glob("data/jobs/*/target.pdf"):
            candidate = fitz.open(path)
            for page in candidate:
                if "00-42-0207-26-NG-3518U-01" in page.get_text():
                    document, target_page = candidate, page
                    break
            if target_page is not None:
                break
            candidate.close()
        if target_page is None or document is None:
            self.skipTest("可选真实设计图 00-42-0207-26-NG-3518U-01 未安装")

        try:
            valves = [
                item for item in extract_design_component_symbols(target_page, {})
                if item.component_type == "valve"
            ]
        finally:
            document.close()

        self.assertFalse(any(item.center[1] < 520.0 for item in valves))
        self.assertFalse(any(
            max(item.bbox[2] - item.bbox[0], item.bbox[3] - item.bbox[1]) < 6.0
            for item in valves
        ))
        self.assertFalse(any(math.dist(item.center, (566.436, 780.318)) < 4.0 for item in valves))

    def test_reported_3518u_01_fl3_false_positive_is_excluded(self) -> None:
        root = Path(__file__).resolve().parents[2]
        target_paths = list(root.glob("data/jobs/*/target.pdf"))
        if not target_paths:
            self.skipTest("可选真实设计图批次未安装")

        with fitz.open(target_paths[0]) as document:
            if len(document) < 9:
                self.skipTest("可选真实设计图批次页数不足")
            page_3518u_01 = extract_design_component_symbols(document[6], {})

        self.assertFalse(any(
            item.component_type == "flange"
            and math.dist(item.center, (561.96, 777.477)) < 4.0
            for item in page_3518u_01
        ))

    def test_target_page_7_recovers_two_closed_flanges_around_the_valve(self) -> None:
        root = Path(__file__).resolve().parents[2]
        target_paths = list(root.glob("data/jobs/*/target.pdf"))
        if not target_paths:
            self.skipTest("可选真实设计图批次未安装")

        with fitz.open(target_paths[0]) as document:
            if len(document) < 7:
                self.skipTest("可选真实设计图批次页数不足")
            if "00-42-0207-26-NG-3518U-01" not in document[6].get_text():
                self.skipTest("当前安装的真实设计图不是该法兰回归样本")
            flanges = [
                item for item in extract_design_component_symbols(document[6], {})
                if item.component_type == "flange"
            ]

        expected = ((562.92, 609.30), (562.92, 640.98))
        self.assertEqual(len(flanges), 2)
        self.assertTrue(all(
            any(math.dist(item.center, center) <= 1.0 for item in flanges)
            for center in expected
        ))
        self.assertTrue(all("closed-four-sided-rectangle" in item.evidence for item in flanges))

    def test_placement_mode_exposes_numbering_targets_with_blue_square_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            source = Path(folder_name) / "design.pdf"
            document = fitz.open()
            page = document.new_page(width=500, height=340)
            _draw_design_components(page)
            document.save(source)
            document.close()

            result = analyze_documents(source, symbol_config={"detectionMode": "placement"})

        page_result = result["pages"][0]
        components = page_result["designComponents"]
        self.assertEqual(page_result["designComponentCount"], 5)
        self.assertEqual(page_result["weldCandidateCount"], 0)
        self.assertEqual(page_result["candidateCount"], 5)
        self.assertEqual(result["designComponentInventory"]["counts"], {
            "valve": 2, "flange": 2, "support": 1,
        })
        self.assertEqual({item["autoNumberPrefix"] for item in components}, {"V", "FL", "SP"})
        self.assertTrue(all(item["defaultMarkerStyle"] == {
            "shape": "rectangle", "color": "#1769d2",
        } for item in components))
        self.assertTrue(all(item["componentKind"] == "design-component" for item in components))

    def test_comparison_mode_does_not_run_design_shape_recognition(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            source = Path(folder_name) / "design.pdf"
            document = fitz.open()
            page = document.new_page(width=500, height=340)
            _draw_design_components(page)
            document.save(source)
            document.close()

            result = analyze_documents(source, symbol_config={"detectionMode": "comparison"})

        self.assertEqual(result["pages"][0]["designComponentCount"], 0)
        self.assertEqual(result["designComponentInventory"]["componentCount"], 0)


if __name__ == "__main__":
    unittest.main()
