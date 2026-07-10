"""Simulates a TopStep-style evaluation account: end-of-day trailing drawdown.

This mirrors what JJ describes for backtesting on a prop firm: track pass/fail
per "run" (one simulated eval attempt) using end-of-day trailing drawdown, a
profit target, and (optionally) a daily loss limit — not a naive equity curve.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import TopstepEvalConfig, InstrumentConfig


@dataclass
class EvalAccountState:
    cfg: TopstepEvalConfig
    instrument: InstrumentConfig

    balance: float = field(init=False)
    trailing_floor: float = field(init=False)
    high_water_mark: float = field(init=False)
    passed: bool = False
    busted: bool = False
    day_pnl_points: float = 0.0

    def __post_init__(self) -> None:
        self.balance = self.cfg.account_size
        self.high_water_mark = self.cfg.account_size
        self.trailing_floor = self.cfg.account_size - self.cfg.trailing_max_drawdown

    def points_to_dollars(self, points: float) -> float:
        ticks = points / self.instrument.tick_size
        return ticks * self.instrument.tick_value

    def apply_day(self, pnl_points_for_day: float) -> None:
        """Apply one trading day's total point PnL, then trail drawdown & check status."""
        if self.passed or self.busted:
            return
        pnl_dollars = self.points_to_dollars(pnl_points_for_day)
        self.balance += pnl_dollars

        if self.cfg.daily_loss_limit is not None and pnl_dollars < -self.cfg.daily_loss_limit:
            self.busted = True
            return

        if self.balance <= self.trailing_floor:
            self.busted = True
            return

        if self.balance > self.high_water_mark:
            self.high_water_mark = self.balance
            self.trailing_floor = min(
                self.high_water_mark - self.cfg.trailing_max_drawdown,
                self.cfg.account_size,
            )
            # Trailing stops trailing once balance has cleared the profit target,
            # matching TopStep-style rules ("drawdown stops trailing at X").
            if self.high_water_mark >= self.cfg.account_size + self.cfg.profit_target:
                self.trailing_floor = self.cfg.account_size

        if self.balance >= self.cfg.account_size + self.cfg.profit_target:
            self.passed = True
