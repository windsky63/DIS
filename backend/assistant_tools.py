"""Validated function tools exposed to the AI assistant."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from document_knowledge import DocumentKnowledge, KnowledgeChunk, KnowledgeError


MAX_QUERY_CHARS = 500
MAX_TOOL_CONTENT_CHARS = 12_000
ID_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")
SECTION_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,128}$")

ASSISTANT_TOOL_DEFINITIONS: tuple[dict[str, object], ...] = ({
    "type": "function",
    "function": {
        "name": "search_system_documents",
        "description": "搜索图纸标识识别系统的已批准本地文档。涉及系统功能、操作、机制、部署或排查时先调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "针对用户问题提炼的简短检索词"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 6},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "read_document_section",
        "description": "读取搜索结果中某个已批准文档章节的内容。只能使用搜索结果给出的文档和章节 ID。",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "pattern": "^[a-z0-9-]{1,64}$"},
                "section_id": {"type": "string", "pattern": "^[a-z0-9-]{1,128}$"},
            },
            "required": ["document_id", "section_id"],
            "additionalProperties": False,
        },
    },
})


class AssistantToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolExecution:
    content: str
    sources: tuple[dict[str, object], ...]


def _parse_arguments(arguments_json: str) -> dict[str, Any]:
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AssistantToolError("工具参数不是有效的 JSON") from exc
    if not isinstance(arguments, dict):
        raise AssistantToolError("工具参数必须是 JSON 对象")
    return arguments


def _source_payload(chunk: KnowledgeChunk) -> dict[str, object]:
    return {
        "documentId": chunk.source.document_id,
        "title": chunk.source.title,
        "path": chunk.source.relative_path,
        "section": chunk.section,
        "startLine": chunk.start_line,
    }


def _serialize_bounded(key: str, chunks: list[KnowledgeChunk], *, singular: bool = False) -> str:
    payloads = [chunk.as_tool_payload() for chunk in chunks]
    def encode() -> str:
        value: object = payloads[0] if singular and payloads else payloads
        return json.dumps({key: value}, ensure_ascii=False)

    encoded = encode()
    while len(encoded) > MAX_TOOL_CONTENT_CHARS and payloads:
        target = max(payloads, key=lambda item: len(str(item.get("content") or "")))
        content = str(target.get("content") or "")
        if not content:
            payloads.pop()
        else:
            excess = len(encoded) - MAX_TOOL_CONTENT_CHARS
            keep = max(0, len(content) - excess - 32)
            target["content"] = content[:keep]
        encoded = encode()
    return encoded


class AssistantToolExecutor:
    def __init__(self, knowledge: DocumentKnowledge) -> None:
        self.knowledge = knowledge

    def execute(self, name: str, arguments_json: str) -> ToolExecution:
        if name == "search_system_documents":
            return self._search(_parse_arguments(arguments_json))
        if name == "read_document_section":
            return self._read(_parse_arguments(arguments_json))
        raise AssistantToolError(f"未知工具：{name}")

    def _search(self, arguments: dict[str, Any]) -> ToolExecution:
        if not set(arguments).issubset({"query", "limit"}) or "query" not in arguments:
            raise AssistantToolError("文档搜索参数无效")
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise AssistantToolError("文档搜索词不能为空")
        query = query.strip()
        if len(query) > MAX_QUERY_CHARS:
            raise AssistantToolError(f"文档搜索词不能超过 {MAX_QUERY_CHARS} 个字符")
        limit = arguments.get("limit", 6)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 6:
            raise AssistantToolError("文档搜索结果数量必须是 1 到 6")
        try:
            chunks = self.knowledge.search(query, limit)
        except KnowledgeError as exc:
            raise AssistantToolError(str(exc)) from exc
        return ToolExecution(
            content=_serialize_bounded("results", chunks),
            sources=tuple(_source_payload(chunk) for chunk in chunks),
        )

    def _read(self, arguments: dict[str, Any]) -> ToolExecution:
        if set(arguments) != {"document_id", "section_id"}:
            raise AssistantToolError("文档章节参数无效")
        document_id = arguments.get("document_id")
        section_id = arguments.get("section_id")
        if not isinstance(document_id, str) or not ID_PATTERN.fullmatch(document_id):
            raise AssistantToolError("文档 ID 无效")
        if not isinstance(section_id, str) or not SECTION_ID_PATTERN.fullmatch(section_id):
            raise AssistantToolError("章节 ID 无效")
        try:
            chunk = self.knowledge.read_section(document_id, section_id)
        except KnowledgeError as exc:
            raise AssistantToolError(str(exc)) from exc
        return ToolExecution(
            content=_serialize_bounded("section", [chunk], singular=True),
            sources=(_source_payload(chunk),),
        )
