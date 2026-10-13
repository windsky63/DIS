"""Application time helpers standardized on China Standard Time (UTC+8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def beijing_now() -> datetime:
    """Return an aware current datetime in Beijing time."""

    return datetime.now(BEIJING_TIMEZONE)


def beijing_from_timestamp(value: float) -> datetime:
    """Convert a Unix timestamp to an aware Beijing datetime."""

    return datetime.fromtimestamp(float(value), tz=BEIJING_TIMEZONE)


def beijing_now_text(*, timespec: str = "seconds") -> str:
    return beijing_now().isoformat(timespec=timespec)
