from __future__ import annotations

import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import fitz

from backend.design_components import extract_design_component_symbols
from backend.engine import _match_design_components_to_reference, analyze_documents


def _draw_design_components(page: fitz.Page) -> None:
    black = (0, 0, 0)
    # Process centreline. CAD exports commonly retain it as a filled/stroked
    # band behind the overlaid valve contours.
    page.draw_line((60, 170), (440, 170), color=black, width=2.0)

    # Design type 1: two triangles sharing their apex.
    page.draw_polyline([(150, 158), (170, 170), (150, 182)], color=black, width=0.8, closePath=True)
    page.draw_polyline([(190, 158), (170, 170), (190, 182)], color=black, width=0.8, closePath=True)

    # Design type 2: triangular body with an internal direction arrow.
    page.draw_polyline([(300, 150), (284, 178), (316, 178)], color=black, width=0.8, closePath=True)
    page.draw_line((292, 168), (307, 168), color=black, width=0.8)
    page.draw_line((307, 168), (302, 164), color=black, width=0.8)
    page.draw_line((307, 168), (302, 172), color=black, width=0.8)

    # One rectangular flange on each side, represented by its two short
    # process-parallel sides and directly connected to the main run.
    for y in (164, 176):
        page.draw_line((266, y), (276, y), color=black, width=0.8)
        page.draw_line((324, y), (334, y), color=black, width=0.8)

    # Support: two short parallel lines straddling the process centreline.
    page.draw_line((374, 164), (384, 164), color=black, width=0.8)
    page.draw_line((380, 176), (390, 176), color=black, width=0.8)


class DesignComponentRecognitionTests(unittest.TestCase):
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

    def test_shape_only_detector_finds_both_valves_two_flanges_and_support(self) -> None:
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
            {"double-triangle-valve", "triangle-arrow-valve"},
        )
        composite = next(item for item in by_type["valve"] if item.symbol_variant == "triangle-arrow-valve")
        self.assertEqual(composite.ep3d_symbol_variant, "z-arrow-valve")
        self.assertTrue(all(item.assembly_key == composite.assembly_key for item in by_type["flange"]))
        double_triangle = next(item for item in by_type["valve"] if item.symbol_variant == "double-triangle-valve")
        self.assertAlmostEqual(double_triangle.center[0], 170.0, delta=0.2)
        self.assertAlmostEqual(double_triangle.center[1], 170.0, delta=0.2)

    def test_independent_mixed_path_flange_does_not_require_a_valve(self) -> None:
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

        flanges = [item for item in symbols if item.component_type == "flange"]
        self.assertEqual(len(flanges), 1)
        self.assertEqual(flanges[0].symbol_variant, "independent-parallel-line-flange")
        self.assertIn("not-dependent-on-valve-presence", flanges[0].evidence)

    def test_double_triangle_with_internal_filled_triangle_is_not_a_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((50, 110), (250, 110), color=(0, 0, 0), width=2.0)
        page.draw_polyline([(120, 98), (140, 110), (120, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        page.draw_polyline([(160, 98), (140, 110), (160, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        page.draw_polyline(
            [(126, 105), (136, 110), (126, 115)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.4, closePath=True,
        )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_double_triangle_with_edge_aligned_solid_arrow_is_not_a_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((50, 110), (250, 110), color=(0, 0, 0), width=2.0)
        page.draw_polyline([(120, 98), (140, 110), (120, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        page.draw_polyline([(160, 98), (140, 110), (160, 122)], color=(0, 0, 0), width=0.8, closePath=True)
        # Common CAD export: the solid arrow fill is a second path exactly on
        # top of one outlined triangle, so none of its edges are inset.
        page.draw_polyline(
            [(120, 98), (140, 110), (120, 122)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.4, closePath=True,
        )

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_nested_outline_flow_arrow_is_not_a_valve(self) -> None:
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

    def test_tiny_drafting_fragments_are_not_a_double_triangle_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((50, 110), (250, 110), color=(0, 0, 0), width=2.0)
        page.draw_polyline([(149.4, 109.0), (150.0, 110.0), (149.4, 111.0)], color=(0, 0, 0), width=0.25, closePath=True)
        page.draw_polyline([(150.6, 109.0), (150.0, 110.0), (150.6, 111.0)], color=(0, 0, 0), width=0.25, closePath=True)

        symbols = extract_design_component_symbols(page, {})
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_two_recognized_welds_bridge_an_occluded_gate_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        for start, end in (
            ((140, 90), (150, 110)), ((160, 90), (150, 110)),
            ((150, 110), (140, 130)), ((150, 110), (160, 130)),
        ):
            page.draw_line(start, end, color=(0, 0, 0), width=0.8)
        features = {
            "recognized_weld_occluders": [
                {"id": "w1", "center": [150, 90], "bbox": [147, 87, 153, 93]},
                {"id": "w2", "center": [150, 130], "bbox": [147, 127, 153, 133]},
            ],
        }

        symbols = extract_design_component_symbols(page, features)
        document.close()

        valves = [item for item in symbols if item.component_type == "valve"]
        self.assertEqual(len(valves), 1)
        self.assertIn("two-recognized-weld-occluders", valves[0].evidence)

    def test_two_recognized_welds_without_body_geometry_do_not_create_a_valve(self) -> None:
        document = fitz.open()
        page = document.new_page(width=300, height=220)
        page.draw_line((150, 40), (150, 180), color=(0, 0, 0), width=2.0)
        features = {
            "recognized_weld_occluders": [
                {"id": "w1", "center": [150, 90], "bbox": [147, 87, 153, 93]},
                {"id": "w2", "center": [150, 130], "bbox": [147, 127, 153, 133]},
            ],
        }

        symbols = extract_design_component_symbols(page, features)
        document.close()

        self.assertFalse(any(item.component_type == "valve" for item in symbols))

    def test_named_production_drawings_keep_reported_valve_and_flange_regressions(self) -> None:
        root = Path(__file__).resolve().parents[2]
        expectations = {
            "00-42-0207-26-NG-3518U-03.pdf": {"flange": 1},
            "00-42-0207-26-NG-3518U-06.pdf": {"flange": 2, "valve": 1},
            "00-42-0207-26-PW-0003-01.pdf": {"flange": 3, "valve": 1},
        }
        missing = [file_name for file_name in expectations if not any(root.rglob(file_name))]
        if missing:
            self.skipTest(f"可选生产图纸回归样本未安装：{', '.join(missing)}")
        for file_name, expected in expectations.items():
            path = next(root.rglob(file_name))
            with self.subTest(file=file_name), fitz.open(path) as document:
                symbols = extract_design_component_symbols(document[0], {})
                counts = {
                    component_type: sum(item.component_type == component_type for item in symbols)
                    for component_type in expected
                }
                for component_type, minimum in expected.items():
                    self.assertGreaterEqual(counts[component_type], minimum)
                if file_name.endswith("3518U-06.pdf"):
                    valve = next(item for item in symbols if item.component_type == "valve")
                    self.assertAlmostEqual(valve.center[0], 660.68, delta=0.5)
                    self.assertAlmostEqual(valve.center[1], 612.0, delta=0.5)

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
