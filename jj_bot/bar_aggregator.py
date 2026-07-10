"""Aggregates a live tick stream into confirmed 1-minute OHLCV bars."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .models import Bar
from .time_utils import to_et


@dataclass
class BarAggregator:
    tz_name: str = "America/New_York"
    _current: Optional[Bar] = None
    _current_minute: Optional[datetime] = None

    def add_tick(self, price: float, timestamp_ms: int) -> Optional[Bar]:
        """Feed one tick (price, epoch-ms timestamp). Returns a closed Bar when
        the minute rolls over, else None."""
        ts = to_et(datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc), self.tz_name)
        minute = ts.replace(second=0, microsecond=0)

        if self._current_minute is None:
            self._current_minute = minute
            self._current = Bar(timestamp=minute, open=price, high=price, low=price, close=price, volume=1)
            return None

        if minute == self._current_minute:
            self._current.high = max(self._current.high, price)
            self._current.low = min(self._current.low, price)
            self._current.close = price
            self._current.volume += 1
            return None

        closed = self._current
        self._current_minute = minute
        self._current = Bar(timestamp=minute, open=price, high=price, low=price, close=price, volume=1)
        return closed
