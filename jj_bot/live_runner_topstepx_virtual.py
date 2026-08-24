"""Live "practice mode" loop: streams real TopstepX market data and drives
N abstract virtual accounts via VirtualAccountManager — this process NEVER
calls place_order()/place_bracket_order() and never resolves or touches any
real TopStep account, so it's safe to run continuously alongside
scripts/run_live.py (live_runner_topstepx.py) on the same machine.

Structurally mirrors live_runner_topstepx.py's market-data plumbing
(reconnect-with-backoff, periodic re-authentication, a single-instance
lock) since this is meant to run unattended for days at a time, same as the
real bot — but strips out everything order/position/account-related, since
there is nothing to place, poll, or hot-reload here.

Still requires TOPSTEPX_ALLOW_LIVE=true (same as the real bot) because
TopstepXClient's constructor gates on it — that flag is TopstepX's only
"I mean it" switch and this process shares the same authenticated session,
even though it never calls an order-placing method.
"""
from __future__ import annotations

import os
import sys
import threading
import time as time_module
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .bar_aggregator import BarAggregator
from .models import TradeResult
from .topstepx_client import TopstepXClient, TopstepXMarketDataStream
from .trade_chart import render_trade_chart
from .virtual_accounts import VirtualAccount, VirtualAccountManager
from .logging_setup import setup_logging

logger = setup_logging()

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VIRTUAL_CHART_DIR = REPO_ROOT / "trade_charts_virtual"


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class TopstepXVirtualPracticeRunner:
    # Separate lock file from the real bot's topstepx_live.lock (see
    # live_runner_topstepx.py) — the two are meant to run at the same time,
    # this only guards against two copies of THIS process stacking
    # duplicate trades onto the same 10 virtual accounts.
    _LOCK_PATH = Path("topstepx_virtual_practice.lock")

    def __init__(self, cfg: AppConfig, num_accounts: Optional[int] = None):
        self.cfg = cfg
        self.client = TopstepXClient(cfg.topstepx)
        self.aggregator = BarAggregator(tz_name=cfg.strategy.timezone)
        self.manager = VirtualAccountManager(cfg, num_accounts=num_accounts, chart_renderer=self._render_chart)
        self._got_first_tick = False
        self._shutting_down = threading.Event()
        # Same rolling-history depth as live_runner_topstepx.py's
        # _bar_history — enough to render a chart around any one trade
        # window without holding unbounded memory over a multi-day run.
        self._bar_history: deque = deque(maxlen=240)
        self._holds_lock = False

    # ---- single-instance lock (same pattern as live_runner_topstepx.py) ----

    def _acquire_single_instance_lock(self) -> None:
        if self._LOCK_PATH.exists():
            try:
                old_pid = int(self._LOCK_PATH.read_text().strip())
            except (ValueError, OSError):
                old_pid = None
            if old_pid is not None and _pid_is_running(old_pid):
                logger.error(
                    "Another virtual-practice instance is already running (PID %d, lock file %s). "
                    "Refusing to start a second instance — it would double-assign trades to the "
                    "same virtual accounts.",
                    old_pid, self._LOCK_PATH,
                )
                sys.exit(1)
            logger.warning(
                "Stale lock file found (PID %s no longer running) — removing and continuing.",
                old_pid,
            )
            try:
                self._LOCK_PATH.unlink()
            except OSError:
                logger.exception(
                    "Could not remove stale lock file %s — continuing anyway since the prior "
                    "owner (PID %s) is confirmed not running.",
                    self._LOCK_PATH, old_pid,
                )
        try:
            self._LOCK_PATH.write_text(str(os.getpid()))
            self._holds_lock = True
        except OSError:
            logger.exception(
                "Could not write lock file %s — single-instance protection is NOT active for this run.",
                self._LOCK_PATH,
            )

    def _release_single_instance_lock(self) -> None:
        if not self._holds_lock:
            return
        try:
            if self._LOCK_PATH.exists() and self._LOCK_PATH.read_text().strip() == str(os.getpid()):
                self._LOCK_PATH.unlink()
        except OSError:
            logger.exception("Failed to remove lock file on shutdown — remove %s manually before next start.", self._LOCK_PATH)
        self._holds_lock = False

    # ---- lifecycle ----------------------------------------------------

    def start(self) -> None:
        self._acquire_single_instance_lock()
        try:
            self._start_inner()
        finally:
            self._release_single_instance_lock()

    def _start_inner(self) -> None:
        logger.info("Authenticating with TopstepX for market data only — no order will ever be placed by this process.")
        self.client.authenticate()

        contract = self.client.find_front_month_contract(self.cfg.instrument.symbol)
        logger.info(
            "Watching %s. Simulating %d virtual practice account(s): %s",
            contract.name, len(self.manager.accounts), [a.name for a in self.manager.accounts],
        )

        threading.Thread(target=self._reauthenticate_loop, daemon=True).start()

        logger.info("Streaming live quotes (practice mode). Ctrl+C to stop.")
        try:
            self._run_market_stream_with_reconnect(contract)
        except KeyboardInterrupt:
            logger.info("Stopping.")
            self._shutting_down.set()

    # Same interval as live_runner_topstepx.py — see that file for why.
    REAUTH_INTERVAL_SECONDS = 6 * 60 * 60

    def _reauthenticate_loop(self) -> None:
        while not self._shutting_down.wait(self.REAUTH_INTERVAL_SECONDS):
            try:
                self.client.authenticate()
                logger.info("Re-authenticated with TopstepX (scheduled refresh) — token renewed.")
            except Exception:
                logger.exception(
                    "Scheduled re-authentication failed — continuing with the current token until "
                    "the next attempt."
                )

    def _run_market_stream_with_reconnect(self, contract) -> None:
        backoff = 5
        while not self._shutting_down.is_set():
            try:
                stream = TopstepXMarketDataStream(token=self.client.token, on_bar=None)
                stream.on_quote = lambda price: self._on_tick(price, contract)
                stream.connect()
                stream.subscribe_quotes(contract.id)
                backoff = 5  # reset after a successful connect
                stream.run_forever()
            except Exception:
                logger.exception("Market data stream setup failed.")
            if self._shutting_down.is_set():
                return
            logger.warning("Reconnecting to market data hub in %ds...", backoff)
            time_module.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _on_tick(self, price: float, contract) -> None:
        if not self._got_first_tick:
            logger.info("First live tick received: %.2f — feed is alive, waiting on first closed bar.", price)
            self._got_first_tick = True
        closed_bar = self.aggregator.add_tick(price, datetime.now().timestamp() * 1000)
        if closed_bar is not None:
            self._on_bar(closed_bar)

    def _on_bar(self, bar) -> None:
        self._bar_history.append(bar)
        logger.info("Bar %s O:%.2f H:%.2f L:%.2f C:%.2f", bar.timestamp.strftime("%H:%M"), bar.open, bar.high, bar.low, bar.close)

        # Check open virtual positions against this bar before scanning for
        # a new entry, so a bar can't both open and close the same trade.
        self.manager.check_exits(bar)
        self.manager.on_bar(bar)

    def _render_chart(self, account: VirtualAccount, trade: TradeResult) -> Optional[str]:
        chart_path = render_trade_chart(
            bars=list(self._bar_history),
            signal=trade.signal,
            exit_price=trade.exit_price,
            exit_timestamp=trade.exit_timestamp,
            win=trade.win,
            account_name=account.name,
            stop_price=trade.signal.stop_price,
            target_price=trade.signal.target_price,
            out_dir=DEFAULT_VIRTUAL_CHART_DIR,
        )
        return str(chart_path) if chart_path else None
