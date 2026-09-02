from __future__ import annotations

from pathlib import Path
import json
import math
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import fitz


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import engine
from engine import (
    _constrained_composite_anchor_pool,
    _drawing_is_explicitly_dashed,
    _effective_symbol_config,
    _match_reference,
    _page_candidates,
    _placement_reference_page,
    _parse_pcf_inventory,
    _placement_symbol_anchors,
    _primary_page_reference,
    _reference_constrained_candidates,
    analyze_documents,
    write_annotated_pdf,
)
from iso_weld_matcher.pdf_vectors import analyze_page


class EngineTests(unittest.TestCase):
    @staticmethod
    def _symbol_page(*, with_x: bool = False, with_brackets: bool = False) -> fitz.Document:
        document = fitz.open()
        page = document.new_page(width=400, height=300)
        page.draw_line((100, 150), (300, 150), color=(0, 0, 0), width=2)
        page.draw_circle((200, 150), 6, color=(0, 0, 0), fill=(0, 0, 0), width=0.8)
        if with_x:
            page.draw_line((196, 146), (204, 154), color=(0, 0, 0), width=0.8)
            page.draw_line((204, 146), (196, 154), color=(0, 0, 0), width=0.8)
        if with_brackets:
            page.draw_line((190, 143), (190, 157), color=(0, 0, 0), width=0.8)
            page.draw_line((210, 143), (210, 157), color=(0, 0, 0), width=0.8)
            for x_value, direction in ((190, 1), (210, -1)):
                page.draw_line((x_value, 143), (x_value + direction * 5, 143), color=(0, 0, 0), width=0.8)
                page.draw_line((x_value, 157), (x_value + direction * 5, 157), color=(0, 0, 0), width=0.8)
        return document

    def test_placement_mode_detects_configurable_black_circle_symbols(self) -> None:
        for expected_shape, options in (
            ("black-circle", {}),
            ("black-circle-x", {"with_x": True}),
            ("black-circle-bracket", {"with_brackets": True}),
        ):
            document = self._symbol_page(**options)
            features = analyze_page(document[0])
            anchors = _placement_symbol_anchors(
                document[0], features, _effective_symbol_config(None)
            )
            document.close()
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0]["symbol_shape"], expected_shape)
            self.assertEqual(anchors[0]["circle_encoding"], "bezier-circle")

    def test_low_edge_approximate_circle_is_disabled_by_default(self) -> None:
        document = fitz.open()
        page = document.new_page(width=400, height=300)
        page.draw_line((100, 150), (300, 150), color=(0, 0, 0), width=2)
        page.draw_polyline([
            (194, 150), (197, 145), (203, 145),
            (206, 150), (203, 155), (197, 155),
        ], color=(0, 0, 0), fill=(0, 0, 0), width=0.8, closePath=True)
        features = analyze_page(page)

        strict = _placement_symbol_anchors(page, features, _effective_symbol_config(None))
        supplemental = _placement_symbol_anchors(page, features, _effective_symbol_config({
            "placementSymbols": {"approximateCircleEnabled": True}
        }))
        document.close()

        self.assertEqual(strict, [])
        self.assertEqual(len(supplemental), 1)
        self.assertEqual(supplemental[0]["circle_encoding"], "low-edge-approximate-circle")

    def test_dense_circular_pdf_polyline_is_classified_by_roundness(self) -> None:
        document = fitz.open()
        page = document.new_page(width=400, height=300)
        page.draw_line((100, 150), (300, 150), color=(0, 0, 0), width=2)
        points = [
            (200 + 6 * math.cos(index * math.tau / 32), 150 + 6 * math.sin(index * math.tau / 32))
            for index in range(32)
        ]
        page.draw_polyline(points, color=(0, 0, 0), fill=(0, 0, 0), width=0.8, closePath=True)
        features = analyze_page(page)
        anchors = _placement_symbol_anchors(page, features, _effective_symbol_config(None))
        document.close()

        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["circle_encoding"], "dense-polyline-circle")

    def test_placement_shape_switch_can_exclude_prefabricated_x(self) -> None:
        document = self._symbol_page(with_x=True)
        features = analyze_page(document[0])
        config = _effective_symbol_config({
            "placementSymbols": {"prefabricatedXEnabled": False}
        })
        anchors = _placement_symbol_anchors(document[0], features, config)
        document.close()
        self.assertEqual(anchors, [])

    def test_complete_symbol_on_weak_terminal_segment_is_still_detected(self) -> None:
        document = self._symbol_page()
        features = analyze_page(document[0])
        features["strong_process_segments"] = []
        anchors = _placement_symbol_anchors(
            document[0], features, _effective_symbol_config(None)
        )
        document.close()
        self.assertEqual(len(anchors), 1)

    def test_title_block_reference_wins_over_see_iso_reference(self) -> None:
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_text((80, 180), "LINE-OTHER", fontsize=10)
        page.insert_text((380, 740), "LINE-MAIN", fontsize=10)
        references = [
            {"file": "LINE-OTHER.pdf", "callouts": []},
            {"file": "LINE-MAIN.pdf", "callouts": []},
        ]
        selected = _primary_page_reference(page, references)
        document.close()
        self.assertIs(selected, references[1])

    def test_reference_prefix_is_identity_only_in_placement(self) -> None:
        reference = {
            "callouts": [
                SimpleNamespace(label="F40"),
                SimpleNamespace(label="FS41"),
                SimpleNamespace(label="f42"),
                SimpleNamespace(label="fs43"),
            ]
        }
        placement_reference, identity_only = _placement_reference_page(reference)
        self.assertEqual(
            [item.label for item in placement_reference["callouts"]],
            ["F40", "FS41", "f42", "fs43"],
        )
        self.assertEqual(identity_only, ["FS41", "fs43"])
        comparison_pattern = re.compile(
            _effective_symbol_config(None)["comparisonSymbols"]["redLabelPattern"], re.IGNORECASE
        )
        self.assertIsNotNone(comparison_pattern.fullmatch("FS41"))

    def test_pdf_dash_style_not_reference_prefix_controls_placement(self) -> None:
        self.assertFalse(_drawing_is_explicitly_dashed({"dashes": ""}))
        self.assertFalse(_drawing_is_explicitly_dashed({"dashes": "[] 0"}))
        self.assertTrue(_drawing_is_explicitly_dashed({"dashes": "[3 2] 0"}))

        document = fitz.open()
        page = document.new_page(width=400, height=300)
        candidates = [
            {
                "id": "solid-fs-prefix",
                "confidence": 0.99,
                "glyphValidated": True,
                "strokePattern": "solid",
                "evidence": "visual",
                "referenceMatched": True,
                "referenceLabel": "FS41",
                "matchConfidence": "high",
            },
            {
                "id": "visual-dashed-f-prefix",
                "confidence": 0.99,
                "glyphValidated": True,
                "strokePattern": "dashed",
                "comparisonOnlyVisual": True,
                "evidence": "visual",
                "referenceMatched": True,
                "referenceLabel": "F42",
                "matchConfidence": "high",
            },
        ]
        reference_page = {"file": "LINE.pdf", "callouts": [object(), object()]}
        with patch.object(engine, "_match_reference", return_value={"status": "matched"}):
            selected, reference = _reference_constrained_candidates(page, candidates, reference_page)
        document.close()

        self.assertEqual([candidate["id"] for candidate in selected], ["solid-fs-prefix"])
        self.assertEqual(reference["excludedVisualDashedReferenceLabels"], ["F42"])
        self.assertEqual(reference["expectedCalloutCount"], 1)
        self.assertEqual(reference["unresolvedCalloutGap"], 0)

    def test_reference_cardinality_does_not_fill_with_unmatched_shapes(self) -> None:
        document = fitz.open()
        page = document.new_page(width=400, height=300)
        candidates = [
            {"id": "physical-1", "confidence": 0.99, "glyphValidated": True,
             "evidence": "visual", "referenceMatched": False},
            {"id": "physical-2", "confidence": 0.98, "glyphValidated": True,
             "evidence": "visual", "referenceMatched": False},
            {"id": "arrow", "confidence": 0.90, "glyphValidated": False,
             "evidence": "topology-recovery:component-boundary", "referenceMatched": True,
             "referenceLabel": "F-ARROW", "matchConfidence": "high"},
        ]
        reference_page = {"file": "LINE.pdf", "callouts": [object(), object(), object()]}
        with patch.object(engine, "_match_reference", return_value={"status": "no-match"}):
            selected, reference = _reference_constrained_candidates(
                page, candidates, reference_page
            )
        document.close()
        self.assertEqual(len(selected), 2)
        self.assertEqual(reference["expectedCalloutCount"], 3)
        self.assertEqual(reference["signatureCompletedCount"], 0)
        self.assertEqual(reference["unresolvedCalloutGap"], 1)
        self.assertEqual(reference["rejectedArrowRiskRecoveryCount"], 1)
        self.assertEqual(reference["rejectedArrowRiskLabels"], ["F-ARROW"])

    def test_single_triangle_projecting_to_pipe_is_not_a_weld(self) -> None:
        document = fitz.open()
        page = document.new_page(width=400, height=300)
        page.draw_line((80, 150), (320, 150), color=(0, 0, 0), width=2)
        page.draw_polyline(
            [(194, 143), (206, 143), (200, 150)],
            color=(0, 0, 0), fill=(0, 0, 0), width=0.5, closePath=True,
        )
        features = analyze_page(page)
        pool = _constrained_composite_anchor_pool(page, features, _effective_symbol_config(None))
        document.close()
        self.assertFalse(any("triangle" in str(item.get("symbol_shape")) for item in pool))

    def test_comparison_mode_reads_red_frame_label_directly(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            source = Path(folder_name) / "red-callout.pdf"
            document = fitz.open()
            page = document.new_page(width=400, height=300)
            page.insert_text((100, 95), "F1", fontsize=12, color=(1, 0, 0))
            label = page.search_for("F1")[0]
            page.draw_rect(label + (-2, -2, 2, 2), color=(1, 0, 0), width=1)
            page.draw_line((label.x1 + 1, (label.y0 + label.y1) / 2), (210, 150), color=(1, 0, 0), width=1)
            document.save(source)
            document.close()

            result = analyze_documents(source, symbol_config={"detectionMode": "comparison"})

            self.assertEqual(result["detectionMode"], "comparison")
            self.assertEqual(result["pages"][0]["candidateCount"], 1)
            self.assertEqual(result["pages"][0]["candidates"][0]["referenceLabel"], "F1")
            self.assertEqual(result["pages"][0]["candidates"][0]["symbolShape"], "red-frame")

    def test_vector_scan_and_annotation_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            source = folder / "source.pdf"
            document = fitz.open()
            page = document.new_page(width=600, height=400)
            page.draw_line((80, 200), (520, 200), color=(0, 0, 0), width=2)
            page.draw_circle((300, 200), 7, color=(0, 0, 0), fill=(0, 0, 0))
            document.save(source)
            document.close()

            progress_messages = []
            published_pages = []
            result = analyze_documents(
                source,
                progress_callback=progress_messages.append,
                page_callback=published_pages.append,
            )
            self.assertEqual(result["schema"], "weld-marker.topology.v1")
            self.assertEqual(result["pageCount"], 1)
            self.assertEqual(len(result["pages"]), 1)
            self.assertEqual([page["page"] for page in published_pages], [1])
            self.assertEqual(published_pages[0]["candidateCount"], result["pages"][0]["candidateCount"])
            self.assertTrue(any("研究设计图页面 1/1" in message for message in progress_messages))
            self.assertTrue(any("候选焊口" in message for message in progress_messages))

            output = folder / "annotated.pdf"
            editable_result = {"schema": "weld-marker.editable.v1", "pages": [{"page": 1, "candidates": []}]}
            write_annotated_pdf(source, output, [{
                "page": 1, "x": 300, "y": 200, "labelX": 360, "labelY": 150,
                "number": "W-01A", "included": True
            }], editable_result)
            with fitz.open(output) as annotated:
                self.assertEqual(annotated.page_count, 1)
                self.assertIn("W-01A", annotated[0].get_text())
                drawings = annotated[0].get_drawings()
                self.assertTrue(any(item["rect"].contains(fitz.Point(300, 200)) for item in drawings))
                frame_drawings = [item for item in drawings if item["rect"].contains(fitz.Point(360, 150))]
                self.assertTrue(frame_drawings)
                self.assertFalse(any(
                    item.get("fill") is not None and item["rect"].width < 10 and item["rect"].height < 10
                    and item["rect"].contains(fitz.Point(300, 200))
                    for item in drawings
                ), "the weld coordinate must not be covered by an endpoint glyph")
                self.assertIn("weld-marker-result.json", annotated.embfile_names())
                embedded = json.loads(annotated.embfile_get("weld-marker-result.json").decode("utf-8"))
                self.assertEqual(embedded["schema"], "weld-marker.editable.v1")

    def test_annotation_shape_configurations(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            source = folder / "source.pdf"
            document = fitz.open()
            document.new_page(width=400, height=300)
            document.save(source)
            document.close()
            for shape in ("circle", "diamond", "rectangle"):
                output = folder / f"{shape}.pdf"
                write_annotated_pdf(source, output, [{
                    "page": 1, "x": 120, "y": 160, "labelX": 190, "labelY": 110,
                    "number": f"{shape[:1].upper()}-12", "included": True,
                    "markerStyle": {"shape": shape, "frameSize": 32, "fontSize": 11,
                                    "lineWidth": 1.6, "color": "#245f9e", "fillOpacity": 0.4},
                }])
                with fitz.open(output) as annotated:
                    self.assertIn("-12", annotated[0].get_text())
                    self.assertGreaterEqual(len(annotated[0].get_drawings()), 2)

    def test_internal_pcf_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            path = Path(folder_name) / "line.pcf"
            path.write_text(
                "PIPELINE-REFERENCE LINE-100\n"
                "PIPE\n"
                "    COMPONENT-IDENTIFIER 10\n"
                "    END-POINT 0 0 0 50\n"
                "    END-POINT 100 200 300 50\n"
                "    SKEY PIPE\n"
                "WELD\n"
                "    MASTER-COMPONENT-IDENTIFIER 10\n"
                "    END-POINT 100 200 300 50\n"
                "    WELD-REMARK-NUMBER W-01\n"
                "    UCI {WELD-1}\n",
                encoding="utf-8",
            )
            result = _parse_pcf_inventory(path)
            self.assertEqual(result["pipeline"], "LINE-100")
            self.assertEqual(result["weld_site_count"], 1)
            self.assertEqual(result["welds"][0]["weld_key"], "W-01")
            self.assertEqual(result["welds"][0]["engineering_coordinate"], [100.0, 200.0, 300.0])
            self.assertEqual(result["welds"][0]["adjacent_component_types"], ["pipe"])
            self.assertEqual(result["welds"][0]["source_weld_id"], "{WELD-1}")

    def test_multiple_reference_pdfs_with_pcf_supplement(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            target = folder / "design.pdf"
            references = [folder / "line-a.pdf", folder / "line-b.pdf"]
            for path in [target, *references]:
                document = fitz.open()
                document.new_page(width=400, height=300)
                document.save(path)
                document.close()
            pcf = folder / "lines.pcf"
            pcf.write_text("PIPELINE-REFERENCE LINES\nWELD\n    WELD-NUMBER W-01\n", encoding="utf-8")

            result = analyze_documents(target, reference_pdfs=references, pcf_file=pcf)

            self.assertEqual(result["referenceMode"], "reference-pdf-folder+pcf")
            self.assertEqual(result["referenceFiles"], ["line-a.pdf", "line-b.pdf"])
            self.assertIn("pcf", result)
            self.assertEqual(result["pcf"]["weldCount"], 1)
            self.assertEqual(result["pages"][0]["pcfResearchFile"], "lines.pcf")

    def test_one_design_page_can_merge_matches_from_multiple_reference_files(self) -> None:
        candidates = [
            {"x": 10.0, "y": 10.0, "confidence": 0.9},
            {"x": 30.0, "y": 30.0, "confidence": 0.9},
        ]
        references = [
            {"file": "line-a.pdf", "page": 1, "callouts": [object()], "skeleton": "A", "landmarks": []},
            {"file": "line-b.pdf", "page": 1, "callouts": [object()], "skeleton": "B", "landmarks": []},
        ]

        def fake_match(*args, **kwargs):
            index = 0 if kwargs["ep3d_skeleton"] == "A" else 1
            return {
                "status": "matched",
                "matches": [{"design_index": index, "ep3d_label": f"W-{index + 1}", "confidence": "high"}],
                "summary": {"high_or_medium_count": 1},
            }

        with patch.object(engine, "extract_process_skeleton", return_value="design"), \
                patch.object(engine, "extract_stable_landmarks", return_value=[]), \
                patch.object(engine, "match_weld_callout_topology", side_effect=fake_match):
            result = _match_reference(object(), candidates, references)

        self.assertEqual(result["acceptedMatchCount"], 2)
        self.assertEqual(result["referenceFiles"], ["line-a.pdf", "line-b.pdf"])
        self.assertEqual([item["referenceFile"] for item in candidates], ["line-a.pdf", "line-b.pdf"])

    def test_folder_mode_aggregates_multiple_pcf_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            target = folder / "design.pdf"
            document = fitz.open()
            document.new_page(width=400, height=300)
            document.save(target)
            document.close()
            pcfs = []
            for index in (1, 2):
                path = folder / f"line-{index}.pcf"
                path.write_text(
                    f"PIPELINE-REFERENCE LINE-{index}\nWELD\n    WELD-NUMBER W-{index:02d}\n",
                    encoding="utf-8",
                )
                pcfs.append(path)

            result = analyze_documents(target, pcf_files=pcfs)

            self.assertEqual(result["referenceMode"], "pcf")
            self.assertEqual(result["pcf"]["weldCount"], 2)
            self.assertEqual([item["file"] for item in result["pcf"]["files"]], ["line-1.pcf", "line-2.pcf"])

    def test_page_candidates_preserve_research_selected_anchor_set(self) -> None:
        features = {
            "display_size": [400, 300],
            "fixed_weld_symbol_candidates": [
                {"process_coordinate": [20, 20], "confidence": 1.0, "glyph_validated": True},
                {"process_coordinate": [40, 40], "confidence": 0.97, "glyph_validated": False},
            ],
            "weld_anchor_candidates": [{"process_coordinate": [60, 60], "confidence": 0.5}],
        }

        selected = _page_candidates(features, 1)
        filtered = _page_candidates(features, 1, {"minimumConfidence": 0.6})

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["x"], 60)
        self.assertEqual(filtered, [])


if __name__ == "__main__":
    unittest.main()
