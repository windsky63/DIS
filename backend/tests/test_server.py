from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import fitz


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import server


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body.read()


class ServerTests(unittest.TestCase):
    def test_training_sample_is_generated_automatically_without_human_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            job_id = "d" * 32
            target = folder / "target.pdf"
            document = fitz.open()
            document.new_page(width=200, height=160)
            document.save(target)
            document.close()
            (folder / "job.json").write_text(json.dumps({"targetFile": target.name}), encoding="utf-8")
            (folder / "result.json").write_text(json.dumps({
                "pages": [{"page": 1, "candidates": [{"id": "V1", "page": 1, "included": True}]}],
            }), encoding="utf-8")

            output, count = server._write_training_sample(job_id, folder)

            self.assertEqual(count, 1)
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["exportMode"], "post-analysis-auto")
                self.assertFalse(manifest["candidates"][0]["confirmedByHuman"])
                self.assertIn("pages/page-1.png", archive.namelist())

    def test_health_poll_access_log_is_suppressed(self) -> None:
        handler = object.__new__(server.ApiHandler)
        with patch.object(server, "_console") as console:
            handler.log_message('"%s" %s %s', "GET /api/health HTTP/1.1", "200", "-")
        console.assert_not_called()

    def test_mineru_health_requires_ready_worker(self) -> None:
        with patch.object(server, "urlopen", return_value=_Response({"ready": True, "worker_alive": True})):
            result = server._mineru_health()
        self.assertEqual(result["status"], "ready")

        with patch.object(server, "urlopen", return_value=_Response({"ready": True, "worker_alive": False})):
            result = server._mineru_health()
        self.assertEqual(result["status"], "offline")

    def test_mineru_health_failure_is_reported_without_raising(self) -> None:
        with patch.object(server, "urlopen", side_effect=TimeoutError("timed out")):
            result = server._mineru_health()
        self.assertEqual(result["status"], "offline")
        self.assertIn("无法连接 MinerU", result["message"])

    def test_pcf_library_folders_and_safe_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            project = root / "项目甲" / "管线01"
            project.mkdir(parents=True)
            (project / "line.pcf").write_text("PIPELINE-REFERENCE LINE-01", encoding="utf-8")
            with patch.object(server, "PCF_LIBRARY_ROOT", root):
                folders = server._pcf_library_folders()
                self.assertEqual(folders, [{"value": "项目甲/管线01", "title": "项目甲 / 管线01", "count": 1}])
                self.assertEqual(server._resolve_pcf_library_folder("项目甲/管线01"), [project / "line.pcf"])
                with self.assertRaisesRegex(ValueError, "路径无效"):
                    server._resolve_pcf_library_folder("../outside")

    def test_recent_batch_restores_all_completed_jobs_in_latest_batch(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            for index, name in enumerate(("A.pdf", "B.pdf")):
                job = root / f"job-{index}"
                job.mkdir()
                (job / "job.json").write_text(json.dumps({
                    "batchId": "batch-latest", "batchIndex": index, "originalTargetName": name,
                }), encoding="utf-8")
                (job / "result.json").write_text(json.dumps({
                    "status": "complete", "jobId": f"id-{index}", "pages": [{"page": 1, "candidates": []}],
                }), encoding="utf-8")
            with patch.object(server, "DATA_ROOT", root):
                restored = server._recent_batch_results()
            self.assertEqual(restored["batchId"], "batch-latest")
            self.assertEqual([item["originalTargetName"] for item in restored["jobs"]], ["A.pdf", "B.pdf"])

    def test_analysis_signature_tracks_page_range_and_configuration(self) -> None:
        payload = {
            "targetPdf": {"name": "drawing.pdf", "dataBase64": "YWJj"},
            "startPage": 1,
            "endPage": 2,
            "symbolConfig": {"detectionMode": "placement"},
        }
        first = server._analysis_signature(payload)
        different_range = server._analysis_signature({**payload, "startPage": 99, "endPage": 120})
        different_mode = server._analysis_signature({**payload, "symbolConfig": {"detectionMode": "comparison"}})
        self.assertNotEqual(first, different_range)
        self.assertNotEqual(first, different_mode)
        with patch.object(server, "ANALYSIS_ALGORITHM_VERSION", "next-version"):
            self.assertNotEqual(first, server._analysis_signature(payload))

    def test_reconcile_marks_interrupted_jobs_failed(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job = root / ("e" * 32)
            job.mkdir()
            (job / "result.json").write_text(json.dumps({
                "jobId": job.name, "status": "processing", "pages": [],
            }), encoding="utf-8")
            with patch.object(server, "DATA_ROOT", root):
                reconciled = server._reconcile_interrupted_jobs()
            saved = json.loads((job / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(reconciled, 1)
            self.assertEqual(saved["status"], "failed")
            self.assertIn("服务重启", saved["progressMessage"])

    def test_page_validation_rejects_out_of_range_and_mismatched_candidates(self) -> None:
        current = {"analyzedRange": [2, 3]}
        with self.assertRaisesRegex(server.ApiError, "不在任务范围"):
            server._validated_pages([{"page": 1, "candidates": []}], current)
        with self.assertRaisesRegex(server.ApiError, "页码与所属页面不一致"):
            server._validated_pages([{"page": 2, "candidates": [{"page": 3, "id": "x"}]}], current)

    def test_cancel_job_sets_event_and_cancelling_status(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "a" * 32
            folder = root / job_id
            folder.mkdir()
            (folder / "result.json").write_text(json.dumps({"jobId": job_id, "status": "processing", "pages": []}), encoding="utf-8")
            event = server.threading.Event()
            handler = object.__new__(server.ApiHandler)
            responses = []
            handler._json = lambda status, payload: responses.append((status, payload))
            with patch.object(server, "DATA_ROOT", root), patch.dict(server.JOB_CANCEL_EVENTS, {job_id: event}, clear=True):
                handler._cancel_job(job_id)
            saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertTrue(event.is_set())
            self.assertEqual(saved["status"], "cancelling")
            self.assertTrue(responses[0][1]["cancelled"])

    def test_analysis_progress_combines_reference_files_and_design_pages(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            (folder / "result.json").write_text(json.dumps({
                "jobId": "job-progress", "status": "processing", "totalPages": 2, "pages": [],
            }), encoding="utf-8")

            def fake_analysis(*_args, progress_callback, page_callback, **_kwargs):
                progress_callback("对照 PDF 1/2 解析完成")
                progress_callback("对照 PDF 2/2 解析完成")
                progress_callback("研究设计图页面 1/2")
                page_callback({"page": 1, "candidates": []})
                page_callback({"page": 2, "candidates": []})
                return {"pages": [{"page": 1, "candidates": []}, {"page": 2, "candidates": []}]}

            def fake_training(_job_id, job_folder, **_kwargs):
                output = job_folder / "training-sample.zip"
                output.write_bytes(b"training")
                return output, 0

            with patch.object(server, "analyze_documents", side_effect=fake_analysis), \
                    patch.object(server, "_write_training_sample", side_effect=fake_training), \
                    patch.object(server, "_update_job_result", wraps=server._update_job_result) as updates, \
                    patch.dict(server.JOB_CANCEL_EVENTS, {}, clear=True):
                server._run_analysis_job(
                    "job-progress", folder, folder / "target.pdf",
                    [folder / "reference-1.pdf", folder / "reference-2.pdf"], [],
                    None, 1, 2, "signature",
                )

            progress_updates = [call.args[1] for call in updates.call_args_list]
            self.assertIn(1, [item.get("progressCompletedUnits") for item in progress_updates])
            self.assertIn(2, [item.get("progressCompletedUnits") for item in progress_updates])
            saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["progressCompletedUnits"], 4)
            self.assertEqual(saved["progressTotalUnits"], 4)
            self.assertEqual(saved["completedReferenceFiles"], 2)
            self.assertEqual(saved["completedPages"], 2)

    def test_export_embeds_independent_marker_styles(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "b" * 32
            folder = root / job_id
            folder.mkdir()
            (folder / "job.json").write_text(json.dumps({"targetFile": "target.pdf"}), encoding="utf-8")
            (folder / "result.json").write_text(json.dumps({
                "status": "complete", "analyzedRange": [1, 1],
                "pages": [{"page": 1, "candidates": []}],
            }), encoding="utf-8")
            (folder / "target.pdf").write_bytes(b"pdf")
            candidates = [
                {"page": 1, "number": "F1", "markerStyle": {"color": "#c43d32", "frameSize": 30}},
                {"page": 1, "number": "V1", "componentType": "valve", "markerStyle": {"color": "#1769d2", "frameSize": 30}},
            ]
            handler = object.__new__(server.ApiHandler)
            handler._read_json = lambda: {"candidates": candidates}
            responses = []
            handler._json = lambda status, payload: responses.append((status, payload))
            captured = {}

            def fake_write(_source, output, _candidates, editable_data):
                captured.update(editable_data)
                output.write_bytes(b"annotated")
                return output

            with patch.object(server, "DATA_ROOT", root), patch.object(server, "write_annotated_pdf", side_effect=fake_write):
                handler._export_job(job_id)

            self.assertEqual(captured["markerStyles"]["weld"]["frameSize"], 30)
            self.assertEqual(captured["markerStyles"]["components"]["valve"]["color"], "#1769d2")
            self.assertTrue(responses)

    def test_save_job_persists_numbers_and_marker_appearances_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "c" * 32
            folder = root / job_id
            folder.mkdir()
            (folder / "result.json").write_text(json.dumps({
                "status": "complete", "pages": [{"page": 1, "candidates": [{"id": "w1"}]}],
            }), encoding="utf-8")
            handler = object.__new__(server.ApiHandler)
            handler.path = f"/api/jobs/{job_id}"
            handler._read_json = lambda: {
                "pages": [{"page": 1, "candidates": [{"id": "w1", "number": "F1"}]}],
                "markerStyle": {"color": "#d4143c", "frameSize": 30},
                "componentMarkerStyles": {"support": {"color": "#1769d2", "frameSize": 30}},
                "markerStyles": {
                    "weld": {"color": "#d4143c", "frameSize": 30},
                    "components": {"support": {"color": "#1769d2", "frameSize": 30}},
                },
                "baseRevision": 0,
            }
            responses = []
            handler._json = lambda status, payload: responses.append((status, payload))
            with patch.object(server, "DATA_ROOT", root):
                handler.do_PUT()

            saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["pages"][0]["candidates"][0]["number"], "F1")
            self.assertEqual(saved["markerStyle"]["frameSize"], 30)
            self.assertEqual(saved["componentMarkerStyles"]["support"]["color"], "#1769d2")
            self.assertEqual(saved["revision"], 1)
            self.assertTrue(responses[0][1]["saved"])

    def test_save_job_rejects_stale_revision(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "f" * 32
            folder = root / job_id
            folder.mkdir()
            (folder / "result.json").write_text(json.dumps({
                "status": "complete", "revision": 2, "analyzedRange": [1, 1],
                "pages": [{"page": 1, "candidates": []}],
            }), encoding="utf-8")
            handler = object.__new__(server.ApiHandler)
            handler.path = f"/api/jobs/{job_id}"
            handler._read_json = lambda: {"baseRevision": 1, "pages": [{"page": 1, "candidates": []}]}
            responses = []
            handler._json = lambda status, payload: responses.append((status, payload))
            with patch.object(server, "DATA_ROOT", root):
                handler.do_PUT()
            self.assertEqual(responses[0][0], 409)
            self.assertIn("其他窗口", responses[0][1]["error"])


if __name__ == "__main__":
    unittest.main()
