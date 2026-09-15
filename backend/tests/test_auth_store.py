from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.auth_store import AuthStore, InvalidCredentials, UsernameTaken


class AuthStoreTests(unittest.TestCase):
    def test_register_authenticate_and_reject_duplicate_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            path = Path(folder_name) / "jobs.db"
            store = AuthStore(path)
            store.initialize()

            user = store.register("Reviewer01", "correct-horse-battery")

            self.assertEqual(user["username"], "Reviewer01")
            self.assertEqual(store.authenticate("reviewer01", "correct-horse-battery")["userId"], user["userId"])
            with self.assertRaises(InvalidCredentials):
                store.authenticate("Reviewer01", "wrong-password")
            with self.assertRaises(UsernameTaken):
                store.register("REVIEWER01", "another-long-password")

            connection = sqlite3.connect(path)
            row = connection.execute(
                "SELECT password_hash, password_salt FROM users WHERE user_id = ?",
                (user["userId"],),
            ).fetchone()
            connection.close()
            self.assertNotIn(b"correct-horse-battery", row)

    def test_session_can_be_resolved_revoked_and_expires(self) -> None:
        now = [1_000.0]
        with tempfile.TemporaryDirectory() as folder_name:
            store = AuthStore(Path(folder_name) / "jobs.db", now=lambda: now[0], session_ttl_seconds=60)
            store.initialize()
            user = store.register("reviewer02", "correct-horse-battery")

            token = store.create_session(user["userId"])
            self.assertNotEqual(token, "")
            self.assertEqual(store.resolve_session(token)["userId"], user["userId"])

            now[0] += 61
            self.assertIsNone(store.resolve_session(token))

            fresh_token = store.create_session(user["userId"])
            store.delete_session(fresh_token)
            self.assertIsNone(store.resolve_session(fresh_token))

    def test_registration_validates_username_and_password_length(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            store = AuthStore(Path(folder_name) / "jobs.db")
            store.initialize()
            for username, password in (("ab", "correct-horse-battery"), ("validname", "short")):
                with self.subTest(username=username):
                    with self.assertRaises(ValueError):
                        store.register(username, password)

    def test_default_user_is_created_once_without_overwriting_an_existing_password(self) -> None:
        with tempfile.TemporaryDirectory() as folder_name:
            store = AuthStore(Path(folder_name) / "jobs.db")
            store.initialize()

            first = store.ensure_default_user("admin", "123456")
            second = store.ensure_default_user("admin", "changed-password")

            self.assertEqual(first["userId"], second["userId"])
            self.assertEqual(first["username"], "admin")
            self.assertEqual(store.authenticate("admin", "123456")["userId"], first["userId"])
            with self.assertRaises(InvalidCredentials):
                store.authenticate("admin", "changed-password")


if __name__ == "__main__":
    unittest.main()
