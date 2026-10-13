from __future__ import annotations

from datetime import datetime, timezone

from backend.time_utils import beijing_from_timestamp, beijing_now_text


def test_beijing_timestamp_has_explicit_utc_plus_eight_offset() -> None:
    value = beijing_from_timestamp(0)
    assert value.isoformat(timespec="seconds") == "1970-01-01T08:00:00+08:00"


def test_current_beijing_time_is_aware_and_matches_current_instant() -> None:
    value = datetime.fromisoformat(beijing_now_text())
    assert value.utcoffset().total_seconds() == 8 * 60 * 60
    assert abs((value.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()) < 2
