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
    def test_client_disconnect_does_not_write_an_error_response(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler._json = lambda *_args, **_kwargs: self.fail("断开的客户端不应再次写入响应")
        with patch.object(server.traceback, "print_exc") as print_exc:
            handler._handle_exception(ConnectionAbortedError(10053, "客户端已关闭"))
        print_exc.assert_not_called()

    def test_disconnect_while_writing_error_response_is_suppressed(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler._json = lambda *_args, **_kwargs: (_ for _ in ()).throw(BrokenPipeError())
        handler._handle_exception(server.ApiError("请求失败"))

    def test_module_worker_is_served_with_javascript_mime_type(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            worker = Path(folder_name) / "pdf.worker.mjs"
            worker.write_text("export default true", encoding="utf-8")
            handler = object.__new__(server.ApiHandler)
            headers = {}
            handler.send_response = lambda _status: None
            handler.send_header = lambda name, value: headers.__setitem__(name, value)
            handler.end_headers = lambda: None
            handler.wfile = io.BytesIO()

            handler._file(worker)

            self.assertEqual(headers["Content-Type"], "application/javascript; charset=utf-8")
            self.assertEqual(handler.wfile.getvalue(), b"export default true")

    def test_health_exposes_deployed_version(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/health"
        responses = []
        handler._json = lambda status, payload: responses.append((status, payload))
        with patch.object(server, "_mineru_health", return_value={"status": "offline"}), patch.object(server, "parser_availability", return_value={}):
            handler._do_GET()
        self.assertEqual(responses[0][1]["version"], server.APP_VERSION)
        self.assertEqual(responses[0][1]["buildId"], server.BUILD_ID)

    def test_frontend_entry_is_not_cached_but_hashed_assets_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            frontend = Path(folder_name)
            assets = frontend / "assets"
            assets.mkdir()
            (frontend / "index.html").write_text("index", encoding="utf-8")
            (assets / "app-hash.js").write_text("asset", encoding="utf-8")
            handler = object.__new__(server.ApiHandler)
            served = []
            handler._file = lambda path, download_name=None, cache_control=None: served.append((path, cache_control))
            with patch.object(server, "FRONTEND_DIST", frontend):
                handler._serve_frontend("/")
                handler._serve_frontend("/assets/app-hash.js")
        self.assertEqual(served[0][1], "no-store, no-cache, must-revalidate")
        self.assertEqual(served[1][1], "public, max-age=31536000, immutable")

    def test_bundled_tutorial_result_is_full_and_numbered(self) -> None:
        result = json.loads((server.TUTORIAL_ROOT / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["analyzedRange"], [7, 26])
        self.assertEqual([page["page"] for page in result["pages"]], list(range(7, 27)))
        candidates = [candidate for page in result["pages"] for candidate in page["candidates"]]
        self.assertGreater(len(candidates), 0)
        self.assertTrue(all(str(candidate.get("number") or "").strip() for candidate in candidates))
        self.assertEqual(len(result["ep3dReferenceInventory"]["documents"]), 20)
        self.assertTrue(all(page.get("reference", {}).get("eligibleReferencePages") for page in result["pages"]))
        self.assertGreater(sum(bool(candidate.get("referenceMatched")) for candidate in candidates), 0)

    def test_tutorial_session_is_loaded_without_creating_a_job(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            tutorial_root = root / "tutorial"
            jobs_root = root / "jobs"
            tutorial_root.mkdir()
            (tutorial_root / "000207.pdf").write_bytes(b"tutorial-pdf")
            references = tutorial_root / "000207"
            references.mkdir()
            reference = fitz.open()
            reference.new_page(width=100, height=200)
            reference.save(references / "第07页.pdf")
            reference.close()
            (tutorial_root / "result.json").write_text(json.dumps({
                "schema": "weld-marker.topology.v1",
                "status": "complete",
                "pages": [{
                    "page": 7,
                    "candidates": [],
                    "reference": {"eligibleReferencePages": [{"file": "第07页.pdf", "page": 1}]},
                }],
                "ep3dReferenceInventory": {
                    "documents": [{"file": "第07页.pdf", "pages": []}],
                },
            }), encoding="utf-8")

            with patch.object(server, "TUTORIAL_ROOT", tutorial_root), patch.object(server, "DATA_ROOT", jobs_root):
                result = server._create_tutorial_session()

            self.assertEqual(result["jobId"], "tutorial-000207")
            self.assertEqual(result["referenceFiles"], ["第07页.pdf"])
            self.assertEqual(
                result["pages"][0]["reference"]["eligibleReferencePages"][0]["file"],
                "第07页.pdf",
            )
            self.assertEqual(len(result["ep3dReferenceInventory"]["documents"]), 1)
            self.assertFalse(jobs_root.exists())

    def test_job_reference_manifest_exposes_recoverable_files_without_server_paths(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "9" * 32
            folder = root / job_id
            folder.mkdir()
            stored_name = "reference-001__对照图.pdf"
            (folder / stored_name).write_bytes(b"pdf")
            (folder / "job.json").write_text(json.dumps({
                "referenceFiles": [stored_name, "reference-002__missing.pdf"],
            }), encoding="utf-8")

            with patch.object(server, "DATA_ROOT", root):
                manifest = server._job_reference_manifest(job_id)

            self.assertEqual(manifest, [{
                "index": 0,
                "name": "对照图.pdf",
                "url": f"/api/jobs/{job_id}/references/0",
                "size": 3,
            }])
            self.assertNotIn(folder_name, json.dumps(manifest, ensure_ascii=False))

            handler = object.__new__(server.ApiHandler)
            handler.path = f"/api/jobs/{job_id}/references/0"
            served = []
            handler._file = lambda path, name=None: served.append((path, name))
            with patch.object(server, "DATA_ROOT", root):
                handler._do_GET()
            self.assertEqual(served, [(folder / stored_name, "对照图.pdf")])

    def test_atomic_dump_retries_transient_windows_access_denial(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            path = Path(folder_name) / "result.json"
            path.write_text(json.dumps({"status": "old"}), encoding="utf-8")
            real_replace = server.os.replace
            attempts = 0

            def intermittently_locked(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError(5, "拒绝访问", str(destination))
                return real_replace(source, destination)

            with patch.object(server.os, "replace", side_effect=intermittently_locked), \
                    patch.object(server.time, "sleep") as sleep:
                server._atomic_dump(path, {"status": "complete", "value": 42})

            self.assertEqual(attempts, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"], 42)
            self.assertEqual(list(path.parent.glob(".result.json.*.tmp")), [])

    def test_atomic_dump_uses_a_unique_staging_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            path = Path(folder_name) / "result.json"
            staged_paths = []
            real_dump = server.dump_result

            def capture_staging_file(staging_path, payload):
                staged_paths.append(staging_path)
                real_dump(staging_path, payload)

            with patch.object(server, "dump_result", side_effect=capture_staging_file):
                server._atomic_dump(path, {"status": "complete"})

            self.assertEqual(len(staged_paths), 1)
            self.assertNotEqual(staged_paths[0], path.with_name("result.json.tmp"))
            self.assertEqual(staged_paths[0].parent, path.parent)

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
                    "analysisAlgorithmVersion": server.ANALYSIS_ALGORITHM_VERSION,
                }), encoding="utf-8")
                (job / "result.json").write_text(json.dumps({
                    "status": "complete", "jobId": f"id-{index}", "pages": [{"page": 1, "candidates": []}],
                }), encoding="utf-8")
            tutorial_job = root / "legacy-tutorial"
            tutorial_job.mkdir()
            (tutorial_job / "job.json").write_text(json.dumps({
                "tutorial": True, "originalTargetName": "000207.pdf",
            }), encoding="utf-8")
            (tutorial_job / "result.json").write_text(json.dumps({
                "status": "complete", "jobId": "legacy-tutorial", "pages": [{"page": 7, "candidates": []}],
            }), encoding="utf-8")
            with patch.object(server, "DATA_ROOT", root):
                restored = server._recent_batch_results()
            self.assertEqual(restored["batchId"], "batch-latest")
            self.assertEqual([item["originalTargetName"] for item in restored["jobs"]], ["A.pdf", "B.pdf"])

    def test_workspace_result_defers_non_visible_page_obstacles(self) -> None:
        result = {
            "status": "complete",
            "pages": [
                {"page": 2, "candidates": [], "layoutObstacles": {"textRects": [[1, 2, 3, 4]]}},
                {"page": 3, "candidates": [], "layoutObstacles": {"textRects": [[5, 6, 7, 8]]}},
            ],
        }
        workspace = server._workspace_result(result, 3)
        self.assertNotIn("layoutObstacles", workspace["pages"][0])
        self.assertFalse(workspace["pages"][0]["detailsLoaded"])
        self.assertIn("layoutObstacles", workspace["pages"][1])
        self.assertTrue(workspace["pages"][1]["detailsLoaded"])
        self.assertIn("layoutObstacles", result["pages"][0])

    def test_lazy_workspace_save_preserves_deferred_page_fields(self) -> None:
        current = {
            "analyzedRange": [1, 1],
            "pages": [{
                "page": 1,
                "candidates": [{"id": "p1", "page": 1, "number": "F1", "x": 1, "y": 2}],
                "layoutObstacles": {"textRects": [[1, 2, 3, 4]]},
            }],
        }
        saved = server._merge_saved_pages([{
            "page": 1,
            "candidates": [{"id": "p1", "page": 1, "number": "F2", "x": 1, "y": 2}],
            "detailsLoaded": False,
        }], current)
        self.assertEqual(saved[0]["candidates"][0]["number"], "F2")
        self.assertEqual(saved[0]["layoutObstacles"]["textRects"], [[1, 2, 3, 4]])

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

    def test_analysis_queue_lists_status_and_reorders_only_waiting_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            queued_first, queued_second, running = "1" * 32, "2" * 32, "3" * 32
            for job_id, status in ((queued_first, "processing"), (queued_second, "processing"), (running, "processing")):
                folder = root / job_id
                folder.mkdir()
                (folder / "job.json").write_text(json.dumps({"originalTargetName": f"{job_id[0]}.pdf"}), encoding="utf-8")
                (folder / "result.json").write_text(json.dumps({
                    "jobId": job_id, "status": status, "pages": [], "completedPages": 0, "totalPages": 4,
                }), encoding="utf-8")

            with patch.object(server, "DATA_ROOT", root), \
                    patch.object(server, "PENDING_ANALYSIS_JOBS", [queued_first, queued_second]), \
                    patch.object(server, "RUNNING_ANALYSIS_JOBS", {running}):
                snapshot = server._analysis_queue_snapshot()
                moved = server._move_queued_job(queued_second, "up")
                reordered = server._analysis_queue_snapshot()
                with self.assertRaisesRegex(server.ApiError, "正在解析"):
                    server._move_queued_job(running, "up")

            self.assertEqual(snapshot["runningCount"], 1)
            self.assertEqual(snapshot["queuedCount"], 2)
            self.assertEqual(snapshot["jobs"][0]["queueState"], "running")
            self.assertEqual(moved["queuePosition"], 1)
            queued = [item["jobId"] for item in reordered["jobs"] if item["queueState"] == "queued"]
            self.assertEqual(queued, [queued_second, queued_first])

    def test_completed_job_can_be_archived_restored_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "7" * 32
            folder = root / job_id
            folder.mkdir()
            (folder / "job.json").write_text(json.dumps({
                "originalTargetName": "done.pdf",
                "analysisAlgorithmVersion": server.ANALYSIS_ALGORITHM_VERSION,
            }), encoding="utf-8")
            (folder / "result.json").write_text(json.dumps({
                "jobId": job_id, "status": "complete", "pages": [{"page": 1, "candidates": []}],
            }), encoding="utf-8")

            with patch.object(server, "DATA_ROOT", root):
                server._set_job_archived(job_id, True)
                self.assertTrue(server._analysis_queue_snapshot()["jobs"][0]["isArchived"])
                self.assertEqual(server._recent_batch_results()["jobs"], [])
                server._set_job_archived(job_id, False)
                self.assertFalse(server._analysis_queue_snapshot()["jobs"][0]["isArchived"])
                server._delete_completed_job(job_id)

            self.assertFalse(folder.exists())

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
            self.assertEqual(captured["sourceFileName"], "target.pdf")
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
