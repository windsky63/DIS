from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import assistant_tools
from document_knowledge import KnowledgeChunk, KnowledgeError, KnowledgeSource


class _Knowledge:
    def __init__(self, chunks: list[KnowledgeChunk] | None = None) -> None:
        self.chunks = chunks or []
        self.search_calls: list[tuple[str, int]] = []

    def search(self, query: str, limit: int = 6) -> list[KnowledgeChunk]:
        self.search_calls.append((query, limit))
        return self.chunks[:limit]

    def read_section(self, document_id: str, section_id: str) -> KnowledgeChunk:
        for chunk in self.chunks:
            if chunk.source.document_id == document_id and chunk.section_id == section_id:
                return chunk
        raise KnowledgeError("知识文档或章节不存在")


class AssistantToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunk = KnowledgeChunk(
            source=KnowledgeSource("usage-guide", "使用指南", "docs/usage.md"),
            section="使用指南 / 保存并关闭",
            section_id="section-a1b2c3",
            start_line=30,
            end_line=34,
            content="先保存页面，再释放页面锁。",
        )

    def test_search_returns_serialized_results_and_only_real_sources(self) -> None:
        knowledge = _Knowledge([self.chunk])
        execution = assistant_tools.AssistantToolExecutor(knowledge).execute(
            "search_system_documents", '{"query":"保存并关闭","limit":5}',
        )

        payload = json.loads(execution.content)
        self.assertEqual(knowledge.search_calls, [("保存并关闭", 5)])
        self.assertEqual(payload["results"][0]["documentId"], "usage-guide")
        self.assertEqual(execution.sources, ({
            "documentId": "usage-guide",
            "title": "使用指南",
            "path": "docs/usage.md",
            "section": "使用指南 / 保存并关闭",
            "startLine": 30,
        },))
        self.assertNotIn("content", execution.sources[0])

    def test_read_section_accepts_only_stable_ids(self) -> None:
        execution = assistant_tools.AssistantToolExecutor(_Knowledge([self.chunk])).execute(
            "read_document_section",
            '{"document_id":"usage-guide","section_id":"section-a1b2c3"}',
        )

        self.assertIn("释放页面锁", json.loads(execution.content)["section"]["content"])
        self.assertEqual(execution.sources[0]["documentId"], "usage-guide")

    def test_tools_reject_unknown_names_properties_and_invalid_values(self) -> None:
        executor = assistant_tools.AssistantToolExecutor(_Knowledge())
        cases = [
            ("read_file", '{"path":".env"}', "未知工具"),
            ("search_system_documents", "not-json", "JSON"),
            ("search_system_documents", '{"query":"保存","path":".env"}', "参数"),
            ("search_system_documents", '{"query":"","limit":2}', "搜索词"),
            ("search_system_documents", json.dumps({"query": "字" * 501}), "500"),
            ("search_system_documents", '{"query":"保存","limit":7}', "1 到 6"),
            ("read_document_section", '{"document_id":"../secret","section_id":"section-a"}', "文档 ID"),
            ("read_document_section", '{"document_id":"usage-guide","section_id":"bad/id"}', "章节 ID"),
        ]
        for name, arguments, message in cases:
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaisesRegex(assistant_tools.AssistantToolError, message):
                    executor.execute(name, arguments)

    def test_search_tool_caps_serialized_content(self) -> None:
        chunks = [
            KnowledgeChunk(
                source=KnowledgeSource(f"doc-{index}", f"文档 {index}", "docs/usage.md"),
                section=f"章节 {index}", section_id=f"section-{index}",
                start_line=index + 1, end_line=index + 2, content="内容" * 1_100,
            )
            for index in range(6)
        ]

        execution = assistant_tools.AssistantToolExecutor(_Knowledge(chunks)).execute(
            "search_system_documents", '{"query":"内容","limit":6}',
        )

        self.assertLessEqual(len(execution.content), assistant_tools.MAX_TOOL_CONTENT_CHARS)
        self.assertLessEqual(len(json.loads(execution.content)["results"]), 6)
        self.assertTrue(execution.sources)


if __name__ == "__main__":
    unittest.main()
