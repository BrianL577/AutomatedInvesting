"""Fans a session's setups out across N abstract "virtual practice"
accounts instead of cloning one shared signal onto every account.

The real live runner (live_runner_topstepx.py) detects one signal and
places the *same* trade on every configured TopStep account. That's the
wrong shape for practice mode: these accounts aren't real TopStep accounts
at all, and the point is to see each account take its own distinct trade
out of whatever the NY-session strategy finds that day — not 10 copies of
one trade.

`VirtualAccountManager` keeps a single StrategyEngine scanning bars for
setups all session long (bypassing its single-shared-account "one trade,
then stop" gates), and hands each newly detected, distinct setup to an
idle virtual account, prioritized: accounts that haven't taken their first
trade yet ($0 balance) go first, then whichever idle account has the
highest balance, then whichever has the lowest (most negative) last (see
`_idle_account()`). One trade per account per day, same rule as real
trading. If the session produces fewer setups than accounts, the remaining
accounts simply don't trade — no synthetic trades are invented to fill the
count.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from .config import AppConfig
from .models import Bar, Direction, Signal, TradeResult
from .strategy import StrategyEngine
from .trade_logger import TradeLogger
from .logging_setup import setup_logging

logger = setup_logging()


@dataclass
class VirtualAccount:
    name: str
    pending_signal: Optional[Signal] = None
    traded_today: bool = False
    # Running lifetime $ P&L across every closed trade this account has
    # ever taken — NOT reset by reset_day() (unlike traded_today, which is
    # a daily flag). Drives the per-user-request "trade the account with
    # the most money on it first" ordering in _idle_account().
    net_dollars: float = 0.0

    def reset_day(self) -> None:
        self.pending_signal = None
        self.traded_today = False


class VirtualAccountManager:
    def __init__(
        self,
        cfg: AppConfig,
        num_accounts: Optional[int] = None,
        source: str = "virtual_practice",
        trade_log_path: Optional[Path] = None,
        chart_renderer: Optional[Callable[[VirtualAccount, TradeResult], Optional[str]]] = None,
        state_path: Optional[Path] = None,
    ):
        self.cfg = cfg
        self.engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk, instrument_cfg=cfg.instrument)
        n = num_accounts if num_accounts is not None else cfg.virtual_accounts.count
        self.accounts: list[VirtualAccount] = [VirtualAccount(name=f"Virtual-{i + 1:02d}") for i in range(n)]
        self.dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
        self.trade_logger = TradeLogger(path=trade_log_path, dollar_per_point=self.dollar_per_point, source=source)
        # Optional hook the caller can supply to render a candlestick chart
        # for a closed trade (see live_runner_topstepx.py's use of
        # trade_chart.render_trade_chart) — kept out of this module so it
        # has no matplotlib dependency and stays trivial to unit test.
        self.chart_renderer = chart_renderer
        self._current_day = None
        self._is_first_day_check = True
        # CONFIRMED: without this, a process restart mid-day silently wiped
        # every account's traded_today back to False (in-memory only, same
        # bug class the old NinjaTrader runner already hit once — see
        # live_runner_topstepx.py's identical fix). One virtual account
        # taking 3 trades in a single real day, all logged normally, is
        # exactly what that looks like: each restart's first bar treated
        # the already-in-progress calendar day as brand new.
        self._state_path = Path(state_path) if state_path else Path("virtual_daily_state.json")
        # CONFIRMED: without this, virtual accounts weren't taking 10
        # independent setups — they were chasing the SAME continuing move.
        # _nearest_structure() (strategy.py) needs a *confirmed* pivot to
        # track a meaningful swing level; that requires swing_strength bars
        # of pullback on both sides, which never happens during a fast,
        # one-directional move. Without one it falls back to "the lowest/
        # highest recent bar", which during a trend is essentially the
        # PREVIOUS bar's own extreme — trivially "broken" again on the very
        # next bar if price keeps extending. The real bot never hits this
        # because position_open=True stops it looking again after one
        # trade/day; this manager's "keep scanning" design is what exposes
        # it. Gate: a same-direction signal only counts as a genuinely new
        # setup if it's extended at least one full stop's worth beyond the
        # last one actually assigned — otherwise it's almost certainly the
        # same move re-triggering off that rolling reference, not a fresh
        # structural break, so skip it and let the next bar re-check.
        self._last_signal: Optional[Signal] = None

    def _idle_account(self) -> Optional[VirtualAccount]:
        """Per explicit user request: 0-balance accounts (never yet traded)
        get first priority, then whichever idle account has the highest
        balance, then whichever has the lowest (most negative) balance last.

        CONFIRMED LIVE BUG this replaced: a plain "highest balance wins"
        scan starves every account still sitting at its untouched $0
        starting balance, because $0 always loses to any account that has
        ever posted a single winning trade (however small). In practice
        only ~6 of 10 accounts ever traded — the early winners kept
        re-winning every new setup — and the untouched accounts only got a
        look-in once every winner had gone net-negative and dropped below
        $0. Putting $0 accounts at the very top of the priority order gives
        every account its first trade before any account gets a second one,
        so all 10 participate; once every account has taken at least one
        trade, priority falls back to concentrating fresh setups on the
        accounts furthest along (highest balance), with accounts that have
        gone negative deprioritized to last (they still get their turn once
        nothing else is idle)."""
        idle = [a for a in self.accounts if not a.traded_today]
        if not idle:
            return None

        def priority(a: VirtualAccount) -> tuple[int, float]:
            if a.net_dollars == 0:
                return (2, 0.0)  # never traded yet -- top priority
            if a.net_dollars > 0:
                return (1, a.net_dollars)  # ahead -- higher balance wins
            return (0, a.net_dollars)  # behind -- least-negative wins, but always last

        return max(idle, key=priority)

    def reset_day(self) -> None:
        self.engine.reset_day()
        for a in self.accounts:
            a.reset_day()
        self._last_signal = None

    def _save_state(self, day: date) -> None:
        # net_dollars is lifetime (survives day rollovers); traded_today is
        # scoped to `date` and only meaningful when it matches. Both live in
        # one file since they're both "state a restart shouldn't lose."
        payload = {
            "date": day.isoformat(),
            "accounts": {
                a.name: {"traded_today": a.traded_today, "net_dollars": a.net_dollars}
                for a in self.accounts
            },
        }
        tmp_path = self._state_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(json.dumps(payload))
            tmp_path.replace(self._state_path)
        except OSError:
            logger.exception("Failed to persist virtual-account state — won't survive a restart right now.")
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def _restore_state_if_same_day(self, day: date) -> None:
        if not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read persisted virtual-account state — starting the session fresh.")
            return
        saved_accounts = payload.get("accounts") or {}
        same_day = payload.get("date") == day.isoformat()
        restored_traded = 0
        for account in self.accounts:
            saved = saved_accounts.get(account.name)
            if not saved:
                continue
            # net_dollars is a running lifetime total — always restore it,
            # regardless of whether today is a new calendar day. Only
            # traded_today (a daily flag) is gated on the date matching; on
            # a genuinely new day it correctly stays at the zeroed value
            # reset_day() already set.
            account.net_dollars = saved.get("net_dollars", 0.0)
            if same_day and saved.get("traded_today"):
                account.traded_today = True
                restored_traded += 1
        if restored_traded:
            logger.info(
                "Restored today's virtual-account state after restart: %d/%d account(s) already traded today.",
                restored_traded, len(self.accounts),
            )

    def on_bar(self, bar: Bar) -> Optional[tuple[VirtualAccount, Signal]]:
        """Feed one confirmed bar. Returns (account, signal) if a new
        distinct setup was just assigned to a fresh virtual account."""
        day = bar.timestamp.date()
        if self._current_day != day:
            is_process_startup = self._is_first_day_check
            self._is_first_day_check = False
            logger.info("New session: %s — resetting %d virtual accounts.", day, len(self.accounts))
            self.reset_day()
            if is_process_startup:
                self._restore_state_if_same_day(day)
            self._current_day = day
            self._save_state(day)

        if self._idle_account() is None:
            return None  # every account already has its one trade for today

        # The engine's own position_open / max_trades_per_day / consecutive-
        # loss / rate-limit gates model a single shared account waiting for
        # its one trade to resolve before looking for another. Practice mode
        # instead wants the engine to keep scanning for the *next* distinct
        # setup regardless of whether earlier ones are still open elsewhere
        # (each goes to a different account) — per-account "one trade a day"
        # is enforced below instead.
        self.engine.position_open = False
        self.engine.trades_today = 0
        self.engine.consecutive_losses = 0
        self.engine.rate_limited = False

        signal = self.engine.on_bar(bar)
        if signal is None:
            return None

        if self._last_signal is not None and signal.direction == self._last_signal.direction:
            extension = abs(signal.entry_price - self._last_signal.entry_price)
            if extension < self.cfg.risk.stop_points:
                # Same direction, barely moved since the last setup we
                # actually assigned — almost certainly the same continuing
                # move re-triggering off _nearest_structure's rolling-extreme
                # fallback, not a fresh structural break. Not a new setup;
                # let the next bar re-check instead of handing this out.
                logger.info(
                    "Skipping signal %.2f pts from last assigned entry %.2f (< %.2f stop_points) "
                    "— same move, not a genuinely new setup.",
                    extension, self._last_signal.entry_price, self.cfg.risk.stop_points,
                )
                return None

        account = self._idle_account()
        if account is None:
            return None
        account.pending_signal = signal
        account.traded_today = True
        self._last_signal = signal
        if self._current_day is not None:
            self._save_state(self._current_day)
        logger.info(
            "%s -> %s %s @ %.2f stop=%.2f target=%.2f grade=%s | %s",
            account.name, signal.phase.value, signal.direction.value,
            signal.entry_price, signal.stop_price, signal.target_price,
            signal.grade.value, signal.reason,
        )
        return account, signal

    def check_exits(self, bar: Bar) -> list[TradeResult]:
        """Walk every account with an open virtual position against this new
        bar; resolve (log + clear) any whose stop or target was touched."""
        resolved: list[TradeResult] = []
        for account in self.accounts:
            signal = account.pending_signal
            if signal is None or bar.timestamp <= signal.timestamp:
                continue
            if signal.direction == Direction.LONG:
                hit_stop = bar.low <= signal.stop_price
                hit_target = bar.high >= signal.target_price
            else:
                hit_stop = bar.high >= signal.stop_price
                hit_target = bar.low <= signal.target_price
            if not hit_stop and not hit_target:
                continue
            # Conservative: assume stop hit first when both are touched in the same bar.
            exit_price = signal.stop_price if hit_stop else signal.target_price
            resolved.append(self._resolve_trade(account, exit_price, bar.timestamp))
        return resolved

    def _resolve_trade(self, account: VirtualAccount, exit_price: float, exit_timestamp: datetime) -> TradeResult:
        signal = account.pending_signal
        pnl_points = (
            exit_price - signal.entry_price if signal.direction == Direction.LONG
            else signal.entry_price - exit_price
        )
        win = pnl_points > 0
        result = TradeResult(
            signal=signal, exit_price=exit_price, exit_timestamp=exit_timestamp,
            win=win, pnl_points=pnl_points, qty=self.cfg.risk.contracts_per_trade,
        )
        chart_path = None
        if self.chart_renderer is not None:
            try:
                chart_path = self.chart_renderer(account, result)
            except Exception:
                logger.exception("Chart rendering failed for %s — trade still logged without a chart.", account.name)
        self.trade_logger.log_trade(result, account_name=account.name, chart_path=chart_path)
        pnl_dollars = pnl_points * self.dollar_per_point * self.cfg.risk.contracts_per_trade
        account.net_dollars += pnl_dollars
        logger.info(
            "%s trade closed: %s pnl=%.2f pts ($%.2f) win=%s net=$%.2f",
            account.name, signal.direction.value, pnl_points, pnl_dollars, win, account.net_dollars,
        )
        account.pending_signal = None
        if self._current_day is not None:
            self._save_state(self._current_day)
        return result
