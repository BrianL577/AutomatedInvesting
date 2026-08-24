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
then stop" gates), and hands each newly detected, distinct setup to the
next still-idle virtual account. One trade per account per day, same rule
as real trading. If the session produces fewer setups than accounts, the
remaining accounts simply don't trade — no synthetic trades are invented to
fill the count.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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

    def _idle_account(self) -> Optional[VirtualAccount]:
        for a in self.accounts:
            if not a.traded_today:
                return a
        return None

    def reset_day(self) -> None:
        self.engine.reset_day()
        for a in self.accounts:
            a.reset_day()

    def on_bar(self, bar: Bar) -> Optional[tuple[VirtualAccount, Signal]]:
        """Feed one confirmed bar. Returns (account, signal) if a new
        distinct setup was just assigned to a fresh virtual account."""
        day = bar.timestamp.date()
        if self._current_day != day:
            logger.info("New session: %s — resetting %d virtual accounts.", day, len(self.accounts))
            self.reset_day()
            self._current_day = day

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

        account = self._idle_account()
        if account is None:
            return None
        account.pending_signal = signal
        account.traded_today = True
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
        logger.info(
            "%s trade closed: %s pnl=%.2f pts ($%.2f) win=%s",
            account.name, signal.direction.value, pnl_points,
            pnl_points * self.dollar_per_point * self.cfg.risk.contracts_per_trade, win,
        )
        account.pending_signal = None
        return result
