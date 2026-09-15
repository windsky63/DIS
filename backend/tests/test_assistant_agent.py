from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import assistant_agent
from assistant_tools import AssistantToolError, ToolExecution


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n".encode("utf-8")


class _StreamResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.lines)


class _SequentialOpener:
    def __init__(self, responses: list[list[bytes]]) -> None:
        self.responses = responses
        self.payloads: list[dict] = []

    def __call__(self, request, timeout):
        self.payloads.append(json.loads(request.data.decode("utf-8")))
        return _StreamResponse(self.responses.pop(0))


class _Tools:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def execute(self, name: str, arguments: str) -> ToolExecution:
        self.calls.append((name, arguments))
        if self.fail:
            self.fail = False
            raise AssistantToolError("搜索参数无效")
        return ToolExecution(
            content='{"results":[{"content":"先保存，再释放页面锁。"}]}',
            sources=({
                "documentId": "usage-guide", "title": "使用指南", "path": "docs/usage.md",
                "section": "保存并关闭", "startLine": 30,
            },),
        )


def tool_response(call_id: str = "call-1") -> list[bytes]:
    return [
        sse({"choices": [{"delta": {
            "reasoning_content": "内部分析",
            "tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": {
                "name": "search_system_", "arguments": '{"query":"保存',
            }}],
        }}]}),
        sse({"choices": [{"delta": {"reasoning_content": "仍不展示", "tool_calls": [{
            "index": 0, "function": {"name": "documents", "arguments": '并关闭","limit":5}'},
        }]}, "finish_reason": "tool_calls"}]}),
        b"data: [DONE]\n",
    ]


def answer_response() -> list[bytes]:
    return [
        sse({"choices": [{"delta": {"reasoning_content": "私有推理", "content": "依据文档，"}}]}),
        sse({"choices": [{"delta": {"content": "系统会先保存。"}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n",
    ]


class AssistantAgentTests(unittest.TestCase):
    def test_split_tool_call_is_executed_and_final_answer_remains_streamed(self) -> None:
        opener = _SequentialOpener([tool_response(), answer_response()])
        tools = _Tools()

        events = list(assistant_agent.stream_agent_events(
            [{"role": "system", "content": "系统"}, {"role": "user", "content": "保存并关闭会怎样？"}],
            url="https://api.example/chat/completions", key="secret", model="guide-model",
            tools=tools, opener=opener,
        ))

        self.assertEqual([event.type for event in events], ["status", "status", "source", "delta", "delta"])
        self.assertEqual(tools.calls, [("search_system_documents", '{"query":"保存并关闭","limit":5}')])
        self.assertEqual("".join(event.payload.get("content", "") for event in events), "依据文档，系统会先保存。")
        self.assertNotIn("内部分析", repr(events))
        self.assertEqual(opener.payloads[0]["tool_choice"], "auto")
        follow_up = opener.payloads[1]["messages"]
        self.assertEqual(follow_up[-1]["role"], "tool")
        self.assertEqual(follow_up[-1]["tool_call_id"], "call-1")
        self.assertEqual(follow_up[-2]["reasoning_content"], "内部分析仍不展示")

    def test_greeting_can_stream_without_using_documents(self) -> None:
        opener = _SequentialOpener([[sse({"choices": [{"delta": {"content": "你好"}, "finish_reason": "stop"}]}), b"data: [DONE]\n"]])
        tools = _Tools()

        events = list(assistant_agent.stream_agent_events(
            [{"role": "user", "content": "你好"}], url="https://api.example", key="secret",
            model="guide-model", tools=tools, opener=opener,
        ))

        self.assertEqual([event.type for event in events], ["status", "delta"])
        self.assertEqual(tools.calls, [])

    def test_invalid_tool_arguments_are_returned_to_the_model_for_one_correction(self) -> None:
        opener = _SequentialOpener([tool_response(), answer_response()])
        tools = _Tools(fail=True)

        events = list(assistant_agent.stream_agent_events(
            [{"role": "user", "content": "保存"}], url="https://api.example", key="secret",
            model="guide-model", tools=tools, opener=opener,
        ))

        self.assertEqual([event.type for event in events], ["status", "status", "delta", "delta"])
        error_payload = json.loads(opener.payloads[1]["messages"][-1]["content"])
        self.assertEqual(error_payload, {"error": "搜索参数无效"})

    def test_tool_round_limit_stops_repeated_requests(self) -> None:
        opener = _SequentialOpener([tool_response("call-1"), tool_response("call-2")])
        with self.assertRaisesRegex(assistant_agent.AssistantAgentError, "次数上限"):
            list(assistant_agent.stream_agent_events(
                [{"role": "user", "content": "保存"}], url="https://api.example", key="secret",
                model="guide-model", tools=_Tools(), opener=opener, max_tool_rounds=1,
            ))

    def test_malformed_upstream_data_and_mixed_content_are_rejected(self) -> None:
        cases = [
            ([b"data: not-json\n"], "无效"),
            ([sse({"choices": [{"delta": {"content": "回答", "tool_calls": [{"index": 0, "id": "x", "function": {"name": "search_system_documents", "arguments": "{}"}}]}}]})], "同时"),
        ]
        for response, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(assistant_agent.AssistantAgentError, message):
                    list(assistant_agent.stream_agent_events(
                        [{"role": "user", "content": "问题"}], url="https://api.example", key="secret",
                        model="guide-model", tools=_Tools(), opener=_SequentialOpener([response]),
                    ))


if __name__ == "__main__":
    unittest.main()
