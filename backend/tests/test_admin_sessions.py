from __future__ import annotations

from pathlib import Path

from backend.admin_store import AdminStore, ONLINE_WINDOW_SECONDS
from backend.auth_store import AuthStore


def test_admin_sessions_lists_online_and_idle_users_without_credentials(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    clock = [1_000.0]
    auth = AuthStore(database, now=lambda: clock[0])
    auth.initialize()
    active_user = auth.register("active-user", "password-123")
    idle_user = auth.register("idle-user", "password-456", is_admin=True)
    active_token = auth.create_session(str(active_user["userId"]))
    auth.create_session(str(idle_user["userId"]))

    clock[0] += ONLINE_WINDOW_SECONDS + 1
    assert auth.resolve_session(active_token) is not None
    result = AdminStore(database, now=lambda: clock[0]).sessions()

    assert result["pagination"]["total"] == 2
    assert result["onlineCount"] == 1
    assert [item["username"] for item in result["sessions"]] == ["active-user", "idle-user"]
    assert result["sessions"][0]["online"] is True
    assert result["sessions"][1]["online"] is False
    assert result["sessions"][1]["idleSeconds"] == ONLINE_WINDOW_SECONDS + 1
    assert "session_hash" not in str(result)
    assert "token" not in str(result).lower()


def test_admin_sessions_supports_search_and_excludes_expired_sessions(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    clock = [2_000.0]
    auth = AuthStore(database, now=lambda: clock[0], session_ttl_seconds=60)
    auth.initialize()
    matching = auth.register("reviewer-one", "password-123")
    other = auth.register("operator-two", "password-456")
    auth.create_session(str(matching["userId"]))
    auth.create_session(str(other["userId"]))

    result = AdminStore(database, now=lambda: clock[0]).sessions(search="VIEWER")
    assert result["pagination"]["total"] == 1
    assert result["sessions"][0]["username"] == "reviewer-one"

    clock[0] += 61
    expired = AdminStore(database, now=lambda: clock[0]).sessions()
    assert expired["pagination"]["total"] == 0
    assert expired["sessions"] == []
