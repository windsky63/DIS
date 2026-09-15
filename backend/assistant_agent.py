"""Streaming DeepSeek agent loop with bounded local function tools."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Iterator, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from assistant_tools import ASSISTANT_TOOL_DEFINITIONS, AssistantToolError, AssistantToolExecutor


class AssistantAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssistantAgentEvent:
    type: Literal["status", "source", "delta"]
    payload: dict[str, object]


@dataclass
class PendingToolCall:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


def _request_stream(
    *,
    url: str,
    key: str,
    model: str,
    messages: list[dict[str, object]],
    opener: Callable[..., Any],
):
    payload = {
        "model": model,
        "messages": messages,
        "tools": list(ASSISTANT_TOOL_DEFINITIONS),
        "tool_choice": "auto",
        "temperature": 0.25,
        "stream": True,
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    return opener(request, timeout=60)


def _http_error_message(exc: HTTPError) -> str:
    detail = ""
    try:
        detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
    except Exception:
        pass
    return f"AI 服务请求失败（HTTP {exc.code}）{f'：{detail}' if detail else ''}"


def stream_agent_events(
    messages: list[dict[str, object]],
    *,
    url: str,
    key: str,
    model: str,
    tools: AssistantToolExecutor,
    opener: Callable[..., Any] = urlopen,
    max_tool_rounds: int = 3,
) -> Iterator[AssistantAgentEvent]:
    conversation = [dict(message) for message in messages]
    tool_rounds = 0
    emitted_sources: set[tuple[object, ...]] = set()
    yield AssistantAgentEvent("status", {"message": "正在分析问题"})

    while True:
        pending: dict[int, PendingToolCall] = {}
        reasoning_parts: list[str] = []
        content_seen = False
        upstream_event_seen = False
        try:
            response_context = _request_stream(
                url=url, key=key, model=model, messages=conversation, opener=opener,
            )
            with response_context as response:
                for raw_line in response:
                    try:
                        line = raw_line.decode("utf-8").strip()
                    except UnicodeDecodeError as exc:
                        raise AssistantAgentError("AI 服务返回了无效的流式数据") from exc
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        upstream = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise AssistantAgentError("AI 服务返回了无效的流式数据") from exc
                    upstream_event_seen = True
                    if isinstance(upstream.get("error"), dict):
                        raise AssistantAgentError(str(upstream["error"].get("message") or "AI 服务流式响应失败"))
                    try:
                        delta = upstream["choices"][0].get("delta") or {}
                    except (KeyError, IndexError, TypeError) as exc:
                        raise AssistantAgentError("AI 服务返回了无效的流式数据") from exc
                    if not isinstance(delta, dict):
                        raise AssistantAgentError("AI 服务返回了无效的流式数据")
                    reasoning = delta.get("reasoning_content")
                    if isinstance(reasoning, str):
                        reasoning_parts.append(reasoning)
                    tool_deltas = delta.get("tool_calls") or []
                    content = delta.get("content")
                    if tool_deltas and (content_seen or (isinstance(content, str) and content)):
                        raise AssistantAgentError("AI 服务不能在同一轮同时返回回答和工具调用")
                    if tool_deltas:
                        if not isinstance(tool_deltas, list):
                            raise AssistantAgentError("AI 服务返回了无效的工具调用")
                        for part in tool_deltas:
                            if not isinstance(part, dict) or isinstance(part.get("index"), bool) or not isinstance(part.get("index"), int):
                                raise AssistantAgentError("AI 服务返回了无效的工具调用")
                            call = pending.setdefault(part["index"], PendingToolCall())
                            if isinstance(part.get("id"), str):
                                call.call_id += part["id"]
                            function = part.get("function") or {}
                            if not isinstance(function, dict):
                                raise AssistantAgentError("AI 服务返回了无效的工具调用")
                            if isinstance(function.get("name"), str):
                                call.name += function["name"]
                            if isinstance(function.get("arguments"), str):
                                call.arguments += function["arguments"]
                    if isinstance(content, str) and content:
                        if pending:
                            raise AssistantAgentError("AI 服务不能在同一轮同时返回回答和工具调用")
                        content_seen = True
                        yield AssistantAgentEvent("delta", {"content": content})
        except HTTPError as exc:
            raise AssistantAgentError(_http_error_message(exc)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AssistantAgentError("无法连接 AI 服务，请检查接口地址和网络") from exc

        if pending:
            if tool_rounds >= max(0, max_tool_rounds):
                raise AssistantAgentError("AI 文档工具调用达到次数上限")
            tool_rounds += 1
            canonical_calls: list[dict[str, object]] = []
            ordered_calls = [pending[index] for index in sorted(pending)]
            for call in ordered_calls:
                if not call.call_id or not call.name or not call.arguments:
                    raise AssistantAgentError("AI 服务返回了不完整的工具调用")
                canonical_calls.append({
                    "id": call.call_id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                })
            assistant_message: dict[str, object] = {
                "role": "assistant",
                "content": None,
                "tool_calls": canonical_calls,
            }
            if reasoning_parts:
                assistant_message["reasoning_content"] = "".join(reasoning_parts)
            conversation.append(assistant_message)
            for call, canonical in zip(ordered_calls, canonical_calls, strict=True):
                status = "正在检索系统文档" if call.name == "search_system_documents" else "正在读取相关文档章节"
                yield AssistantAgentEvent("status", {"message": status})
                try:
                    execution = tools.execute(call.name, call.arguments)
                    tool_content = execution.content
                except AssistantToolError as exc:
                    execution = None
                    tool_content = json.dumps({"error": str(exc)}, ensure_ascii=False)
                conversation.append({
                    "role": "tool",
                    "tool_call_id": canonical["id"],
                    "content": tool_content,
                })
                if execution:
                    for source in execution.sources:
                        source_key = (
                            source.get("documentId"), source.get("section"), source.get("startLine"),
                        )
                        if source_key in emitted_sources:
                            continue
                        emitted_sources.add(source_key)
                        yield AssistantAgentEvent("source", dict(source))
            continue

        if content_seen:
            return
        if not upstream_event_seen:
            raise AssistantAgentError("AI 服务返回了空回答")
        raise AssistantAgentError("AI 服务响应中缺少回答内容")
