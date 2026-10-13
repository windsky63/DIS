import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from backend import job_runner
from backend.job_runner import assign_page_numbers
from backend.job_store import JobStore


class JobRunnerNumberingTests(unittest.TestCase):
    def test_initial_numbers_use_reference_labels_and_generated_fallbacks(self) -> None:
        page = {
            "page": 1,
            "candidates": [
                {"id": "matched", "x": 1, "y": 1, "referenceLabel": "F18"},
                {"id": "generated", "x": 2, "y": 2, "referenceLabel": ""},
                {
                    "id": "matched-valve", "x": 3, "y": 3,
                    "componentKind": "design-component", "componentType": "valve",
                    "referenceLabel": "V1",
                },
                {
                    "id": "generated-valve", "x": 4, "y": 4,
                    "componentKind": "design-component", "componentType": "valve",
                    "referenceLabel": "",
                },
            ],
        }

        assign_page_numbers(page, {
            "useReferenceNumber": True,
            "startNumber": 7,
            "prefix": "W",
            "suffix": "A",
        })

        self.assertEqual(
            [candidate["number"] for candidate in page["candidates"]],
            ["F18", "W8A", "V1", "V2"],
        )

    def test_numbering_without_references_uses_the_task_rule(self) -> None:
        page = {"candidates": [
            {"id": "first", "x": 5, "y": 2, "referenceLabel": "F99"},
            {"id": "second", "x": 1, "y": 1},
        ]}

        assign_page_numbers(page, {
            "useReferenceNumber": False,
            "startNumber": 3,
            "prefix": "J-",
            "suffix": "X",
        })

        self.assertEqual(page["candidates"][0]["number"], "J-4X")
        self.assertEqual(page["candidates"][1]["number"], "J-3X")

    def test_direct_placement_optimizes_pdf_and_skips_recognition(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "a" * 32
            folder = root / job_id
            folder.mkdir()
            document = fitz.open()
            document.new_page(width=300, height=200)
            document.new_page(width=400, height=250)
            document.save(folder / "target.pdf")
            document.close()
            meta = {
                "taskType": "direct-placement",
                "targetFile": "target.pdf",
                "originalTargetName": "design.pdf",
                "referenceFiles": [],
                "pcfSources": [],
                "startPage": 1,
                "endPage": 2,
            }
            (folder / "job.json").write_text(json.dumps(meta), encoding="utf-8")
            (folder / "result.json").write_text(json.dumps({
                "jobId": job_id,
                "taskType": "direct-placement",
                "status": "processing",
                "totalPages": 2,
                "pages": [],
            }), encoding="utf-8")
            store = JobStore(root / "jobs.db")
            store.initialize()
            store.create_job(
                job_id=job_id,
                analysis_signature="direct",
                job_folder=folder,
                original_target_name="design.pdf",
                algorithm_version="test",
            )
            row = store.claim_next("worker-test", 60)
            with patch.object(job_runner, "_optimize_job_pdfs", return_value=(meta, [{"status": "complete"}])) as optimize, \
                    patch.object(job_runner, "analyze_documents", side_effect=AssertionError("recognition must be skipped")):
                status = job_runner.run_job(store, row, "worker-test")

            self.assertEqual(status, "complete")
            optimize.assert_called_once()
            pages = store.get_job_pages(job_id)
            self.assertEqual([(page["page"], page["width"], page["height"]) for page in pages], [
                (1, 300.0, 200.0),
                (2, 400.0, 250.0),
            ])
            self.assertTrue(all(page["candidates"] == [] for page in pages))
            saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["taskType"], "direct-placement")
            self.assertEqual(saved["status"], "complete")
            self.assertNotIn("pages", saved)


if __name__ == "__main__":
    unittest.main()
