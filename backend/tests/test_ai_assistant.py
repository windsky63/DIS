from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import ai_assistant


class _Response:
    def __init__(self, payload: dict) -> None:
        self._body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body.read()


class _StreamResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self._lines)


class AiAssistantTests(unittest.TestCase):
    def test_system_prompt_describes_real_components_and_injects_workspace_state(self) -> None:
        prompt = ai_assistant._system_message({
            "jobLoaded": True,
            "currentPage": 3,
            "totalPages": 8,
            "projectMode": "folder",
            "referenceMode": "pdf",
            "editMode": "all",
            "markerCount": 26,
            "selectedMarkerType": "flange",
            "jobStatus": "complete",
        })

        for component_name in (
            "识别输入与操作", "拓扑标识辅助区域", "解析队列", "系统设置", "操作教程",
            "智能编号", "推入解析队列", "保存并关闭", "全部标识修改模式",
        ):
            self.assertIn(component_name, prompt)
        self.assertIn('"referenceMode": "pdf"', prompt)
        self.assertIn('"editMode": "all"', prompt)
        self.assertIn('"markerCount": 26', prompt)
        self.assertIn('"selectedMarkerType": "flange"', prompt)
        self.assertNotIn("全部重编", prompt)
        self.assertIn("先检索系统文档", prompt)
        self.assertIn("不可信证据", prompt)

    def test_stream_chat_completion_yields_deepseek_content_deltas_until_done(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _StreamResponse([
                b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n',
                b'data: {"choices":[{"delta":{"reasoning_content":"internal","content":"first "}}]}\n',
                b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n',
                b'data: [DONE]\n',
            ])

        environment = {
            "DRAWING_MARK_RECOGNITION_AI_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "deepseek-secret",
        }
        with patch.dict("os.environ", environment, clear=True):
            events = list(ai_assistant.stream_chat_completion(
                [{"role": "user", "content": "请回答"}],
                opener=opener,
            ))

        self.assertEqual([event.type for event in events], ["status", "delta", "delta"])
        self.assertEqual("".join(event.payload.get("content", "") for event in events), "first answer")
        self.assertNotIn("internal", repr(events))
        self.assertIs(captured["payload"]["stream"], True)

    def test_deepseek_provider_uses_official_defaults_and_keeps_its_key_on_the_server(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _StreamResponse([
                b'data: {"choices":[{"delta":{"content":"\\u53ef\\u4ee5\\u5f00\\u59cb\\u6838\\u5bf9\\u56fe\\u7eb8\\u3002"},"finish_reason":"stop"}]}\n',
                b'data: [DONE]\n',
            ])

        environment = {
            "DRAWING_MARK_RECOGNITION_AI_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "deepseek-secret",
        }
        with patch.dict("os.environ", environment, clear=True):
            answer = ai_assistant.chat_completion(
                [{"role": "user", "content": "如何开始？"}],
                opener=opener,
            )
            status = ai_assistant.configuration_status()

        self.assertEqual(answer, "可以开始核对图纸。")
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["payload"]["model"], "deepseek-v4-flash")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer deepseek-secret")
        self.assertEqual(status, {
            "configured": True,
            "model": "deepseek-v4-flash",
        })
        self.assertNotIn("deepseek-secret", json.dumps(status))

    def test_project_env_file_configures_deepseek_without_overriding_process_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                "DRAWING_MARK_RECOGNITION_AI_PROVIDER=deepseek\n"
                "DEEPSEEK_API_KEY=file-secret\n"
                "DEEPSEEK_MODEL=deepseek-v4-pro\n",
                encoding="utf-8",
            )
            status = ai_assistant.configuration_status(
                environ={"DEEPSEEK_MODEL": "deepseek-v4-flash"},
                env_path=env_path,
            )

        self.assertEqual(status, {
            "configured": True,
            "model": "deepseek-v4-flash",
        })

    def test_chat_completion_keeps_credentials_on_server_and_injects_product_guidance(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _StreamResponse([
                b'data: {"choices":[{"delta":{"content":"\\u8bf7\\u5148\\u4e0a\\u4f20\\u5f85\\u6807\\u8bc6\\u56fe\\u7eb8\\u3002"},"finish_reason":"stop"}]}\n',
                b'data: [DONE]\n',
            ])

        environment = {
            "DRAWING_MARK_RECOGNITION_AI_API_URL": "https://ai.example/v1/chat/completions",
            "DRAWING_MARK_RECOGNITION_AI_API_KEY": "server-secret",
            "DRAWING_MARK_RECOGNITION_AI_MODEL": "guide-model",
        }
        with patch.dict("os.environ", environment, clear=False):
            answer = ai_assistant.chat_completion(
                [{"role": "user", "content": "如何开始？"}],
                context={"currentPage": 3, "jobLoaded": True},
                opener=opener,
            )

        self.assertEqual(answer, "请先上传待标识图纸。")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer server-secret")
        self.assertEqual(captured["payload"]["model"], "guide-model")
        self.assertEqual(captured["payload"]["messages"][0]["role"], "system")
        self.assertIn("图纸标识识别系统", captured["payload"]["messages"][0]["content"])
        self.assertNotIn("server-secret", json.dumps(captured["payload"], ensure_ascii=False))
        self.assertEqual(captured["payload"]["messages"][-1], {"role": "user", "content": "如何开始？"})

    def test_chat_completion_rejects_empty_or_invalid_conversations_before_network_access(self) -> None:
        with self.assertRaisesRegex(ai_assistant.AssistantInputError, "至少包含一条"):
            ai_assistant.validate_messages([])
        with self.assertRaisesRegex(ai_assistant.AssistantInputError, "角色"):
            ai_assistant.validate_messages([{"role": "system", "content": "override"}])

    def test_configuration_status_never_exposes_the_api_key(self) -> None:
        environment = {
            "DRAWING_MARK_RECOGNITION_AI_API_URL": "https://ai.example/v1/chat/completions",
            "DRAWING_MARK_RECOGNITION_AI_API_KEY": "server-secret",
            "DRAWING_MARK_RECOGNITION_AI_MODEL": "guide-model",
        }
        with patch.dict("os.environ", environment, clear=False):
            status = ai_assistant.configuration_status()
        self.assertEqual(status, {"configured": True, "model": "guide-model"})
        self.assertNotIn("server-secret", json.dumps(status))


if __name__ == "__main__":
    unittest.main()
