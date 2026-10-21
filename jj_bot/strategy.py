"""Core rules engine for JJ's strategy.

Turns the discretionary ideas from the video (plus a trader's live-session
notes) into concrete, backtestable rules:

- Anchor = the opening 1-minute candle of a trading session ("fair price").
  Per explicit user request, this now runs across FIVE independent daily
  sessions, not just the NY open: NY AM (09:30 ET, unchanged from before --
  90 min total, cutoff 11:00), plus NY PM (14:00), Session (18:00), Asia
  (20:00), and London (03:00), each running the exact same shape/rules as
  NY AM just anchored at a different time and lasting
  additional_session_duration_minutes (2 hours) instead of NY AM's own
  reversion_end_minutes/hard_cutoff. None of these windows overlap.
- Phase 1 (0-10 min after a session's own open): continuation in the
  direction of that session's opening candle's body.
- Phase 2 (10 min after open until that session's own cutoff): mean
  reversion back toward that session's own open, once price has displaced
  away from it.
- Entry trigger ("displacement" + "break of structure and close"): a candle
  whose *true range* (volatility-adjusted, gap-aware) is notably larger than
  recent bars and the previous bar, whose body dominates the candle (small
  wicks), that closes beyond a recent swing high/low by more than a small
  noise buffer.
- Fixed 1:1.5 R:R (configurable stop/target in points).
- Caps: one trade per ACCOUNT per DAY (risk_cfg.max_trades_per_day) -- all 5
  sessions are scanned, but once any one of them produces that day's trade
  the rest of the day is done, regardless of which session got there first.
  (VirtualAccountManager deliberately resets this gate every bar so its
  underlying engine keeps finding fresh distinct setups across all 5
  sessions all day, to hand out to whichever of its own several accounts
  hasn't traded yet -- see jj_bot/virtual_accounts.py. A plain single-account
  engine, as the real live runner uses, is NOT wrapped that way, so this gate
  applies to it exactly as it reads.) Also: stop after N consecutive losses
  in the day, no entries after a session's own cutoff, and a daily dollar
  rate limiter (stop trading for the rest of the day once the day's running
  P&L hits a profit cap or a loss cap) -- these day-level risk controls are
  shared across all 5 sessions, not reset per session, since they're a
  dollar-risk circuit breaker, not a per-session opportunity.

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
class _SessionWindow:
    """Static config for one daily session: when it opens and how long it
    runs. continuation_end_minutes is shared across ALL sessions
    (strategy_cfg.continuation_end_minutes) since every session uses the
    same shape -- only the anchor time and total duration differ."""
    name: str
    open_hour: int
    open_minute: int
    duration_minutes: int


@dataclass
class _SessionState:
    """Runtime state for one session, reset once per calendar day. Mirrors
    exactly what used to be the engine's own single set of fields back when
    there was only ever one session (NY AM)."""
    window: _SessionWindow
    open_bar: Optional[Bar] = None
    open_price: Optional[float] = None
    open_dt: Optional[datetime] = None
    cutoff_dt: Optional[datetime] = None
    phase: Phase = Phase.WAITING_FOR_OPEN
    continuation_direction: Optional[Direction] = None
    traded: bool = False  # this session has already taken its one trade today

    def reset(self) -> None:
        self.open_bar = None
        self.open_price = None
        self.open_dt = None
        self.cutoff_dt = None
        self.phase = Phase.WAITING_FOR_OPEN
        self.continuation_direction = None
        self.traded = False


@dataclass
class StrategyEngine:
    strategy_cfg: StrategyConfig
    risk_cfg: RiskConfig
    instrument_cfg: Optional[InstrumentConfig] = None

    day_bars: list[Bar] = field(default_factory=list)
    phase: Phase = Phase.WAITING_FOR_OPEN

    pivot_highs: list[_Pivot] = field(default_factory=list)
    pivot_lows: list[_Pivot] = field(default_factory=list)

    # Day-level (NOT per-session) risk state -- shared across all 5
    # sessions deliberately, since these are dollar-risk circuit breakers
    # for the whole day, not a per-session opportunity count.
    trades_today: int = 0
    consecutive_losses: int = 0
    position_open: bool = False
    day_pnl_points: float = 0.0
    rate_limited: bool = False

    sessions: list[_SessionState] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.sessions:
            return  # already constructed (e.g. a test passed sessions= explicitly)
        windows = [_SessionWindow("NY AM", *_hhmm(self.strategy_cfg.session_open), self.strategy_cfg.reversion_end_minutes)]
        for entry in self.strategy_cfg.additional_sessions:
            hour, minute = _hhmm(entry["open"])
            windows.append(_SessionWindow(entry["name"], hour, minute, self.strategy_cfg.additional_session_duration_minutes))
        self.sessions = [_SessionState(window=w) for w in windows]

    # ---- backward-compatible accessors for the NY AM session specifically
    # (tests and structure_debug's own bookkeeping read these; before multi-
    # session support, these WERE the engine's own top-level fields) ----
    @property
    def open_bar(self) -> Optional[Bar]:
        return self.sessions[0].open_bar

    @property
    def open_price(self) -> Optional[float]:
        return self.sessions[0].open_price

    @property
    def open_dt(self) -> Optional[datetime]:
        return self.sessions[0].open_dt

    @property
    def cutoff_dt(self) -> Optional[datetime]:
        return self.sessions[0].cutoff_dt

    @property
    def continuation_direction(self) -> Optional[Direction]:
        return self.sessions[0].continuation_direction

    def reset_day(self) -> None:
        self.day_bars = []
        self.phase = Phase.WAITING_FOR_OPEN
        self.pivot_highs = []
        self.pivot_lows = []
        self.trades_today = 0
        self.consecutive_losses = 0
        self.position_open = False
        self.day_pnl_points = 0.0
        self.rate_limited = False
        for session in self.sessions:
            session.reset()

    def _dollar_per_point(self) -> float:
        # Per-contract dollar value times contracts_per_trade — the daily
        # profit/loss cap check below is against real account dollars, and
        # config.yaml explicitly runs 2 contracts/trade ("never 1
        # contract"), so omitting this multiplier would let the cap take
        # 2x the intended points move to trip (confirmed: the live runner's
        # own per-fill P&L calc already applies this same multiplier via
        # filled_qty — this was the one place still missing it).
        if self.instrument_cfg is None or self.instrument_cfg.tick_size <= 0:
            return 1.0
        return (self.instrument_cfg.tick_value / self.instrument_cfg.tick_size) * self.risk_cfg.contracts_per_trade

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

    def _find_session(self, ts: datetime) -> Optional[_SessionState]:
        """Which configured session window (by time-of-day) this bar falls
        in, if any -- sessions are configured to never overlap and never
        cross midnight, so at most one can ever match. Recomputed fresh
        from ts's own date every call (unlike the old single-session code,
        there's no overnight-rollover ambiguity to guard against here)."""
        for session in self.sessions:
            w = session.window
            window_open = ts.replace(hour=w.open_hour, minute=w.open_minute, second=0, microsecond=0)
            window_cutoff = window_open + timedelta(minutes=w.duration_minutes)
            if window_open <= ts < window_cutoff:
                return session
        return None

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
        extreme of the bars seen so far, so a real break of structure can
        still be recognized before a pivot has confirmed — but bounded to the
        same structure_lookback window pivots use, not the whole day_bars
        history. Without this bound, a live process that started well before
        the session's own anchor (day_bars holds every bar since the
        calendar-day reset, not just bars since this session opened) could
        anchor the fallback to a stale extreme from outside the intended
        lookback window, silently blocking a live break of structure."""
        cutoff = self.day_bars[-1].timestamp - timedelta(minutes=self.strategy_cfg.structure_lookback)
        prior_bars = [b for b in self.day_bars[:-1] if b.timestamp >= cutoff]
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

    def _htf_bias(self) -> Optional[Direction]:
        """Higher-timeframe bias confluence: no separate HTF data feed is
        available, so approximate it by aggregating the most recent
        htf_bar_minutes 1-min bars into one synthetic candle and reading its
        direction. This is a grading confluence only, never a gate."""
        n = self.strategy_cfg.htf_bar_minutes
        if len(self.day_bars) < n:
            return None
        recent = self.day_bars[-n:]
        if recent[-1].close > recent[0].open:
            return Direction.LONG
        if recent[-1].close < recent[0].open:
            return Direction.SHORT
        return None

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

    def structure_debug(self) -> Optional[str]:
        """Compact one-liner explaining why the current bar did/didn't break
        structure — the session, direction being watched, the tracked
        level, and the distance to the break threshold. Meant to be tacked
        onto the existing per-bar log line so a "why didn't it trade"
        question is answerable straight from bot_log.txt. Returns None when
        there's no active session, it already traded, a position is
        already open, or there's nothing meaningful to show yet."""
        if not self.day_bars:
            return None
        bar = self.day_bars[-1]
        session = self._find_session(bar.timestamp)
        if session is None or session.open_bar is None or session.traded or self.position_open:
            return None
        if session.phase not in (Phase.CONTINUATION, Phase.REVERSION):
            return None
        if session.phase == Phase.CONTINUATION:
            if session.continuation_direction is None:
                return None
            direction = session.continuation_direction
            extension_note = ""
        else:
            extension = bar.close - session.open_price
            if abs(extension) < self.strategy_cfg.min_extension_points:
                return f"[{session.window.name}] reversion: extension {extension:+.2f} below min {self.strategy_cfg.min_extension_points}"
            direction = Direction.SHORT if extension > 0 else Direction.LONG
            extension_note = f" ext={extension:+.2f}"
        bos, level = self._break_of_structure(bar, direction)
        if level is None:
            return f"[{session.window.name}] {session.phase.value}: dir={direction.value}{extension_note} no tracked level yet"
        buffer = self.strategy_cfg.break_buffer_points
        threshold = level - buffer if direction == Direction.SHORT else level + buffer
        # Signed margin: how far past the break threshold the close is, in
        # the direction that would count as breaking (positive = broke,
        # negative = short of it by that many points).
        margin = threshold - bar.close if direction == Direction.SHORT else bar.close - threshold
        return (
            f"[{session.window.name}] {session.phase.value}: dir={direction.value}{extension_note} level={level:.2f} "
            f"threshold={threshold:.2f} {'BROKE' if bos else f'{abs(margin):.2f}pts short'}"
        )

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Feed one confirmed bar. Returns a Signal if a new entry should be taken."""
        self.day_bars.append(bar)
        self._update_pivots()

        if self.rate_limited:
            self.phase = Phase.DONE_FOR_DAY
            return None
        if self.consecutive_losses >= self.risk_cfg.stop_after_consecutive_losses:
            self.phase = Phase.DONE_FOR_DAY
            return None
        if self.trades_today >= self.risk_cfg.max_trades_per_day:
            # Per explicit user request: one trade per ACCOUNT per DAY, not
            # per session -- all 5 sessions are still scanned, but once any
            # one of them has produced the day's trade, every later session
            # must stay quiet for the rest of the day too.
            self.phase = Phase.DONE_FOR_DAY
            return None

        session = self._find_session(bar.timestamp)
        if session is None:
            # Between sessions (or past the last one for today) -- nothing
            # to do, just wait for the next one. Deliberately NOT
            # DONE_FOR_DAY: a later session still gets its own chance.
            self.phase = Phase.WAITING_FOR_OPEN
            return None

        if session.open_bar is None:
            # First bar seen within this session's window anchors its "fair
            # price" — not necessarily the literal first minute (a live
            # process restarting mid-window anchors late rather than never
            # anchoring at all, same reasoning the single-session version
            # used to document here).
            open_dt = bar.timestamp.replace(hour=session.window.open_hour, minute=session.window.open_minute, second=0, microsecond=0)
            session.open_bar = bar
            session.open_price = bar.open
            session.open_dt = open_dt
            session.cutoff_dt = open_dt + timedelta(minutes=session.window.duration_minutes)
            session.continuation_direction = Direction.SHORT if not bar.is_green else Direction.LONG
            session.phase = Phase.CONTINUATION
            self.phase = session.phase
            return None

        if session.traded:
            # This session already took its one trade today -- a LATER
            # session (not this one) still gets its own independent shot,
            # so this is WAITING_FOR_OPEN from this session's perspective,
            # not a day-ending state.
            session.phase = Phase.DONE_FOR_DAY
            self.phase = session.phase
            return None

        if bar.timestamp >= session.cutoff_dt:
            session.phase = Phase.DONE_FOR_DAY
            self.phase = session.phase
            return None

        mins = minutes_since(session.open_dt, bar.timestamp)
        if mins <= self.strategy_cfg.continuation_end_minutes:
            session.phase = Phase.CONTINUATION
        elif mins <= session.window.duration_minutes:
            session.phase = Phase.REVERSION
        else:
            session.phase = Phase.DONE_FOR_DAY
        self.phase = session.phase

        if self.position_open:
            return None

        if mins <= self.strategy_cfg.continuation_end_minutes:
            direction = session.continuation_direction
            if direction is None:
                return None
            # Break of structure is the mandatory trigger — no BOS, no trade.
            # Displacement and HTF bias are secondary confirming factors that
            # only upgrade the setup grade; their absence must not block entry.
            bos, level = self._break_of_structure(bar, direction)
            if not bos:
                return None
            displaced = self._is_displacement(bar)
            htf_aligned = self._htf_bias() == direction
            confluences = sum([displaced, htf_aligned])
            if confluences == 2:
                grade = SetupGrade.A_PLUS
            elif confluences == 1:
                grade = SetupGrade.A
            else:
                grade = SetupGrade.B_PLUS
            reason = (
                f"[{session.window.name}] Continuation of {direction.value} opening flow, "
                f"{'displacement + ' if displaced else ''}"
                f"{'HTF-aligned + ' if htf_aligned else ''}"
                f"close through structure {level:.2f}"
            )
            signal = self._build_signal(
                bar, direction, Phase.CONTINUATION, grade, reason=reason,
            )
            self.position_open = True
            session.traded = True
            return signal

        if mins <= session.window.duration_minutes:
            extension = bar.close - session.open_price
            if abs(extension) < self.strategy_cfg.min_extension_points:
                return None
            direction = Direction.SHORT if extension > 0 else Direction.LONG
            # Break of structure is the mandatory trigger — no BOS, no trade.
            # Displacement and HTF bias are secondary confirming factors that
            # only upgrade the setup grade.
            bos, level = self._break_of_structure(bar, direction)
            if not bos:
                return None
            displaced = self._is_displacement(bar)
            htf_aligned = self._htf_bias() == direction
            confluences = sum([displaced, htf_aligned])
            if confluences == 2:
                grade = SetupGrade.A_PLUS
            elif confluences == 1:
                grade = SetupGrade.A
            else:
                grade = SetupGrade.B_PLUS
            signal = self._build_signal(
                bar, direction, Phase.REVERSION, grade,
                reason=(
                    f"[{session.window.name}] Mean reversion toward open {session.open_price:.2f} "
                    f"(extended {extension:+.2f} pts), "
                    f"{'displacement + ' if displaced else ''}"
                    f"{'HTF-aligned + ' if htf_aligned else ''}"
                    f"close through structure {level:.2f}"
                ),
            )
            self.position_open = True
            session.traded = True
            return signal

        return None


def _hhmm(s: str) -> tuple[int, int]:
    t = parse_hhmm(s)
    return t.hour, t.minute
