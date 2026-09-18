from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from http.cookies import SimpleCookie

import fitz


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import server
import job_runner
from admin_store import AdminStore
from assistant_agent import AssistantAgentEvent
from job_store import JobStore


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
    def test_analysis_queue_route_forwards_validated_pagination(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/analysis-queue?scope=archived&page=3&pageSize=40"
        responses = []
        handler._json = lambda status, payload, headers=None: responses.append((status, payload))
        expected = {"jobs": [], "pagination": {"scope": "archived", "page": 3, "pageSize": 40}}

        with patch.object(server, "_analysis_queue_snapshot", return_value=expected) as load_snapshot:
            handler._do_GET()

        load_snapshot.assert_called_once_with(scope="archived", page=3, page_size=40)
        self.assertEqual(responses, [(200, expected)])

    def test_analysis_queue_route_normalizes_invalid_pagination(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/analysis-queue?scope=unknown&page=-2&pageSize=500"
        handler._json = lambda *_args, **_kwargs: None

        with patch.object(server, "_analysis_queue_snapshot", return_value={"jobs": []}) as load_snapshot:
            handler._do_GET()

        load_snapshot.assert_called_once_with(scope="current", page=1, page_size=100)

    def test_auth_store_bootstraps_the_default_admin_account(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            with patch.object(server, "DATA_ROOT", Path(folder_name) / "jobs"):
                server.AUTH_STORES.clear()
                user = server._auth_store().authenticate("admin", "123456")
            self.assertEqual(user["username"], "admin")
            self.assertIs(user["isAdmin"], True)

    def test_registration_creates_session_and_me_resolves_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name) / "jobs"
            responses = []
            handler = object.__new__(server.ApiHandler)
            handler.path = "/api/auth/register"
            handler.headers = {}
            handler._read_json = lambda *_args: {"username": "Reviewer01", "password": "correct-horse-battery"}
            handler._json = lambda status, payload, headers=None: responses.append((status, payload, headers or {}))
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.AUTH_STORES.clear()
                handler.do_POST()
                self.assertEqual(responses[0][0], 201)
                self.assertIn("HttpOnly", responses[0][2]["Set-Cookie"])
                self.assertIn("SameSite=Lax", responses[0][2]["Set-Cookie"])
                self.assertNotIn("Max-Age", responses[0][2]["Set-Cookie"])
                cookie = SimpleCookie()
                cookie.load(responses[0][2]["Set-Cookie"])
                token = cookie[server.SESSION_COOKIE_NAME].value

                handler.path = "/api/auth/me"
                handler.headers = {"Cookie": f"{server.SESSION_COOKIE_NAME}={token}"}
                handler._do_GET()

            self.assertEqual(responses[1][0], 200)
            self.assertEqual(responses[1][1]["user"]["username"], "Reviewer01")
            self.assertIs(responses[1][1]["user"]["isAdmin"], False)

    def test_new_login_invalidates_the_previous_session_for_the_same_account(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            with patch.object(server, "DATA_ROOT", Path(folder_name) / "jobs"):
                server.AUTH_STORES.clear()
                store = server._auth_store()
                user = store.authenticate("admin", "123456")
                first_token = store.create_session(str(user["userId"]))
                second_token = store.create_session(str(user["userId"]))

                self.assertIsNone(store.resolve_session(first_token))
                self.assertEqual(store.resolve_session(second_token)["userId"], user["userId"])

    def test_new_login_releases_the_accounts_existing_page_locks(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name) / "jobs"
            handler = object.__new__(server.ApiHandler)
            handler.path = "/api/auth/login"
            handler.headers = {}
            handler._read_json = lambda *_args: {"username": "admin", "password": "123456"}
            handler._json = lambda *_args, **_kwargs: None
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.AUTH_STORES.clear()
                server.PAGE_LOCK_STORES.clear()
                admin = server._auth_store().authenticate("admin", "123456")
                locks = server._page_lock_store()
                locks.acquire("job", 1, str(admin["userId"]), "admin", "old-tab")

                handler.do_POST()

                self.assertIsNone(locks.get("job", 1))

    def test_admin_routes_reject_regular_users_and_return_sanitized_database_data(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name) / "jobs"
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.AUTH_STORES.clear()
                server.JOB_STORES.clear()
                server.PAGE_LOCK_STORES.clear()
                server.ASSISTANT_CONVERSATION_STORES.clear()
                admin = server._auth_store().authenticate("admin", "123456")
                regular = server._auth_store().register("reviewer", "correct-horse-battery")

                denied = object.__new__(server.ApiHandler)
                denied.path = "/api/admin/overview"
                denied.headers = {}
                denied._request_user = regular
                denied_responses = []
                denied._json = lambda status, payload, headers=None: denied_responses.append((status, payload))
                denied.do_GET()
                self.assertEqual(denied_responses, [(403, {"error": "需要管理员权限"})])

                users = AdminStore(jobs_root / ".queue" / "jobs.db").users()
                self.assertEqual(users["pagination"]["total"], 2)
                self.assertTrue(any(item["isAdmin"] for item in users["users"]))
                serialized = json.dumps(users, ensure_ascii=False)
                self.assertNotIn("password_hash", serialized)
                self.assertNotIn("password_salt", serialized)

                allowed = object.__new__(server.ApiHandler)
                allowed.path = "/api/admin/overview"
                allowed.headers = {}
                allowed._request_user = admin
                allowed_responses = []
                allowed._json = lambda status, payload, headers=None: allowed_responses.append((status, payload))
                allowed.do_GET()
                self.assertEqual(allowed_responses[0][0], 200)
                self.assertEqual(allowed_responses[0][1]["metrics"]["totalUsers"], 2)

    def test_login_remember_password_controls_persistent_cookie_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name) / "jobs"
            responses = []
            handler = object.__new__(server.ApiHandler)
            handler.path = "/api/auth/login"
            handler.headers = {}
            handler._json = lambda status, payload, headers=None: responses.append((status, payload, headers or {}))
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.AUTH_STORES.clear()
                handler._read_json = lambda *_args: {
                    "username": "admin", "password": "123456", "rememberPassword": False,
                }
                handler.do_POST()
                handler._read_json = lambda *_args: {
                    "username": "admin", "password": "123456", "rememberPassword": True,
                }
                handler.do_POST()

        session_cookie = responses[0][2]["Set-Cookie"]
        persistent_cookie = responses[1][2]["Set-Cookie"]
        self.assertNotIn("Max-Age", session_cookie)
        self.assertNotIn("Expires=", session_cookie)
        self.assertIn(f"Max-Age={7 * 24 * 60 * 60}", persistent_cookie)
        self.assertNotIn("123456", session_cookie + persistent_cookie)

    def test_business_api_requires_authentication(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/jobs"
        handler.headers = {}
        responses = []
        handler._json = lambda status, payload, headers=None: responses.append((status, payload))
        handler.do_POST()
        self.assertEqual(responses, [(401, {"error": "请先登录"})])

    def test_ai_status_reports_configuration_without_credentials(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/ai/status"
        handler.headers = {}
        handler._request_user = {"userId": "u1", "username": "张三"}
        responses = []
        handler._json = lambda status, payload, headers=None: responses.append((status, payload))
        with patch.object(server.ai_assistant, "configuration_status", return_value={"configured": True, "model": "guide-model"}):
            handler.do_GET()
        self.assertEqual(responses, [(200, {"configured": True, "model": "guide-model"})])

    def test_ai_chat_stream_forwards_typed_agent_events(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/ai/chat/stream"
        handler.headers = {}
        handler._request_user = {"userId": "u1", "username": "张三"}
        handler._read_json = lambda *_args: {
            "messages": [{"role": "user", "content": "如何开始？"}],
            "context": {"currentPage": 1, "jobLoaded": False},
        }
        statuses = []
        headers = {}
        handler.send_response = lambda status: statuses.append(status)
        handler.send_header = lambda name, value: headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()

        events = iter([
            AssistantAgentEvent("status", {"message": "正在检索系统文档"}),
            AssistantAgentEvent("source", {
                "documentId": "usage-guide", "title": "使用指南", "path": "docs/usage.md",
                "section": "保存并关闭", "startLine": 30,
            }),
            AssistantAgentEvent("delta", {"content": "第一段回答"}),
        ])
        with patch.object(server.ai_assistant, "stream_chat_completion", return_value=events):
            handler.do_POST()

        body = handler.wfile.getvalue().decode("utf-8")
        self.assertEqual(statuses, [200])
        self.assertEqual(headers["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertIn('event: status\ndata: {"message": "正在检索系统文档"}\n\n', body)
        self.assertIn('event: source\ndata: {"documentId": "usage-guide"', body)
        self.assertIn('event: delta\ndata: {"content": "第一段回答"}\n\n', body)
        self.assertTrue(body.endswith("event: done\ndata: {}\n\n"))

    def test_ai_chat_stream_converts_late_agent_failure_to_sse_error(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/ai/chat/stream"
        handler.headers = {}
        handler._request_user = {"userId": "u1", "username": "张三"}
        handler._read_json = lambda *_args: {"messages": [{"role": "user", "content": "如何保存？"}]}
        handler.send_response = lambda _status: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()

        def events():
            yield AssistantAgentEvent("status", {"message": "正在分析问题"})
            raise server.ai_assistant.AssistantRequestError("上游连接中断")

        with patch.object(server.ai_assistant, "stream_chat_completion", return_value=events()):
            handler.do_POST()

        body = handler.wfile.getvalue().decode("utf-8")
        self.assertIn('event: status\ndata: {"message": "正在分析问题"}\n\n', body)
        self.assertTrue(body.endswith('event: error\ndata: {"error": "上游连接中断"}\n\n'))

    def test_ai_conversation_history_round_trips_for_only_the_current_user(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name) / "jobs"
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.AUTH_STORES.clear()
                alice = server._auth_store().register("alice", "secret1")
                bob = server._auth_store().register("bobby", "secret2")
                responses = []
                handler = object.__new__(server.ApiHandler)
                handler.headers = {}
                handler._request_user = alice
                handler._json = lambda status, payload, headers=None: responses.append((status, payload))
                handler.path = "/api/ai/conversations"
                handler._read_json = lambda *_args: {"conversations": [{
                    "id": "conversation-1", "title": "识别流程", "updatedAt": 1234,
                    "messages": [{"role": "assistant", "content": "先上传图纸。"}],
                }]}
                handler.do_PUT()
                handler.do_GET()
                handler._request_user = bob
                handler.do_GET()

            self.assertEqual(responses[0][0], 200)
            self.assertEqual(responses[1][1]["conversations"][0]["title"], "识别流程")
            self.assertEqual(responses[2], (200, {"conversations": []}))

    def test_cross_origin_write_is_rejected(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/jobs"
        handler.headers = {"Origin": "https://attacker.example", "Host": "drawings.example"}
        responses = []
        handler._json = lambda status, payload, headers=None: responses.append((status, payload))
        handler.do_POST()
        self.assertEqual(responses[0][0], 403)
        self.assertIn("跨站", responses[0][1]["error"])

    def test_page_lock_api_returns_owner_on_contention(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name)
            job_id = "1" * 32
            job = jobs_root / job_id
            job.mkdir()
            (job / "result.json").write_text(json.dumps({
                "status": "complete", "pages": [{"page": 1, "candidates": []}],
            }), encoding="utf-8")
            responses = []
            handler = object.__new__(server.ApiHandler)
            handler.path = f"/api/jobs/{job_id}/pages/1/lock"
            handler.headers = {}
            handler._read_json = lambda *_args: {"clientInstanceId": "tab-a"}
            handler._request_user = {"userId": "u1", "username": "张三"}
            handler._json = lambda status, payload, headers=None: responses.append((status, payload))
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.JOB_STORES.clear()
                server.PAGE_LOCK_STORES.clear()
                server._job_store().replace_job_pages(job_id, [{"page": 1, "candidates": []}])
                handler.do_POST()
                handler._request_user = {"userId": "u2", "username": "李四"}
                handler._read_json = lambda *_args: {"clientInstanceId": "tab-b"}
                handler.do_POST()

            self.assertEqual(responses[0][0], 200)
            self.assertTrue(responses[0][1]["lock"]["lockToken"])
            self.assertEqual(responses[1][0], 423)
            self.assertEqual(responses[1][1]["lock"]["owner"]["username"], "张三")

    def test_locked_task_cannot_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name)
            job_id = "4" * 32
            job = jobs_root / job_id
            job.mkdir()
            (job / "job.json").write_text(json.dumps({
                "analysisSignature": "locked-delete", "originalTargetName": "drawing.pdf",
            }), encoding="utf-8")
            (job / "result.json").write_text(json.dumps({
                "status": "complete", "pages": [{"page": 1, "candidates": []}],
            }), encoding="utf-8")
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.JOB_STORES.clear(); server.PAGE_LOCK_STORES.clear()
                store = server._job_store()
                store.create_job(
                    job_id=job_id, analysis_signature="locked-delete", job_folder=job,
                    original_target_name="drawing.pdf", algorithm_version="test",
                )
                claimed = store.claim_next("worker-test", 60)
                store.finish(job_id, "complete", worker_id=str(claimed["lease_owner"]))
                server._page_lock_store().acquire(job_id, 1, "u1", "张三", "tab-a")
                with self.assertRaises(server.ApiError) as raised:
                    server._delete_completed_job(job_id)
            self.assertEqual(raised.exception.status, 423)
            self.assertTrue(job.exists())

    def test_two_reviewers_save_different_pages_with_independent_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name)
            job_id = "2" * 32
            job = jobs_root / job_id
            job.mkdir()
            result_path = job / "result.json"
            result_path.write_text(json.dumps({
                "status": "complete",
                "analyzedRange": [1, 2],
                "pages": [
                    {"page": 1, "candidates": [{"id": "a", "page": 1, "number": ""}]},
                    {"page": 2, "candidates": [{"id": "b", "page": 2, "number": ""}]},
                ],
            }), encoding="utf-8")
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.JOB_STORES.clear(); server.PAGE_LOCK_STORES.clear()
                server._job_store().replace_job_pages(job_id, json.loads(result_path.read_text(encoding="utf-8"))["pages"])
                lock_store = server._page_lock_store()
                alice_lock = lock_store.acquire(job_id, 1, "u1", "张三", "tab-a")
                bob_lock = lock_store.acquire(job_id, 2, "u2", "李四", "tab-b")
                cases = [
                    (1, {"userId": "u1", "username": "张三"}, "tab-a", alice_lock["lockToken"], "A1"),
                    (2, {"userId": "u2", "username": "李四"}, "tab-b", bob_lock["lockToken"], "B1"),
                ]
                responses = []
                for page, user, client_id, token, number in cases:
                    handler = object.__new__(server.ApiHandler)
                    handler.path = f"/api/jobs/{job_id}/pages/{page}"
                    handler.headers = {}
                    handler._request_user = user
                    handler._read_json = lambda *_args, p=page, c=client_id, t=token, n=number: {
                        "page": {"page": p, "candidates": [{"id": "a" if p == 1 else "b", "page": p, "number": n}]},
                        "basePageRevision": 0, "clientInstanceId": c, "lockToken": t,
                    }
                    handler._json = lambda status, payload, headers=None: responses.append((status, payload))
                    handler.do_PUT()
                page_store = server._job_store()
                review_history = page_store.page_review_history(job_id)
                saved_pages = page_store.get_job_pages(job_id)

            self.assertEqual([response[0] for response in responses], [200, 200])
            self.assertEqual([page["reviewRevision"] for page in saved_pages], [1, 1])
            self.assertEqual([page["candidates"][0]["number"] for page in saved_pages], ["A1", "B1"])
            self.assertEqual([(item["page_number"], item["username"]) for item in review_history], [(1, "张三"), (2, "李四")])

    def test_stale_page_revision_is_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            jobs_root = Path(folder_name)
            job_id = "3" * 32
            job = jobs_root / job_id
            job.mkdir()
            result_path = job / "result.json"
            result_path.write_text(json.dumps({
                "status": "complete", "pages": [{
                    "page": 1, "reviewRevision": 1,
                    "candidates": [{"id": "a", "page": 1, "number": "A1"}],
                }],
            }), encoding="utf-8")
            with patch.object(server, "DATA_ROOT", jobs_root):
                server.JOB_STORES.clear(); server.PAGE_LOCK_STORES.clear()
                server._job_store().replace_job_pages(
                    job_id, json.loads(result_path.read_text(encoding="utf-8"))["pages"],
                )
                lock = server._page_lock_store().acquire(job_id, 1, "u1", "张三", "tab-a")
                handler = object.__new__(server.ApiHandler)
                handler.path = f"/api/jobs/{job_id}/pages/1"
                handler.headers = {}
                handler._request_user = {"userId": "u1", "username": "张三"}
                handler._read_json = lambda *_args: {
                    "page": {"page": 1, "candidates": [{"id": "a", "page": 1, "number": "STALE"}]},
                    "basePageRevision": 0, "clientInstanceId": "tab-a", "lockToken": lock["lockToken"],
                }
                responses = []
                handler._json = lambda status, payload, headers=None: responses.append((status, payload))
                handler.do_PUT()
                saved = server._job_store().get_job_page(job_id, 1)
            self.assertEqual(responses[0][0], 409)
            self.assertEqual(saved["candidates"][0]["number"], "A1")

    def test_binary_upload_streams_to_disk_and_is_consumed_by_job(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            uploads = root / "uploads"
            job = root / "job"
            job.mkdir()
            content = b"%PDF-streamed-upload"
            with patch.object(server, "UPLOAD_ROOT", uploads), patch.object(server, "MIN_FREE_DISK_BYTES", 0):
                record = server._store_upload(io.BytesIO(content), len(content), "%E5%9B%BE%E7%BA%B8.pdf")
                stored = uploads / record["uploadId"] / "payload.bin"
                self.assertEqual(stored.read_bytes(), content)
                self.assertEqual(record["name"], "图纸.pdf")
                destination = server._consume_upload(record, job, "target.pdf")

            self.assertEqual(destination, job / "target.pdf")
            self.assertEqual(destination.read_bytes(), content)
            self.assertFalse(stored.parent.exists())

    def test_binary_upload_rejects_oversized_file_before_reading(self) -> None:
        with patch.object(server, "MAX_UPLOAD_BYTES", 3):
            with self.assertRaises(server.ApiError) as raised:
                server._store_upload(io.BytesIO(b"four"), 4, "large.pdf")
        self.assertEqual(raised.exception.status, 413)

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

    def test_tutorial_pages_are_read_only_and_cannot_enter_job_save_route(self) -> None:
        responses = []
        handler = object.__new__(server.ApiHandler)
        handler.path = "/api/tutorial/pages/7"
        handler.headers = {}
        handler._json = lambda status, payload, headers=None: responses.append((status, payload))

        handler.do_PUT()

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][0], 404)
        self.assertIn("error", responses[0][1])

    def test_tutorial_workspace_defers_non_visible_page_obstacles(self) -> None:
        tutorial = server._create_tutorial_session()
        first_page = int(tutorial["pages"][0]["page"])
        workspace = server._workspace_result(tutorial, first_page)

        self.assertEqual(workspace["workspaceView"], "lazy-page-details")
        self.assertTrue(workspace["pages"][0]["detailsLoaded"])
        self.assertIn("layoutObstacles", workspace["pages"][0])
        self.assertTrue(all(
            not page["detailsLoaded"] and "layoutObstacles" not in page
            for page in workspace["pages"][1:]
        ))

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
            root = Path(folder_name)
            job_id = "d" * 32
            folder = root / job_id
            folder.mkdir()
            target = folder / "target.pdf"
            document = fitz.open()
            document.new_page(width=200, height=160)
            document.save(target)
            document.close()
            (folder / "job.json").write_text(json.dumps({"targetFile": target.name}), encoding="utf-8")
            (folder / "result.json").write_text(json.dumps({
                "status": "complete",
            }), encoding="utf-8")

            with patch.object(server, "DATA_ROOT", root):
                server.JOB_STORES.clear()
                server._job_store().replace_job_pages(job_id, [{
                    "page": 1, "candidates": [{"id": "V1", "page": 1, "included": True}],
                }])
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
            job_ids = [(str(index + 1) * 32) for index in range(3)]
            batches = (("batch-older", 0, "Old.pdf"), ("batch-latest", 0, "A.pdf"), ("batch-latest", 1, "B.pdf"))
            for index, (batch_id, batch_index, name) in enumerate(batches):
                job = root / job_ids[index]
                job.mkdir()
                (job / "job.json").write_text(json.dumps({
                    "batchId": batch_id, "batchIndex": batch_index, "originalTargetName": name,
                    "analysisAlgorithmVersion": server.ANALYSIS_ALGORITHM_VERSION,
                }), encoding="utf-8")
                (job / "result.json").write_text(json.dumps({
                    "status": "complete", "jobId": job_ids[index],
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
                server.JOB_STORES.clear()
                store = server._job_store()
                for index, (job_id, (batch_id, batch_index, name)) in enumerate(zip(job_ids, batches)):
                    store.create_job(
                        job_id=job_id, analysis_signature=f"batch-{index}", job_folder=root / job_id,
                        original_target_name=name, algorithm_version=server.ANALYSIS_ALGORITHM_VERSION,
                        batch_id=batch_id, batch_index=batch_index,
                    )
                    claimed = store.claim_next(f"worker-{index}", 60)
                    store.replace_job_pages(job_id, [{"page": 1, "candidates": []}])
                    self.assertTrue(store.finish(job_id, "complete", worker_id=str(claimed["lease_owner"])))
                    with store._connection() as connection:
                        connection.execute(
                            "UPDATE jobs SET completed_at = ? WHERE job_id = ?",
                            (f"2026-09-18T10:00:0{index}", job_id),
                        )
                loaded_job_ids = []
                original_loader = server._load_job_result

                def tracked_loader(job_id, *args, **kwargs):
                    loaded_job_ids.append(job_id)
                    return original_loader(job_id, *args, **kwargs)

                with patch.object(server, "_load_job_result", side_effect=tracked_loader):
                    restored = server._recent_batch_results()
            self.assertEqual(restored["batchId"], "batch-latest")
            self.assertEqual([item["originalTargetName"] for item in restored["jobs"]], ["A.pdf", "B.pdf"])
            self.assertEqual(loaded_job_ids, job_ids[1:])

    def test_workspace_result_defers_non_visible_page_obstacles(self) -> None:
        result = {
            "status": "complete",
            "labelLayout": {"optimized": True},
            "pages": [
                {"page": 2, "candidates": [], "layoutObstacles": {"textRects": [[1, 2, 3, 4]]}},
                {"page": 3, "candidates": [], "layoutObstacles": {"textRects": [[5, 6, 7, 8]]}},
            ],
        }
        workspace = server._workspace_result(result, 3)
        self.assertNotIn("layoutObstacles", workspace["pages"][0])
        self.assertEqual(workspace["pages"][0]["candidates"], [])
        self.assertFalse(workspace["pages"][0]["detailsLoaded"])
        self.assertIn("layoutObstacles", workspace["pages"][1])
        self.assertTrue(workspace["pages"][1]["detailsLoaded"])
        self.assertIn("layoutObstacles", result["pages"][0])
        self.assertNotIn("labelLayout", workspace)

    def test_analysis_signature_tracks_page_range_and_configuration(self) -> None:
        payload = {
            "targetUpload": {"uploadId": "a" * 32, "name": "drawing.pdf"},
            "startPage": 1,
            "endPage": 2,
            "symbolConfig": {"detectionMode": "placement"},
        }
        upload = (Path("payload.bin"), {"name": "drawing.pdf", "sha256": "abc"})
        with patch.object(server, "_upload_record", return_value=upload):
            first = server._analysis_signature(payload)
            different_range = server._analysis_signature({**payload, "startPage": 99, "endPage": 120})
            different_mode = server._analysis_signature({**payload, "symbolConfig": {"detectionMode": "comparison"}})
            different_project = server._analysis_signature({**payload, "project": {"id": "another-project", "name": "其他项目"}})
            self.assertNotEqual(first, different_range)
            self.assertNotEqual(first, different_mode)
            self.assertNotEqual(first, different_project)
            with patch.object(server, "ANALYSIS_ALGORITHM_VERSION", "next-version"):
                self.assertNotEqual(first, server._analysis_signature(payload))

    def test_job_creation_rejects_disabled_comparison_mode(self) -> None:
        handler = object.__new__(server.ApiHandler)
        handler._read_json = lambda *_: {"symbolConfig": {"detectionMode": "comparison"}}
        with self.assertRaisesRegex(server.ApiError, "对照模式暂未开放"):
            handler._create_job()

    def test_create_job_records_authenticated_creator(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            source = fitz.open()
            source.new_page(width=100, height=100)
            pdf_bytes = source.tobytes()
            source.close()
            upload_root = root / "uploads"
            upload_id = "f" * 32
            upload_folder = upload_root / upload_id
            upload_folder.mkdir(parents=True)
            (upload_folder / "payload.bin").write_bytes(pdf_bytes)
            (upload_folder / "upload.json").write_text(json.dumps({
                "name": "drawing.pdf", "size": len(pdf_bytes), "sha256": "test-pdf",
                "createdAt": "2026-09-18T00:00:00",
            }), encoding="utf-8")
            handler = object.__new__(server.ApiHandler)
            handler._request_user = {"userId": "user-1", "username": "Reviewer01"}
            handler._read_json = lambda *_args: {
                "targetUpload": {"uploadId": upload_id, "name": "drawing.pdf"},
                "project": {"id": "chengda-indonesia", "name": "成达印尼项目"},
                "symbolConfig": {"detectionMode": "placement"},
            }
            responses = []
            handler._json = lambda status, payload, headers=None: responses.append((status, payload))
            with patch.object(server, "DATA_ROOT", root), patch.object(server, "UPLOAD_ROOT", upload_root):
                server.JOB_STORES.clear()
                handler._create_job()
            created = responses[0][1]
            folder = root / created["jobId"]
            meta = json.loads((folder / "job.json").read_text(encoding="utf-8"))
            result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            row = JobStore(root / ".queue" / "jobs.db").get(created["jobId"])
            expected = {"userId": "user-1", "username": "Reviewer01"}
            self.assertEqual(created["createdBy"], expected)
            self.assertEqual(meta["createdBy"], expected)
            self.assertEqual(result["createdBy"], expected)
            self.assertEqual(meta["project"]["id"], "chengda-indonesia")
            self.assertEqual(result["project"]["name"], "成达印尼项目")
            self.assertEqual(row["createdBy"], expected)

    def test_reconcile_does_not_import_json_only_legacy_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job = root / ("e" * 32)
            job.mkdir()
            (job / "result.json").write_text(json.dumps({
                "jobId": job.name, "status": "processing", "pages": [],
            }), encoding="utf-8")
            (job / "job.json").write_text(json.dumps({
                "analysisSignature": "signature", "originalTargetName": "drawing.pdf",
            }), encoding="utf-8")
            with patch.object(server, "DATA_ROOT", root):
                server.JOB_STORES.clear()
                reconciled = server._reconcile_interrupted_jobs()
            saved = json.loads((job / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(reconciled, 0)
            self.assertEqual(saved["status"], "processing")
            self.assertIsNone(JobStore(root / ".queue" / "jobs.db").get(job.name))

    def test_page_validation_rejects_out_of_range_and_mismatched_candidates(self) -> None:
        current = {"analyzedRange": [2, 3]}
        with self.assertRaisesRegex(server.ApiError, "不在任务范围"):
            server._validated_pages([{"page": 1, "candidates": []}], current)
        with self.assertRaisesRegex(server.ApiError, "页码与所属页面不一致"):
            server._validated_pages([{"page": 2, "candidates": [{"page": 3, "id": "x"}]}], current)

    def test_cancel_job_persists_cancellation_request(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "a" * 32
            folder = root / job_id
            folder.mkdir()
            (folder / "result.json").write_text(json.dumps({"jobId": job_id, "status": "processing", "pages": []}), encoding="utf-8")
            (folder / "job.json").write_text(json.dumps({
                "analysisSignature": "cancel-signature", "originalTargetName": "drawing.pdf",
            }), encoding="utf-8")
            handler = object.__new__(server.ApiHandler)
            responses = []
            handler._json = lambda status, payload: responses.append((status, payload))
            with patch.object(server, "DATA_ROOT", root):
                store = server._job_store()
                store.create_job(
                    job_id=job_id, analysis_signature="cancel-signature", job_folder=folder,
                    original_target_name="drawing.pdf", algorithm_version="test",
                )
                claimed = store.claim_next("worker-test", 60)
                self.assertIsNotNone(claimed)
                handler._cancel_job(job_id)
            saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertTrue(store.cancellation_requested(job_id))
            self.assertEqual(saved["status"], "cancelling")
            self.assertTrue(responses[0][1]["cancelled"])

    def test_analysis_queue_lists_status_and_reorders_only_waiting_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            queued_first, queued_second, queued_last, running = "1" * 32, "2" * 32, "4" * 32, "3" * 32
            for job_id, status in ((queued_first, "processing"), (queued_second, "processing"), (queued_last, "processing"), (running, "processing")):
                folder = root / job_id
                folder.mkdir()
                (folder / "job.json").write_text(json.dumps({"originalTargetName": f"{job_id[0]}.pdf"}), encoding="utf-8")
                (folder / "result.json").write_text(json.dumps({
                    "jobId": job_id, "status": status, "pages": [], "completedPages": 0, "totalPages": 4,
                    "completedReferenceFiles": 1, "totalReferenceFiles": 2,
                    "progressCompletedUnits": 1.5, "progressTotalUnits": 6,
                    "progressPercent": 35,
                }), encoding="utf-8")

            with patch.object(server, "DATA_ROOT", root):
                store = server._job_store()
                for queued_id in (queued_first, queued_second, queued_last, running):
                    store.create_job(
                        job_id=queued_id, analysis_signature=f"queue-{queued_id}",
                        job_folder=root / queued_id, original_target_name=f"{queued_id[0]}.pdf",
                        algorithm_version="test",
                        queue_summary={
                            "totalPages": 4,
                            "completedReferenceFiles": 1,
                            "totalReferenceFiles": 2,
                            "referenceFileCount": 2,
                            "progressCompletedUnits": 1.5,
                            "progressTotalUnits": 6,
                            "progressPercent": 35,
                        },
                    )
                claimed = store.claim_next("worker-test", 60)
                self.assertEqual(claimed["job_id"], queued_first)
                store.move_queued(queued_first, "down") if False else None
                # Import order is directory-dependent; explicitly rank the three waiting jobs.
                waiting = [job_id for job_id in (queued_first, queued_second, queued_last, running) if job_id != queued_first]
                for job_id in reversed(waiting):
                    store.move_queued(job_id, "front")
                snapshot = server._analysis_queue_snapshot()
                moved = server._move_queued_job(queued_second, "up")
                promoted = server._move_queued_job(queued_last, "front")
                running_priority = server._move_queued_job(queued_first, "front")
                reordered = server._analysis_queue_snapshot()
                with self.assertRaisesRegex(server.ApiError, "正在解析"):
                    server._move_queued_job(queued_first, "up")

            self.assertEqual(snapshot["runningCount"], 1)
            self.assertEqual(snapshot["queuedCount"], 3)
            self.assertEqual(snapshot["jobs"][0]["queueState"], "running")
            self.assertEqual(snapshot["jobs"][0]["progressCompletedUnits"], 1.5)
            self.assertEqual(snapshot["jobs"][0]["progressTotalUnits"], 6)
            self.assertEqual(snapshot["jobs"][0]["progressPercent"], 35)
            self.assertEqual(moved["queuePosition"], 1)
            self.assertEqual(promoted["previousQueuePosition"], 2)
            self.assertEqual(promoted["queuePosition"], 1)
            self.assertEqual(running_priority["queueState"], "running")
            queued = [item["jobId"] for item in reordered["jobs"] if item["queueState"] == "queued"]
            self.assertEqual(queued[0], queued_last)

    def test_reused_completed_analysis_exposes_canonical_progress(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            folder = root / ("8" * 32)
            folder.mkdir()
            (folder / "job.json").write_text(json.dumps({"analysisSignature": "same"}), encoding="utf-8")
            (folder / "result.json").write_text(json.dumps({
                "jobId": folder.name, "status": "complete",
            }), encoding="utf-8")
            with patch.object(server, "DATA_ROOT", root):
                server.JOB_STORES.clear()
                server._job_store().replace_job_pages(folder.name, [{"page": 1, "candidates": []}])
                existing = server._find_existing_analysis("same")
        self.assertEqual(existing["result"]["progressPercent"], 100)

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
                "jobId": job_id, "status": "complete",
            }), encoding="utf-8")

            with patch.object(server, "DATA_ROOT", root):
                server.JOB_STORES.clear()
                store = server._job_store()
                store.create_job(
                    job_id=job_id, analysis_signature="archive", job_folder=folder,
                    original_target_name="done.pdf", algorithm_version=server.ANALYSIS_ALGORITHM_VERSION,
                )
                claimed = store.claim_next("worker-test", 60)
                store.replace_job_pages(job_id, [{"page": 1, "candidates": []}])
                self.assertTrue(store.finish(job_id, "complete", worker_id=str(claimed["lease_owner"])))
                server._set_job_archived(job_id, True)
                self.assertTrue(server._analysis_queue_snapshot(scope="archived")["jobs"][0]["isArchived"])
                self.assertEqual(server._recent_batch_results()["jobs"], [])
                server._set_job_archived(job_id, False)
                self.assertFalse(server._analysis_queue_snapshot(scope="current")["jobs"][0]["isArchived"])
                server._delete_completed_job(job_id)

            self.assertFalse(folder.exists())

    def test_analysis_progress_combines_reference_files_and_design_pages(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            folder = root / ("d" * 32)
            folder.mkdir()
            (folder / "result.json").write_text(json.dumps({
                "jobId": folder.name, "status": "processing", "totalPages": 2, "pages": [],
            }), encoding="utf-8")
            (folder / "job.json").write_text(json.dumps({
                "targetFile": "target.pdf",
                "referenceFiles": ["reference-1.pdf", "reference-2.pdf"],
                "pcfSources": [], "startPage": 1, "endPage": 2,
            }), encoding="utf-8")

            def fake_analysis(*_args, progress_callback, page_callback, **_kwargs):
                progress_callback("解析对照 PDF 1/2：页面 2/4")
                progress_callback("对照 PDF 1/2 解析完成")
                progress_callback("对照 PDF 2/2 解析完成")
                progress_callback("研究设计图页面 1/2")
                page_callback({"page": 1, "candidates": []})
                page_callback({"page": 2, "candidates": []})
                return {"pages": [{"page": 1, "candidates": []}, {"page": 2, "candidates": []}]}

            training_stage_progress = []

            def fake_training(_job_id, job_folder, **_kwargs):
                training_stage_progress.append(json.loads(
                    (job_folder / "result.json").read_text(encoding="utf-8")
                )["progressPercent"])
                output = job_folder / "training-sample.zip"
                output.write_bytes(b"training")
                return output, 0

            store = JobStore(root / "jobs.db")
            store.initialize()
            store.create_job(
                job_id=folder.name, analysis_signature="signature", job_folder=folder,
                original_target_name="target.pdf", algorithm_version="test",
            )
            row = store.claim_next("worker-test", 60)
            with patch.object(job_runner, "analyze_documents", side_effect=fake_analysis), \
                    patch.object(job_runner, "write_training_sample", side_effect=fake_training), \
                    patch.object(job_runner, "update_result", wraps=job_runner.update_result) as updates:
                job_runner.run_job(
                    store, row, "worker-test",
                )

            progress_updates = [call.args[1] for call in updates.call_args_list]
            self.assertIn(0.5, [item.get("progressCompletedUnits") for item in progress_updates])
            self.assertIn(1, [item.get("progressCompletedUnits") for item in progress_updates])
            self.assertIn(2, [item.get("progressCompletedUnits") for item in progress_updates])
            self.assertIn(25, [item.get("progressPercent") for item in progress_updates])
            layout_updates = [item for item in progress_updates if item.get("progressStage") == "optimizing-layout"]
            self.assertEqual([item.get("layoutCompletedPages") for item in layout_updates], [0, 1, 2])
            self.assertEqual([item.get("progressPercent") for item in layout_updates], [95, 97.0, 99.0])
            self.assertEqual(training_stage_progress, [99])
            saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["progressCompletedUnits"], 4)
            self.assertEqual(saved["progressTotalUnits"], 4)
            self.assertEqual(saved["completedReferenceFiles"], 2)
            self.assertEqual(saved["completedPages"], 2)
            self.assertEqual(saved["progressPercent"], 100)
            queue_summary = store.get(folder.name)["queueSummary"]
            self.assertEqual(queue_summary["completedPages"], 2)
            self.assertEqual(queue_summary["progressPercent"], 100)
            self.assertEqual(queue_summary["progressStage"], "complete")
            self.assertNotIn("pages", queue_summary)

    def test_worker_optimizes_all_pages_before_completing_job(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            folder = root / ("9" * 32)
            folder.mkdir()
            (folder / "result.json").write_text(json.dumps({
                "jobId": folder.name, "status": "processing", "totalPages": 2, "pages": [],
            }), encoding="utf-8")
            (folder / "job.json").write_text(json.dumps({
                "targetFile": "target.pdf", "referenceFiles": [], "pcfSources": [],
                "startPage": 1, "endPage": 2,
            }), encoding="utf-8")
            parsed_pages = [{
                "page": page, "width": 300, "height": 200,
                "candidates": [{
                    "id": f"w{page}", "number": str(page), "x": 100, "y": 100,
                    "labelX": 250, "labelY": 180, "included": True,
                }],
                "layoutObstacles": {"textRects": [], "processSegments": []},
            } for page in (1, 2)]

            def fake_analysis(*_args, page_callback, **_kwargs):
                for page in parsed_pages:
                    page_callback(page)
                return {"pages": parsed_pages}

            def fake_training(_job_id, job_folder, **_kwargs):
                output = job_folder / "training-sample.zip"
                output.write_bytes(b"training")
                return output, 2

            store = JobStore(root / "jobs.db")
            store.initialize()
            store.create_job(
                job_id=folder.name, analysis_signature="signature", job_folder=folder,
                original_target_name="target.pdf", algorithm_version="test",
            )
            row = store.claim_next("worker-test", 60)
            with patch.object(job_runner, "analyze_documents", side_effect=fake_analysis), \
                    patch.object(job_runner, "write_training_sample", side_effect=fake_training):
                job_runner.run_job(store, row, "worker-test")

            saved = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            saved_pages = store.get_job_pages(folder.name)
            self.assertNotIn("pages", saved)
            self.assertTrue(all(page["candidates"][0]["labelX"] != 250 for page in saved_pages))
            self.assertNotIn("labelLayout", saved)

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
                server.JOB_STORES.clear()
                server._job_store().replace_job_pages(job_id, [{"page": 1, "candidates": []}])
                handler._export_job(job_id)

            self.assertEqual(captured["markerStyles"]["weld"]["frameSize"], 30)
            self.assertEqual(captured["markerStyles"]["components"]["valve"]["color"], "#1769d2")
            self.assertEqual(captured["sourceFileName"], "target.pdf")
            self.assertTrue(responses)

    def test_export_clears_stale_embedded_candidates_from_pages_omitted_by_filtered_payload(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            root = Path(folder_name)
            job_id = "d" * 32
            folder = root / job_id
            folder.mkdir()
            (folder / "job.json").write_text(json.dumps({"targetFile": "target.pdf"}), encoding="utf-8")
            (folder / "result.json").write_text(json.dumps({
                "status": "complete", "analyzedRange": [1, 2],
            }), encoding="utf-8")
            (folder / "target.pdf").write_bytes(b"pdf")
            handler = object.__new__(server.ApiHandler)
            handler._read_json = lambda: {"candidates": [
                {"id": "kept", "page": 1, "number": "F1", "included": True},
            ]}
            handler._json = lambda *_args: None
            captured = {}

            def fake_write(_source, output, _candidates, editable_data):
                captured.update(editable_data)
                output.write_bytes(b"annotated")
                return output

            with patch.object(server, "DATA_ROOT", root), patch.object(server, "write_annotated_pdf", side_effect=fake_write):
                server.JOB_STORES.clear()
                server._job_store().replace_job_pages(job_id, [
                    {"page": 1, "candidates": [{"id": "old-1", "page": 1}], "candidateCount": 1},
                    {"page": 2, "candidates": [{"id": "old-2", "page": 2}], "candidateCount": 1},
                ])
                handler._export_job(job_id)

            pages = {page["page"]: page for page in captured["pages"]}
            self.assertEqual([item["id"] for item in pages[1]["candidates"]], ["kept"])
            self.assertEqual(pages[1]["candidateCount"], 1)
            self.assertEqual(pages[2]["candidates"], [])
            self.assertEqual(pages[2]["candidateCount"], 0)

    def test_tutorial_export_clears_stale_embedded_candidates_when_all_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            export_root = Path(folder_name)
            handler = object.__new__(server.ApiHandler)
            handler._read_json = lambda: {"candidates": []}
            handler._json = lambda *_args: None
            captured = {}
            tutorial_result = {
                "status": "complete", "analyzedRange": [1, 1],
                "pages": [{"page": 1, "candidates": [{"id": "old", "page": 1}], "candidateCount": 1}],
            }

            def fake_write(_source, output, _candidates, editable_data):
                captured.update(editable_data)
                output.write_bytes(b"annotated")
                return output

            with patch.object(server, "TUTORIAL_EXPORT_ROOT", export_root), \
                    patch.object(server, "_create_tutorial_session", return_value=tutorial_result), \
                    patch.object(server, "write_annotated_pdf", side_effect=fake_write):
                handler._export_tutorial()

            self.assertEqual(captured["pages"][0]["candidates"], [])
            self.assertEqual(captured["pages"][0]["candidateCount"], 0)

if __name__ == "__main__":
    unittest.main()
