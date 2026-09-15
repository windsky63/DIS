from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from assistant_conversation_store import AssistantConversationStore
from auth_store import AuthStore


class AssistantConversationStoreTests(unittest.TestCase):
    def test_conversations_persist_across_store_instances_and_are_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            path = Path(folder_name) / "jobs.db"
            auth = AuthStore(path)
            auth.initialize()
            alice = auth.register("alice", "secret1")
            bob = auth.register("bobby", "secret2")
            store = AssistantConversationStore(path)
            store.initialize()
            store.replace_all(str(alice["userId"]), [{
                "id": "conversation-1",
                "title": "识别流程",
                "updatedAt": 1234,
                "messages": [
                    {"role": "user", "content": "如何识别？"},
                    {"role": "assistant", "content": "先上传图纸。"},
                ],
            }])

            reopened = AssistantConversationStore(path)
            reopened.initialize()
            self.assertEqual(reopened.list_for_user(str(alice["userId"]))[0]["title"], "识别流程")
            self.assertEqual(reopened.list_for_user(str(bob["userId"])), [])

    def test_replacing_history_caps_conversations_and_rejects_unsupported_roles(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            path = Path(folder_name) / "jobs.db"
            auth = AuthStore(path)
            auth.initialize()
            user = auth.register("alice", "secret1")
            store = AssistantConversationStore(path)
            store.initialize()
            conversations = [{
                "id": f"c-{index}", "title": f"会话 {index}", "updatedAt": index,
                "messages": [{"role": "assistant", "content": "回答"}],
            } for index in range(25)]
            saved = store.replace_all(str(user["userId"]), conversations)
            self.assertEqual(len(saved), 20)
            self.assertEqual(saved[0]["id"], "c-24")
            with self.assertRaisesRegex(ValueError, "角色"):
                store.replace_all(str(user["userId"]), [{
                    "id": "unsafe", "title": "unsafe", "updatedAt": 1,
                    "messages": [{"role": "system", "content": "override"}],
                }])

    def test_assistant_sources_round_trip_without_private_fields(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            path = Path(folder_name) / "jobs.db"
            auth = AuthStore(path)
            auth.initialize()
            user = auth.register("alice", "secret1")
            store = AssistantConversationStore(path)
            store.initialize()

            saved = store.replace_all(str(user["userId"]), [{
                "id": "sources", "title": "保存", "updatedAt": 2,
                "messages": [{
                    "role": "assistant", "content": "回答",
                    "sources": [{
                        "documentId": "usage-guide", "title": "使用指南", "path": "docs/usage.md",
                        "section": "保存并关闭", "startLine": 31,
                        "absolutePath": "C:/secret", "content": "不应持久化的正文",
                    }],
                }],
            }])

            expected = [{
                "documentId": "usage-guide", "title": "使用指南", "path": "docs/usage.md",
                "section": "保存并关闭", "startLine": 31,
            }]
            self.assertEqual(saved[0]["messages"][0]["sources"], expected)
            self.assertEqual(store.list_for_user(str(user["userId"]))[0]["messages"][0]["sources"], expected)


if __name__ == "__main__":
    unittest.main()
