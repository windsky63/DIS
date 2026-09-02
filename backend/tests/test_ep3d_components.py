from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import fitz

from backend.engine import analyze_documents
from backend.ep3d_components import extract_design_support_callouts, extract_ep3d_component_callouts


def _framed_callout(page: fitz.Page, label: str, origin: tuple[float, float], root: tuple[float, float]) -> None:
    page.insert_text(origin, label, fontsize=10, color=(0, 0, 0))
    text_box = page.search_for(label)[-1]
    frame = text_box + (-3, -3, 3, 3)
    page.draw_rect(frame, color=(0, 0, 0), width=0.7)
    page.draw_line(
        (frame.x1, (frame.y0 + frame.y1) / 2.0),
        root,
        color=(0, 0, 0),
        width=0.7,
    )


class Ep3dComponentTests(unittest.TestCase):
    def test_design_s_frames_follow_leaders_and_merge_multiple_labels_per_support(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=360)
        target = (200, 170)
        for label, origin in (("S11", (70, 80)), ("S13", (70, 135))):
            page.insert_text(origin, label, fontsize=10, color=(0, 0, 0))
            text_box = page.search_for(label)[-1]
            frame = text_box + (-3, -3, 3, 3)
            # Deliberately export each edge independently, as in the real
            # design PDFs that motivated this regression.
            page.draw_line(frame.top_left, frame.top_right, color=(0, 0, 0), width=0.7)
            page.draw_line(frame.top_right, frame.bottom_right, color=(0, 0, 0), width=0.7)
            page.draw_line(frame.bottom_right, frame.bottom_left, color=(0, 0, 0), width=0.7)
            page.draw_line(frame.bottom_left, frame.top_left, color=(0, 0, 0), width=0.7)
            page.draw_line((frame.x1, (frame.y0 + frame.y1) / 2), target, color=(0, 0, 0), width=0.7)
        page.draw_line((190, 164), (210, 164), color=(0, 0, 0), width=0.8)
        page.draw_line((190, 176), (210, 176), color=(0, 0, 0), width=0.8)

        callouts = extract_design_support_callouts(page)
        document.close()

        self.assertEqual(len(callouts), 1)
        self.assertAlmostEqual(callouts[0].component_point[0], target[0], delta=1.0)
        self.assertTrue(any("merged-design-labels:S11,S13" == value for value in callouts[0].evidence))

    def test_framed_labels_drive_valve_flange_and_support_identity(self) -> None:
        document = fitz.open()
        page = document.new_page(width=500, height=360)
        page.draw_line((60, 230), (440, 230), color=(0, 0, 0), width=2.0)

        # Valve type 1: two triangles share the leader target / valve centre.
        page.draw_polyline(
            [(180, 230), (166, 220), (166, 240)],
            color=(0, 0, 0), width=0.8, closePath=True,
        )
        page.draw_polyline(
            [(180, 230), (194, 220), (194, 240)],
            color=(0, 0, 0), width=0.8, closePath=True,
        )
        _framed_callout(page, "V1", (70, 90), (180, 230))

        # Flange and support both use short parallel strokes; their prefixes
        # remain authoritative when the same local shape is used.
        for x in (274, 282):
            page.draw_line((x, 220), (x, 240), color=(0, 0, 0), width=0.8)
        _framed_callout(page, "FL2", (220, 80), (278, 230))

        for x in (354, 362):
            page.draw_line((x, 220), (x, 240), color=(0, 0, 0), width=0.8)
        _framed_callout(page, "SP3", (350, 90), (358, 230))

        callouts = extract_ep3d_component_callouts(page)
        document.close()

        by_label = {item.label: item for item in callouts}
        self.assertEqual(set(by_label), {"V1", "FL2", "SP3"})
        self.assertEqual(by_label["V1"].component_type, "valve")
        self.assertEqual(by_label["V1"].symbol_variant, "double-triangle-valve")
        self.assertTrue(by_label["V1"].geometry_verified)
        self.assertEqual(by_label["FL2"].symbol_variant, "parallel-line-flange")
        self.assertEqual(by_label["SP3"].symbol_variant, "parallel-line-support")
        self.assertAlmostEqual(by_label["FL2"].component_point[0], 278.0, delta=1.0)

    def test_z_arrow_valve_variant_is_validated(self) -> None:
        document = fitz.open()
        page = document.new_page(width=420, height=320)
        page.draw_line((70, 210), (350, 210), color=(0, 0, 0), width=2.0)
        page.draw_line((184, 198), (210, 198), color=(0, 0, 0), width=0.8)
        page.draw_line((184, 222), (210, 222), color=(0, 0, 0), width=0.8)
        page.draw_line((184, 222), (210, 198), color=(0, 0, 0), width=0.8)
        page.draw_polyline(
            [(194, 207), (202, 210), (196, 215)],
            color=(0, 0, 0), width=0.7, closePath=True,
        )
        _framed_callout(page, "V7", (75, 85), (198, 210))

        callouts = extract_ep3d_component_callouts(page)
        document.close()

        self.assertEqual(len(callouts), 1)
        self.assertEqual(callouts[0].symbol_variant, "z-arrow-valve")
        self.assertTrue(callouts[0].geometry_verified)

    def test_component_inventory_is_ep3d_only_and_does_not_add_design_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            design_path, ep3d_path = folder / "design.pdf", folder / "ep3d.pdf"

            design = fitz.open()
            design.new_page(width=420, height=320)
            design.save(design_path)
            design.close()

            ep3d = fitz.open()
            page = ep3d.new_page(width=420, height=320)
            page.draw_line((80, 210), (340, 210), color=(0, 0, 0), width=2.0)
            for x in (196, 204):
                page.draw_line((x, 201), (x, 219), color=(0, 0, 0), width=0.8)
            _framed_callout(page, "SP1", (80, 85), (200, 210))
            ep3d.save(ep3d_path)
            ep3d.close()

            result = analyze_documents(design_path, reference_pdfs=[ep3d_path])

        inventory = result["ep3dComponentInventory"]
        self.assertEqual(result["productName"], "图纸拓扑标识系统")
        self.assertEqual(inventory["scope"], "ep3d-reference-only")
        self.assertEqual(inventory["componentCount"], 1)
        self.assertEqual(inventory["counts"], {"valve": 0, "flange": 0, "support": 1})
        self.assertEqual(result["pages"][0]["candidateCount"], 0)


if __name__ == "__main__":
    unittest.main()
