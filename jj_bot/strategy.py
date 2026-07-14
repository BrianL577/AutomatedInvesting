"""Core rules engine for JJ's NY-session strategy.

Turns the discretionary ideas from the video into concrete, backtestable rules:

- Anchor = the 9:30 AM ET opening 1-minute candle ("fair price").
- Phase 1 (0-10 min after open): continuation in the direction of the opening
  candle's body.
- Phase 2 (10-90 min after open, until a hard cutoff): mean reversion back
  toward the open, once price has displaced away from it.
- Entry trigger ("displacement" + "break of structure and close"): a candle
  whose *true range* (volatility-adjusted, gap-aware) is notably larger than
  recent bars and the previous bar, whose body dominates the candle (small
  wicks), that closes beyond a recent swing high/low by more than a small
  noise buffer.
- Fixed 1:1.5 R:R (configurable stop/target in points).
- Caps: max trades/day, stop after N consecutive losses, no entries after the
  hard cutoff, and a daily dollar rate limiter (stop trading once the day's
  running P&L hits a profit cap or a loss cap).

The engine is fed one confirmed (closed) bar at a time via `on_bar`. It is
pure decision logic — it does not manage open positions or place orders;
callers (backtest.py / live_runner.py) own trade lifecycle and call
`record_trade_result` when a trade closes so the engine can enforce daily
limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .config import StrategyConfig, RiskConfig, InstrumentConfig
from .models import Bar, Direction, Phase, Signal, SetupGrade
from .time_utils import minutes_since, parse_hhmm


def true_range(bar: Bar, prev_close: Optional[float]) -> float:
    """Volatility-adjusted, gap-aware range: accounts for gaps between bars,
    not just the bar's own high-low, so displacement detection isn't fooled
    by a quiet-looking candle that actually gapped hard from the prior close."""
    if prev_close is None:
        return bar.range
    return max(bar.range, abs(bar.high - prev_close), abs(bar.low - prev_close))


@dataclass
class _Pivot:
    timestamp: datetime
    price: float


@dataclass
class StrategyEngine:
    strategy_cfg: StrategyConfig
    risk_cfg: RiskConfig
    instrument_cfg: Optional[InstrumentConfig] = None

    day_bars: list[Bar] = field(default_factory=list)
    open_bar: Optional[Bar] = None
    open_price: Optional[float] = None
    phase: Phase = Phase.WAITING_FOR_OPEN

    pivot_highs: list[_Pivot] = field(default_factory=list)
    pivot_lows: list[_Pivot] = field(default_factory=list)

    trades_today: int = 0
    consecutive_losses: int = 0
    position_open: bool = False
    continuation_direction: Optional[Direction] = None
    day_pnl_points: float = 0.0
    rate_limited: bool = False

    def reset_day(self) -> None:
        self.day_bars = []
        self.open_bar = None
        self.open_price = None
        self.phase = Phase.WAITING_FOR_OPEN
        self.pivot_highs = []
        self.pivot_lows = []
        self.trades_today = 0
        self.consecutive_losses = 0
        self.position_open = False
        self.continuation_direction = None
        self.day_pnl_points = 0.0
        self.rate_limited = False

    def _dollar_per_point(self) -> float:
        if self.instrument_cfg is None or self.instrument_cfg.tick_size <= 0:
            return 1.0
        return self.instrument_cfg.tick_value / self.instrument_cfg.tick_size

    def record_trade_result(self, win: bool, pnl_points: float = 0.0) -> None:
        self.trades_today += 1
        self.consecutive_losses = 0 if win else self.consecutive_losses + 1
        self.position_open = False
        self.day_pnl_points += pnl_points

        day_pnl_dollars = self.day_pnl_points * self._dollar_per_point()
        if day_pnl_dollars >= self.risk_cfg.daily_profit_cap:
            self.rate_limited = True
            self.phase = Phase.DONE_FOR_DAY
        elif day_pnl_dollars <= -self.risk_cfg.daily_loss_cap:
            self.rate_limited = True
            self.phase = Phase.DONE_FOR_DAY

    def _session_open_time(self, dt: datetime) -> datetime:
        t = parse_hhmm(self.strategy_cfg.session_open)
        return dt.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)

    def _hard_cutoff_time(self, dt: datetime) -> datetime:
        t = parse_hhmm(self.strategy_cfg.hard_cutoff)
        return dt.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)

    def _update_pivots(self) -> None:
        n = len(self.day_bars)
        strength = self.strategy_cfg.swing_strength
        idx = n - 1 - strength
        if idx < strength:
            return
        window = self.day_bars[idx - strength: idx + strength + 1]
        center = self.day_bars[idx]
        if center.high == max(b.high for b in window):
            self.pivot_highs.append(_Pivot(center.timestamp, center.high))
        if center.low == min(b.low for b in window):
            self.pivot_lows.append(_Pivot(center.timestamp, center.low))

        cutoff = self.day_bars[-1].timestamp - timedelta(minutes=self.strategy_cfg.structure_lookback)
        self.pivot_highs = [p for p in self.pivot_highs if p.timestamp >= cutoff]
        self.pivot_lows = [p for p in self.pivot_lows if p.timestamp >= cutoff]

    def _nearest_structure(self, direction: Direction) -> Optional[float]:
        """Nearest relevant swing level to break-and-close through.

        Confirmed pivots need swing_strength bars of pullback on both sides,
        so on a clean, monotonic trend (no pullback yet to confirm a pivot in
        the trend direction) they never form in time. Fall back to the
        extreme of the bars seen so far this session, so a real break of
        structure can still be recognized before a pivot has confirmed."""
        prior_bars = self.day_bars[:-1]
        if direction == Direction.SHORT:
            if self.pivot_lows:
                return min(p.price for p in self.pivot_lows)
            if not prior_bars:
                return None
            return min(b.low for b in prior_bars)
        else:
            if self.pivot_highs:
                return max(p.price for p in self.pivot_highs)
            if not prior_bars:
                return None
            return max(b.high for b in prior_bars)

    def _is_displacement(self, bar: Bar) -> bool:
        """A displacement candle: true-range notably larger than both the
        recent volatility baseline and the previous bar, with a body that
        dominates the range (small wicks) — i.e. real directional force, not
        just a wide, indecisive candle."""
        idx = len(self.day_bars) - 1
        if idx == 0:
            return False
        lookback = self.day_bars[max(0, idx - 10):idx]
        if not lookback:
            return False

        lookback_closes = [None] + [b.close for b in lookback[:-1]]
        lookback_tr = [true_range(b, prev_c) for b, prev_c in zip(lookback, lookback_closes)]
        avg_tr = sum(lookback_tr) / len(lookback_tr)

        prev = self.day_bars[idx - 1]
        bar_tr = true_range(bar, prev.close)
        prev_tr = true_range(prev, self.day_bars[idx - 2].close if idx >= 2 else None)

        if avg_tr <= 0 or prev_tr <= 0:
            return False
        if bar_tr < self.strategy_cfg.displacement_size_ratio * avg_tr:
            return False
        if bar_tr < self.strategy_cfg.displacement_prev_ratio * prev_tr:
            return False
        if bar.wick_ratio > self.strategy_cfg.max_wick_ratio:
            return False
        return True

    def _break_of_structure(self, bar: Bar, direction: Direction) -> tuple[bool, Optional[float]]:
        """Break-of-structure requires the close to clear the nearest swing
        level by more than a small buffer, so marginal/noise breaks (a close
        one tick past a prior low) don't get treated as a real BOS."""
        level = self._nearest_structure(direction)
        if level is None:
            return False, None
        buffer = self.strategy_cfg.break_buffer_points
        if direction == Direction.SHORT:
            return bar.close < level - buffer, level
        return bar.close > level + buffer, level

    def _build_signal(
        self, bar: Bar, direction: Direction, phase: Phase, grade: SetupGrade, reason: str
    ) -> Signal:
        entry = bar.close
        if direction == Direction.LONG:
            stop = entry - self.risk_cfg.stop_points
            target = entry + self.risk_cfg.target_points
        else:
            stop = entry + self.risk_cfg.stop_points
            target = entry - self.risk_cfg.target_points
        return Signal(
            timestamp=bar.timestamp,
            direction=direction,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            phase=phase,
            grade=grade,
            reason=reason,
        )

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Feed one confirmed bar. Returns a Signal if a new entry should be taken."""
        self.day_bars.append(bar)
        self._update_pivots()

        open_dt = self._session_open_time(bar.timestamp)
        cutoff_dt = self._hard_cutoff_time(bar.timestamp)

        if self.open_bar is None:
            if bar.timestamp < open_dt:
                return None
            # First bar at or after the session open anchors "fair price" —
            # NOT an exact-equality match. A live process that starts or
            # restarts (e.g. a crash-recovery restart mid-session) after the
            # literal open-minute bar has already passed would otherwise
            # never see a bar equal to open_dt and get stuck in
            # WAITING_FOR_OPEN for the rest of the day, silently never
            # trading again until the next calendar day — confirmed live.
            # Anchoring on a later bar than the true 09:30 candle uses a
            # stale-ish open price on a late restart, but that's still far
            # better than never anchoring at all.
            if bar.timestamp >= cutoff_dt:
                self.phase = Phase.DONE_FOR_DAY
                return None
            self.open_bar = bar
            self.open_price = bar.open
            self.continuation_direction = Direction.SHORT if not bar.is_green else Direction.LONG
            self.phase = Phase.CONTINUATION
            return None

        if self.rate_limited:
            self.phase = Phase.DONE_FOR_DAY
            return None
        if self.trades_today >= self.risk_cfg.max_trades_per_day:
            self.phase = Phase.DONE_FOR_DAY
            return None
        if self.consecutive_losses >= self.risk_cfg.stop_after_consecutive_losses:
            self.phase = Phase.DONE_FOR_DAY
            return None
        if bar.timestamp >= cutoff_dt:
            self.phase = Phase.DONE_FOR_DAY
            return None
        if self.position_open:
            return None

        mins = minutes_since(open_dt, bar.timestamp)

        if mins <= self.strategy_cfg.continuation_end_minutes:
            self.phase = Phase.CONTINUATION
            direction = self.continuation_direction
            if direction is None:
                return None
            # Break of structure is the mandatory trigger — no BOS, no trade.
            # Displacement is a secondary confirming factor that only
            # upgrades the setup grade; its absence must not block entry.
            bos, level = self._break_of_structure(bar, direction)
            if not bos:
                return None
            displaced = self._is_displacement(bar)
            grade = SetupGrade.A if displaced else SetupGrade.B_PLUS
            reason = (
                f"Continuation of {direction.value} opening flow, "
                f"{'displacement + ' if displaced else ''}close through structure {level:.2f}"
            )
            signal = self._build_signal(
                bar, direction, Phase.CONTINUATION, grade, reason=reason,
            )
            self.position_open = True
            return signal

        if mins <= self.strategy_cfg.reversion_end_minutes:
            self.phase = Phase.REVERSION
            extension = bar.close - self.open_price
            if abs(extension) < self.strategy_cfg.min_extension_points:
                return None
            direction = Direction.SHORT if extension > 0 else Direction.LONG
            # Break of structure is the mandatory trigger — no BOS, no trade.
            # Displacement and extension size are secondary confirming
            # factors that only upgrade the setup grade.
            bos, level = self._break_of_structure(bar, direction)
            if not bos:
                return None
            displaced = self._is_displacement(bar)
            big_extension = abs(extension) >= 1.5 * self.strategy_cfg.min_extension_points
            if displaced and big_extension:
                grade = SetupGrade.A_PLUS
            elif displaced or big_extension:
                grade = SetupGrade.A
            else:
                grade = SetupGrade.B_PLUS
            signal = self._build_signal(
                bar, direction, Phase.REVERSION, grade,
                reason=(
                    f"Mean reversion toward open {self.open_price:.2f} "
                    f"(extended {extension:+.2f} pts), "
                    f"{'displacement + ' if displaced else ''}close through structure {level:.2f}"
                ),
            )
            self.position_open = True
            return signal

        self.phase = Phase.DONE_FOR_DAY
        return None
