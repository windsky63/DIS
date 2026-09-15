from __future__ import annotations

import importlib
import math
import copy
from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class LabelLayoutTests(unittest.TestCase):
    def test_backend_optimization_updates_every_page_without_completion_flag(self) -> None:
        try:
            layout = importlib.import_module("label_layout")
        except ImportError:
            self.fail("backend label layout optimizer is missing")
        result = {"pages": [
            {
                "page": page, "width": 300, "height": 200,
                "candidates": [{
                    "id": f"w{page}", "number": str(page), "x": 100, "y": 100,
                    "labelX": 250, "labelY": 180, "included": True,
                }],
                "layoutObstacles": {"textRects": [], "processSegments": []},
            }
            for page in (1, 2)
        ]}

        totals = layout.optimize_result_label_positions(result)

        self.assertEqual(totals["placed"], 2)
        self.assertTrue(all(page["candidates"][0]["labelX"] != 250 for page in result["pages"]))
        self.assertNotIn("labelLayout", result)

    def test_dense_layout_reports_repairs_and_respects_emergency_leader_cap(self) -> None:
        layout = importlib.import_module("label_layout")
        page = {
            "page": 1,
            "width": 640,
            "height": 420,
            "candidates": [
                {
                    "id": f"dense-{index}",
                    "number": str(index + 1),
                    "x": 300 + (index % 3) * 9,
                    "y": 195 + (index // 3) * 10,
                    "included": True,
                    "markerStyle": {"shape": "circle", "frameSize": 20, "fontSize": 12},
                }
                for index in range(9)
            ],
            "layoutObstacles": {
                "textRects": [[245, 150, 375, 180], [245, 230, 375, 260]],
                "processSegments": [
                    {"start": [180, 200], "end": [460, 200]},
                    {"start": [180, 220], "end": [460, 220]},
                ],
            },
        }

        totals = layout.reflow_label_positions([page])

        self.assertEqual(totals["placed"], 9)
        self.assertIn("remainingCollisions", totals)
        self.assertIn("repairPasses", totals)
        self.assertLessEqual(totals["repairPasses"], 3)
        self.assertEqual(totals["remainingCollisions"], 0)
        for item in page["candidates"]:
            leader_length = math.hypot(item["labelX"] - item["x"], item["labelY"] - item["y"])
            self.assertLessEqual(leader_length, item["markerStyle"]["frameSize"] * 18 + 1e-6)

    def test_long_straight_pipe_uses_ordered_near_perpendicular_group_lanes(self) -> None:
        layout = importlib.import_module("label_layout")
        page = {
            "page": 1, "width": 620, "height": 420,
            "candidates": [
                {"id": f"pipe-{index}", "number": str(index + 1), "x": x, "y": 210, "included": True,
                 "markerStyle": {"shape": "circle", "frameSize": 20, "fontSize": 12}}
                for index, x in enumerate((150, 220, 290, 360, 430))
            ],
            "layoutObstacles": {"textRects": [], "processSegments": [{"start": [80, 210], "end": [520, 210], "width": 2}]},
        }

        layout.reflow_label_positions([page])

        offsets = [item["labelY"] - item["y"] for item in page["candidates"]]
        self.assertTrue(all(value > 0 for value in offsets) or all(value < 0 for value in offsets))
        self.assertTrue(all(abs(item["labelX"] - item["x"]) <= 32 for item in page["candidates"]))
        self.assertTrue(all(item["layoutDiagnostics"]["source"].startswith("long-pipe-group") for item in page["candidates"]))
        lanes = [item["layoutDiagnostics"]["longPipeLane"] for item in page["candidates"]]
        self.assertEqual(lanes, sorted(lanes))

    def test_full_port_emits_reference_diagnostics_and_is_deterministic(self) -> None:
        layout = importlib.import_module("label_layout")
        template = {
            "page": 1, "width": 500, "height": 400,
            "candidates": [{"id": "diagnostic", "number": "1", "x": 220, "y": 200, "included": True,
                            "markerStyle": {"shape": "circle", "frameSize": 20, "fontSize": 12}}],
            "layoutObstacles": {
                "textRects": [[195, 130, 245, 175]],
                "processSegments": [{"start": [80, 200], "end": [420, 200], "width": 2}],
            },
        }
        first, second = copy.deepcopy(template), copy.deepcopy(template)

        layout.reflow_label_positions([first])
        layout.reflow_label_positions([second])

        self.assertEqual(first["candidates"], second["candidates"])
        diagnostics = first["candidates"][0]["layoutDiagnostics"]
        self.assertIn(diagnostics["selectionStage"], {"long-pipe-group", "local-pipe-band-compact", "local-pipe-band", "local-pipe-band-extended", "perimeter-distribution", "strict-perpendicular", "relaxed-direction", "non-crossing-emergency", "fallback-best-effort"})
        self.assertEqual(diagnostics["hardCollisionCount"], 0)
        self.assertGreater(diagnostics["lineLength"], 0)
        self.assertIn("hardCollisionSummary", diagnostics)

    def test_strict_perpendicular_stage_uses_shortest_reference_length(self) -> None:
        layout = importlib.import_module("label_layout")
        page = {
            "page": 1, "width": 500, "height": 400,
            "candidates": [{"id": "normal", "number": "1", "x": 250, "y": 200, "included": True,
                            "markerStyle": {"shape": "circle", "frameSize": 20, "fontSize": 12}}],
            "layoutObstacles": {"textRects": [], "processSegments": [{"start": [80, 200], "end": [420, 200], "width": 2}]},
        }

        layout.reflow_label_positions([page])

        item = page["candidates"][0]
        self.assertEqual(item["layoutDiagnostics"]["selectionStage"], "strict-perpendicular")
        self.assertAlmostEqual(item["layoutDiagnostics"]["lineLength"], 20 * 140 / 56.7, delta=0.01)
        self.assertAlmostEqual(item["labelX"], item["x"], delta=0.01)

    def test_perimeter_mode_uses_local_pipe_band_before_spokes(self) -> None:
        layout = importlib.import_module("label_layout")
        anchors = [(100, 100), (300, 100), (500, 100), (100, 300), (300, 300), (500, 300), (200, 200), (400, 200)]
        page = {
            "page": 1, "width": 620, "height": 420,
            "candidates": [{"id": f"local-{index}", "number": str(index + 1), "x": x, "y": y, "included": True,
                            "markerStyle": {"shape": "circle", "frameSize": 20, "fontSize": 12}} for index, (x, y) in enumerate(anchors)],
            "layoutObstacles": {"textRects": [], "processSegments": [
                {"start": [x - 25, y], "end": [x + 25, y], "width": 2} if index % 2 == 0 else {"start": [x, y - 25], "end": [x, y + 25], "width": 2}
                for index, (x, y) in enumerate(anchors)
            ]},
        }

        layout.reflow_label_positions([page])

        self.assertTrue(all(item["layoutDiagnostics"]["selectionStage"].startswith("local-pipe-band") for item in page["candidates"]))

    def test_foreign_source_anchor_proximity_is_soft_like_reference_engine(self) -> None:
        layout = importlib.import_module("label_layout")
        page = {"page": 1, "width": 400, "height": 300, "candidates": [
            {"id": "near-a", "number": "1", "x": 200, "y": 150, "included": True, "markerStyle": {"shape": "circle", "frameSize": 20, "fontSize": 12}},
            {"id": "near-b", "number": "2", "x": 200, "y": 150, "included": True, "markerStyle": {"shape": "circle", "frameSize": 20, "fontSize": 12}},
        ], "layoutObstacles": {"textRects": [], "processSegments": []}}

        totals = layout.reflow_label_positions([page])

        self.assertEqual(totals["remainingCollisions"], 0)


if __name__ == "__main__":
    unittest.main()
