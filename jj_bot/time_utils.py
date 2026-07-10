"""Session-boundary helpers, all in America/New_York local time."""
from __future__ import annotations

from datetime import datetime, time

import pytz


def to_et(dt: datetime, tz_name: str = "America/New_York") -> datetime:
    tz = pytz.timezone(tz_name)
    if dt.tzinfo is None:
        return pytz.utc.localize(dt).astimezone(tz)
    return dt.astimezone(tz)


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def minutes_since(open_dt: datetime, dt: datetime) -> float:
    return (dt - open_dt).total_seconds() / 60.0
