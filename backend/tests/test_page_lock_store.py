from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest

from backend.page_lock_store import PageLockConflict, PageLockStore


class PageLockStoreTests(unittest.TestCase):
    def test_default_lease_is_180_seconds_and_heartbeat_extends_it(self) -> None:
        now = [1000.0]
        with tempfile.TemporaryDirectory() as folder_name:
            store = PageLockStore(Path(folder_name) / 'jobs.db', now=lambda: now[0])
            store.initialize()
            lock = store.acquire('job', 1, 'user', 'tester', 'tab')
            self.assertEqual(store.lease_seconds, 180)
            now[0] += 179
            store.refresh('job', 1, 'user', 'tab', lock['lockToken'])
            now[0] += 179
            self.assertIsNotNone(store.get('job', 1))
            now[0] += 2
            self.assertIsNone(store.get('job', 1))

    def test_second_browser_cannot_acquire_active_page_even_for_same_user(self) -> None:
        now = [1_000.0]
        with tempfile.TemporaryDirectory() as folder_name:
            store = PageLockStore(Path(folder_name) / "jobs.db", now=lambda: now[0], lease_seconds=90)
            store.initialize()
            first = store.acquire("job", 15, "user-1", "张三", "tab-a")

            with self.assertRaises(PageLockConflict) as raised:
                store.acquire("job", 15, "user-1", "张三", "tab-b")

            self.assertEqual(raised.exception.lock["owner"]["username"], "张三")
            self.assertEqual(store.assert_owner("job", 15, "user-1", "tab-a", first["lockToken"])["page"], 15)

    def test_expired_lock_can_be_taken_over_and_old_token_cannot_refresh(self) -> None:
        now = [1_000.0]
        with tempfile.TemporaryDirectory() as folder_name:
            store = PageLockStore(Path(folder_name) / "jobs.db", now=lambda: now[0], lease_seconds=90)
            store.initialize()
            old = store.acquire("job", 1, "user-1", "张三", "tab-a")
            now[0] += 91
            new = store.acquire("job", 1, "user-2", "李四", "tab-b")

            self.assertNotEqual(old["lockToken"], new["lockToken"])
            with self.assertRaises(PageLockConflict):
                store.refresh("job", 1, "user-1", "tab-a", old["lockToken"])

    def test_bulk_acquire_is_atomic_when_any_page_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            store = PageLockStore(Path(folder_name) / "jobs.db")
            store.initialize()
            store.acquire("job", 2, "user-2", "李四", "tab-b")

            with self.assertRaises(PageLockConflict):
                store.acquire_many("job", [1, 2, 3], "user-1", "张三", "tab-a")

            self.assertIsNone(store.get("job", 1))
            self.assertIsNone(store.get("job", 3))

    def test_release_requires_matching_owner_and_token(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            store = PageLockStore(Path(folder_name) / "jobs.db")
            store.initialize()
            lock = store.acquire("job", 7, "user-1", "张三", "tab-a")
            self.assertFalse(store.release("job", 7, "user-1", "tab-a", "wrong"))
            self.assertTrue(store.release("job", 7, "user-1", "tab-a", lock["lockToken"]))
            self.assertIsNone(store.get("job", 7))

    def test_new_login_can_release_every_lock_owned_by_the_account(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            store = PageLockStore(Path(folder_name) / "jobs.db")
            store.initialize()
            store.acquire("job-a", 1, "user-1", "张三", "tab-a")
            store.acquire("job-b", 2, "user-1", "张三", "tab-a")
            store.acquire("job-a", 3, "user-2", "李四", "tab-b")

            self.assertEqual(store.release_user("user-1"), 2)
            self.assertIsNone(store.get("job-a", 1))
            self.assertIsNone(store.get("job-b", 2))
            self.assertIsNotNone(store.get("job-a", 3))

    def test_different_pages_are_listed_for_concurrent_reviewers(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            store = PageLockStore(Path(folder_name) / "jobs.db")
            store.initialize()
            store.acquire("job", 3, "user-1", "张三", "tab-a")
            store.acquire("job", 8, "user-2", "李四", "tab-b")
            self.assertEqual([lock["page"] for lock in store.list_active("job")], [3, 8])

    def test_concurrent_acquire_has_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            store = PageLockStore(Path(folder_name) / "jobs.db")
            store.initialize()

            def attempt(index: int) -> bool:
                try:
                    store.acquire("job", 9, f"user-{index}", f"用户{index}", f"tab-{index}")
                    return True
                except PageLockConflict:
                    return False

            with ThreadPoolExecutor(max_workers=8) as executor:
                outcomes = list(executor.map(attempt, range(8)))
            self.assertEqual(outcomes.count(True), 1)


if __name__ == "__main__":
    unittest.main()
