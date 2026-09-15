from __future__ import annotations

from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from document_knowledge import DocumentKnowledge


class AssistantDocumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.knowledge = DocumentKnowledge(PROJECT_ROOT)

    def test_every_manifest_document_is_valid_and_searchable(self) -> None:
        status = self.knowledge.status()
        self.assertTrue(status["configured"], status.get("error"))
        self.assertEqual(status["documentCount"], 6)
        self.assertGreaterEqual(status["chunkCount"], 20)
        document_ids = {chunk.source.document_id for chunk in self.knowledge.chunks()}
        self.assertEqual(document_ids, {
            "product-overview", "usage-guide", "system-architecture", "development-guide",
            "deployment-guide", "ai-assistant-guide",
        })

    def test_key_questions_retrieve_the_expected_technical_document(self) -> None:
        cases = {
            "页面锁 revision 保存冲突": "system-architecture",
            "DeepSeek tool_calls SSE 文档检索": "ai-assistant-guide",
            "Docker Worker 环境变量": "deployment-guide",
            "如何复制粘贴标识": "usage-guide",
        }
        for query, expected_id in cases.items():
            with self.subTest(query=query):
                results = self.knowledge.search(query)
                self.assertTrue(results)
                self.assertEqual(results[0].source.document_id, expected_id)

    def test_retrieved_sources_are_relative_and_sections_can_be_read(self) -> None:
        result = self.knowledge.search("AI 工具调用次数上限")[0]
        payload = result.as_tool_payload()
        self.assertFalse(Path(str(payload["path"])).is_absolute())
        self.assertNotIn(str(PROJECT_ROOT), str(payload))
        section = self.knowledge.read_section(result.source.document_id, result.section_id)
        self.assertIn("工具", section.content)


if __name__ == "__main__":
    unittest.main()
