"""Shared dataclasses used across strategy, backtest, and live runner."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


@dataclass
class Bar:
    timestamp: datetime  # tz-aware, America/New_York
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_green(self) -> bool:
        return self.close > self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def wick_ratio(self) -> float:
        r = self.range
        if r <= 0:
            return 1.0
        return (self.upper_wick + self.lower_wick) / r


class Direction(Enum):
    LONG = "long"
    SHORT = "short"


class Phase(Enum):
    WAITING_FOR_OPEN = "waiting_for_open"
    CONTINUATION = "continuation"
    REVERSION = "reversion"
    DONE_FOR_DAY = "done_for_day"


class SetupGrade(Enum):
    A_PLUS = "A+"
    A = "A"
    B_PLUS = "B+"


@dataclass
class Signal:
    """A trade signal emitted by the strategy state machine."""

    timestamp: datetime
    direction: Direction
    entry_price: float
    stop_price: float
    target_price: float
    phase: Phase
    grade: SetupGrade
    reason: str


@dataclass
class TradeResult:
    signal: Signal
    exit_price: float
    exit_timestamp: datetime
    win: bool
    pnl_points: float
