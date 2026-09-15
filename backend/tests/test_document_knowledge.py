from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import document_knowledge


class DocumentKnowledgeTests(unittest.TestCase):
    def make_project(self, files: dict[str, str], documents: list[dict[str, str]]) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for relative_path, content in files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        manifest = root / "docs" / "assistant-knowledge.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"version": 1, "documents": documents}, ensure_ascii=False), encoding="utf-8")
        return root

    def test_chinese_query_prefers_matching_heading_and_preserves_source(self) -> None:
        root = self.make_project({
            "README.md": "# 产品\n\n这是系统简介。",
            "docs/usage.md": "# 使用指南\n\n## 保存并关闭\n\n先保存页面，再释放页面锁。",
        }, [
            {"id": "overview", "path": "README.md", "title": "产品"},
            {"id": "usage", "path": "docs/usage.md", "title": "使用指南"},
        ])

        results = document_knowledge.DocumentKnowledge(root).search("保存并关闭 页面锁")

        self.assertEqual(results[0].source.document_id, "usage")
        self.assertEqual(results[0].section, "使用指南 / 保存并关闭")
        self.assertEqual(results[0].source.relative_path, "docs/usage.md")
        self.assertIn("释放页面锁", results[0].content)
        self.assertNotIn(str(root), json.dumps(results[0].as_tool_payload(), ensure_ascii=False))

    def test_manifest_blocks_unapproved_missing_and_duplicate_documents(self) -> None:
        cases = [
            ([{"id": "secret", "path": ".env", "title": "密钥"}], {".env": "SECRET=value"}, "不允许"),
            ([{"id": "escape", "path": "../README.md", "title": "越界"}], {}, "不允许"),
            ([{"id": "missing", "path": "docs/usage.md", "title": "缺失"}], {}, "不存在"),
            ([
                {"id": "same", "path": "README.md", "title": "一"},
                {"id": "same", "path": "docs/usage.md", "title": "二"},
            ], {"README.md": "# 一", "docs/usage.md": "# 二"}, "重复"),
        ]
        for documents, files, message in cases:
            with self.subTest(message=message, documents=documents):
                root = self.make_project(files, documents)
                knowledge = document_knowledge.DocumentKnowledge(root)
                status = knowledge.status()
                self.assertFalse(status["configured"])
                self.assertIn(message, status["error"])
                with self.assertRaisesRegex(document_knowledge.KnowledgeError, message):
                    knowledge.search("系统")

    def test_long_sections_are_bounded_and_code_fences_remain_whole(self) -> None:
        code = "```powershell\npython backend/server.py\npython backend/worker.py\n```"
        paragraphs = [f"第 {index} 段描述页面锁和保存行为。" * 12 for index in range(24)]
        root = self.make_project({
            "docs/development.md": "# 开发\n\n## 启动命令\n\n" + code + "\n\n" + "\n\n".join(paragraphs),
        }, [{"id": "development", "path": "docs/development.md", "title": "开发"}])

        knowledge = document_knowledge.DocumentKnowledge(root)
        results = knowledge.search("页面锁 保存", limit=99)

        self.assertLessEqual(len(results), 6)
        self.assertTrue(all(len(item.content) <= 2200 for item in results))
        indexed = knowledge.chunks()
        code_chunks = [item.content for item in indexed if "python backend/server.py" in item.content]
        self.assertEqual(len(code_chunks), 1)
        self.assertIn(code, code_chunks[0])

    def test_index_rebuilds_after_a_document_changes(self) -> None:
        root = self.make_project({
            "docs/usage.md": "# 使用指南\n\n## 保存\n\n保存页面。",
        }, [{"id": "usage", "path": "docs/usage.md", "title": "使用指南"}])
        knowledge = document_knowledge.DocumentKnowledge(root)
        self.assertEqual(knowledge.search("释放页面锁"), [])

        path = root / "docs" / "usage.md"
        path.write_text("# 使用指南\n\n## 保存并关闭\n\n保存后释放页面锁。", encoding="utf-8")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

        self.assertIn("释放页面锁", knowledge.search("释放页面锁")[0].content)

    def test_read_section_uses_only_ids_returned_by_the_index(self) -> None:
        root = self.make_project({
            "docs/architecture.md": "# 架构\n\n## 页面保存与协作\n\nrevision 冲突时拒绝覆盖。",
        }, [{"id": "architecture", "path": "docs/architecture.md", "title": "系统架构"}])
        knowledge = document_knowledge.DocumentKnowledge(root)
        match = knowledge.search("revision 冲突")[0]

        section = knowledge.read_section("architecture", match.section_id)

        self.assertIn("拒绝覆盖", section.content)
        with self.assertRaisesRegex(document_knowledge.KnowledgeError, "不存在"):
            knowledge.read_section("architecture", "missing-section")


if __name__ == "__main__":
    unittest.main()
