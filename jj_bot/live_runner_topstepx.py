"""Live trading loop: wires the strategy engine to one or more TopStep
accounts via TopstepX (TopStep's own platform — see topstepx_client.py).
Streams live quotes, aggregates them into 1-minute bars, evaluates the
strategy on each closed bar, and fans out a bracket (market entry + OCO
stop/target) order to every configured account when a signal fires.

No demo/sandbox environment exists here — every order is real money the
moment this runs, gated by TOPSTEPX_ALLOW_LIVE (see topstepx_client.py).

Structurally mirrors live_runner.py (the Tradovate path); see that file's
docstring for the account-state/rate-limit design this shares.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time as time_module
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from .bar_aggregator import BarAggregator
from .config import AppConfig, fetch_saved_account_names
from .models import Direction, Signal, TradeResult
from .strategy import StrategyEngine
from .time_utils import to_et
from .topstepx_client import REST_BASE, Account, TopstepXClient, TopstepXMarketDataStream, TopstepXUserDataStream
from .trade_chart import render_trade_chart
from .trade_logger import TradeLogger
from .logging_setup import setup_logging

logger = setup_logging()


def _pid_is_running(pid: int) -> bool:
    """Cross-platform liveness check for a PID. os.kill(pid, 0) works on
    POSIX but not the same way on Windows (this bot's actual deployment
    target — see TOPSTEPX_ALLOW_LIVE / README), so use OpenProcess there
    instead."""
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


@dataclass
class _AccountState:
    account: Account
    pending_entry_price: Optional[float] = None
    pending_signal: Optional[Signal] = None
    # CONFIRMED BY A REAL LIVE TRADE: TopstepX does NOT auto-cancel the
    # sibling stop/target order when the other fills (linkedOrderId is not
    # a working OCO — see topstepx_client.py). These IDs are tracked so
    # _check_account_flat can manually cancel whichever leg is still open
    # the moment a fill is detected — this cost a real $905 loss before
    # this tracking/cleanup existed.
    stop_order_id: Optional[int] = None
    target_order_id: Optional[int] = None
    day_pnl_dollars: float = 0.0
    rate_limited: bool = False
    # CONFIRMED BY A REAL LIVE TRADE: get_recent_trade_pnl with no after_iso
    # sums the account's ENTIRE trade history, not just the current trade.
    # A real $1,530 winner got logged as $1,365 because it silently netted
    # in two unrelated connectivity-test trades (-$105, -$60) from the day
    # before. Recording the entry's placement time and passing it as
    # after_iso scopes the P&L query to just this trade.
    entry_placed_at: Optional[str] = None
    # The ACTUAL stop/target prices sent to the broker for the open trade —
    # may differ from signal.stop_price/target_price when eval scale-down
    # (RiskConfig.eval_scale_down_enabled) shrank them. Used for accurate
    # display-only exit-price reporting; the real $ P&L always comes from
    # the exchange's own trade history regardless of this.
    placed_stop_price: Optional[float] = None
    placed_target_price: Optional[float] = None
    # CONFIRMED LIVE (real account): the 3s poll thread and the real-time
    # push-callback thread both call _check_account_flat() independently —
    # the code assumed this was safe because order CANCELLATION is
    # idempotent, but the trade-logging/day_pnl-accumulation part below it
    # is not. Both threads can pass the "position is flat" check before
    # either clears pending_signal, so both log the same real trade and
    # double-count its P&L — this is what inflated a real single $1,490
    # win into two identical $1,490 dashboard rows and a fake $2,980 "day"
    # that wrongly tripped the eval simulator's target-raising rule. This
    # lock makes the whole check-log-clear sequence atomic across threads.
    flat_check_lock: "threading.Lock" = field(default_factory=threading.Lock)

    def reset_day(self) -> None:
        self.pending_entry_price = None
        self.pending_signal = None
        self.stop_order_id = None
        self.target_order_id = None
        self.day_pnl_dollars = 0.0
        self.rate_limited = False
        self.entry_placed_at = None
        self.placed_stop_price = None
        self.placed_target_price = None


class TopstepXLiveRunner:
    # CONFIRMED LIVE (2026-08-11 through 2026-08-18): two separate
    # `run_live.py` processes ran concurrently, unnoticed, for a full week —
    # started from different launch paths (a venv python and the system
    # python), each independently authenticated and placed brackets on
    # every signal against the same real account. There was nothing in this
    # class stopping that. This lock makes a second instance refuse to
    # start instead of silently trading alongside the first one — the same
    # pattern that protects a NinjaTrader-style desktop bot from a leftover
    # terminal + a Task Scheduler job both being alive at once.
    _LOCK_PATH = Path("topstepx_live.lock")

    # CONFIRMED: this codebase already hit this exact bug class once before
    # (the old NinjaTrader runner's docstring records it) — daily risk
    # counters (trades taken, consecutive losses, per-account rate-limit
    # flags) lived only in memory, so a process restart mid-day silently
    # reset every daily limit back to zero, letting the bot re-enter after
    # it should already have stopped for the day. That fix was never
    # ported to this TopstepX runner. Persisted here, keyed by calendar
    # date, and restored on startup only if the file's date matches
    # today's — a genuinely new day still starts from zero as normal.
    _DAILY_STATE_PATH = Path("daily_risk_state.json")

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.client = TopstepXClient(cfg.topstepx)
        self.engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk, instrument_cfg=cfg.instrument)
        self.aggregator = BarAggregator(tz_name=cfg.strategy.timezone)
        self.dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
        self.trade_logger = TradeLogger(dollar_per_point=self.dollar_per_point, source="live_topstepx")
        self._current_day = None
        self._account_states: dict[str, _AccountState] = {}
        self._got_first_tick = False
        self._shutting_down = threading.Event()
        # Rolling context for trade screenshots (see trade_chart.py) — 240
        # 1-min bars (~4 hours) is comfortably more than one trade window
        # (entry to exit is normally minutes, not hours) plus padding before
        # entry, without holding unbounded memory over a multi-day run.
        self._bar_history: deque = deque(maxlen=240)
        self._holds_lock = False

    def _acquire_single_instance_lock(self) -> None:
        if self._LOCK_PATH.exists():
            try:
                old_pid = int(self._LOCK_PATH.read_text().strip())
            except (ValueError, OSError):
                old_pid = None
            if old_pid is not None and _pid_is_running(old_pid):
                logger.error(
                    "Another instance is already running (PID %d, lock file %s). Refusing to "
                    "start a second instance — check Task Manager / Task Scheduler before "
                    "forcing this. Running two copies means BOTH can place real orders on the "
                    "same account for the same signal.",
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
                    "Could not remove stale lock file %s — you may need to delete it manually. "
                    "Continuing anyway since the prior owner (PID %s) is confirmed not running.",
                    self._LOCK_PATH, old_pid,
                )
        try:
            self._LOCK_PATH.write_text(str(os.getpid()))
            self._holds_lock = True
        except OSError:
            logger.exception(
                "Could not write lock file %s — single-instance protection is NOT active for "
                "this run. Fix file permissions before relying on this safeguard.",
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

    # ---- daily state persistence (survives a mid-day process restart) ----

    def _save_daily_state(self, day) -> None:
        payload = {
            "date": day.isoformat(),
            "trades_today": self.engine.trades_today,
            "consecutive_losses": self.engine.consecutive_losses,
            "rate_limited": self.engine.rate_limited,
            "accounts": {
                name: {"day_pnl_dollars": state.day_pnl_dollars, "rate_limited": state.rate_limited}
                for name, state in self._account_states.items()
            },
        }
        # Atomic write (temp file + replace) so a crash or antivirus scan
        # mid-write can never leave a half-written, unparseable state file.
        tmp_path = self._DAILY_STATE_PATH.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(json.dumps(payload))
            tmp_path.replace(self._DAILY_STATE_PATH)
        except OSError:
            logger.exception("Failed to persist daily risk state — limits won't survive a restart right now.")
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

    def _restore_daily_state_if_same_day(self, day) -> None:
        if not self._DAILY_STATE_PATH.exists():
            return
        try:
            payload = json.loads(self._DAILY_STATE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to read persisted daily risk state — starting the day fresh.")
            return
        if payload.get("date") != day.isoformat():
            return  # genuinely a new trading day — zeroed state from reset_day() is correct
        self.engine.trades_today = payload.get("trades_today", 0)
        self.engine.consecutive_losses = payload.get("consecutive_losses", 0)
        self.engine.rate_limited = payload.get("rate_limited", False)
        for name, saved in (payload.get("accounts") or {}).items():
            state = self._account_states.get(name)
            if state is not None:
                state.day_pnl_dollars = saved.get("day_pnl_dollars", 0.0)
                state.rate_limited = saved.get("rate_limited", False)
        logger.info(
            "Restored today's risk state after restart: trades_today=%d consecutive_losses=%d rate_limited=%s",
            self.engine.trades_today, self.engine.consecutive_losses, self.engine.rate_limited,
        )

    def start(self) -> None:
        self._acquire_single_instance_lock()
        try:
            self._start_inner()
        finally:
            self._release_single_instance_lock()

    def _start_inner(self) -> None:
        logger.info("Authenticating with TopstepX...")
        self.client.authenticate()
        accounts = self.client.load_accounts()
        self._account_states = {a.name: _AccountState(account=a) for a in accounts}
        logger.info("Trading %d account(s): %s", len(accounts), [a.name for a in accounts])

        contract = self.client.find_front_month_contract(self.cfg.instrument.symbol)
        logger.info("Trading contract: %s", contract.name)

        # CONFIRMED LIVE (real account, ~3 days uptime): self.client.token
        # was never refreshed anywhere — the user-hub stream was
        # reconnecting every ~0.3-0.4s with an immediate server-side close,
        # consistent with an expired JWT being reused on every attempt.
        # Every REST call (orders, position polling, P&L) shares this same
        # token via _headers(), so a stale token silently breaks far more
        # than just the streams. Re-authenticate on a fixed interval well
        # under any plausible token TTL so a multi-day run never runs on a
        # dead token again.
        threading.Thread(target=self._reauthenticate_loop, daemon=True).start()

        # Real-time order-fill notifications, one connection per account —
        # reacts to a fill (and cancels the sibling bracket order)
        # immediately instead of waiting for the next 3s poll tick. Runs
        # ALONGSIDE the poll loop below, not instead of it: if a hub
        # connection drops or an event never arrives, the poll is still
        # there to catch it, just slower.
        for state in self._account_states.values():
            threading.Thread(target=self._run_user_stream_with_reconnect, args=(state,), daemon=True).start()

        threading.Thread(target=self._poll_positions, daemon=True).start()

        if self.cfg.topstepx.dashboard_managed:
            threading.Thread(target=self._account_refresh_loop, daemon=True).start()
        else:
            logger.info(
                "TOPSTEPX_ACCOUNT_NAMES is set explicitly in .env — dashboard account "
                "hot-reload is disabled; restart this process to pick up account changes."
            )

        logger.info("Streaming live quotes. Ctrl+C to stop.")
        try:
            self._run_market_stream_with_reconnect(contract)
        except KeyboardInterrupt:
            logger.info("Stopping.")
            self._shutting_down.set()

    # Deliberately well under any plausible token TTL (unconfirmed exact
    # value — TopstepX doesn't publish one) rather than trying to parse the
    # JWT's own expiry claim, so this stays correct even if that assumption
    # is wrong in either direction.
    REAUTH_INTERVAL_SECONDS = 6 * 60 * 60

    def _reauthenticate_loop(self) -> None:
        while not self._shutting_down.wait(self.REAUTH_INTERVAL_SECONDS):
            try:
                self.client.authenticate()
                logger.info("Re-authenticated with TopstepX (scheduled refresh) — token renewed.")
            except Exception:
                logger.exception(
                    "Scheduled re-authentication failed — continuing with the current token until "
                    "the next attempt; if it's actually expired, REST calls and hub reconnects will "
                    "start failing visibly."
                )

    # How often to check the dashboard's "My Accounts" page (Supabase) for a
    # newly saved account. Only runs in dashboard_managed mode (see
    # config.py). Deliberately short — the whole point is "type the new
    # account name in on the dashboard, it starts trading within seconds"
    # after a blown eval gets replaced, with zero action on the trading
    # machine itself.
    ACCOUNT_REFRESH_INTERVAL_SECONDS = 20

    def _account_refresh_loop(self) -> None:
        """Polls Supabase for account names saved on the dashboard since this
        process started, and starts trading any that aren't already running
        — no restart needed. Deliberately ADD-ONLY: an account that
        disappears from a poll (dashboard deletion, or just a transient
        Supabase hiccup — the two look identical from here) is never
        auto-removed from trading. Stopping a live account is a deliberate
        action (delete it on the dashboard AND restart the process), never
        something a flaky network request should be able to trigger on its
        own."""
        while not self._shutting_down.wait(self.ACCOUNT_REFRESH_INTERVAL_SECONDS):
            try:
                saved_names = fetch_saved_account_names()
            except Exception:
                logger.exception("Account refresh: failed to fetch saved accounts from Supabase.")
                continue

            new_names = [n for n in saved_names if n not in self._account_states]
            if not new_names:
                continue

            logger.info("Account refresh: found newly saved account(s) %s — resolving against TopstepX.", new_names)
            try:
                self.client.creds.account_names = list(self._account_states.keys()) + new_names
                accounts = self.client.load_accounts()
            except Exception:
                logger.exception(
                    "Account refresh: could not resolve %s against TopstepX (typo in the account "
                    "name, not yet active, etc.) — will retry next cycle.", new_names,
                )
                continue

            for account in accounts:
                if account.name in self._account_states:
                    continue
                state = _AccountState(account=account)
                self._account_states[account.name] = state
                threading.Thread(target=self._run_user_stream_with_reconnect, args=(state,), daemon=True).start()
                logger.info("Now trading newly added account: %s (picked up live, no restart).", account.name)

    def _run_market_stream_with_reconnect(self, contract) -> None:
        """A dropped WebSocket (sleep/wake, Wi-Fi blip, etc.) used to kill
        quote streaming permanently for the rest of the run — the process
        kept running but silently stopped producing bars forever, which is
        exactly what happened on 2026-08-03: a 16-minute gap then a
        ConnectionResetError with nothing after it. This loop reconnects
        with backoff instead of dying once."""
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

    def _run_user_stream_with_reconnect(self, state: "_AccountState") -> None:
        backoff = 5
        while not self._shutting_down.is_set():
            connected_at = time_module.monotonic()
            try:
                user_stream = TopstepXUserDataStream(
                    token=self.client.token,
                    on_order_event=lambda _evt, s=state: self._check_account_flat(s),
                )
                user_stream.connect()
                user_stream.subscribe_orders(state.account.id)
                backoff = 5
                user_stream.run_forever()
            except Exception:
                logger.exception("Order event stream failed for %s — the 3s poll is still covering it.", state.account.name)
            if self._shutting_down.is_set():
                return
            # Independent of whatever the stream class itself logged (it
            # should already say why — see topstepx_client.py), record how
            # long the connection actually lasted here too. A short-lived
            # connection repeating on a tight cadence is the signature of a
            # keepalive/idle-timeout problem even if the inner class's own
            # logging were ever wrong or silent again.
            lifetime = time_module.monotonic() - connected_at
            logger.warning(
                "Order stream for %s lasted %.1fs before disconnecting — reconnecting in %ds...",
                state.account.name, lifetime, backoff,
            )
            time_module.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _on_tick(self, price: float, contract) -> None:
        if not self._got_first_tick:
            logger.info("First live tick received: %.2f — feed is alive, waiting on first closed bar.", price)
            self._got_first_tick = True
        # NOTE: add_tick expects epoch MILLISECONDS (see bar_aggregator.py /
        # tradovate_client.py, which forwards the exchange's ms timestamp
        # directly) — datetime.now().timestamp() returns SECONDS. Passing
        # seconds here made the aggregator interpret ~1000 minutes of real
        # time as one simulated minute, so no bar ever closed for hours.
        # This was the actual root cause of "no bars logging" after several
        # minutes of a live run.
        closed_bar = self.aggregator.add_tick(price, datetime.now().timestamp() * 1000)
        if closed_bar is not None:
            self._on_bar(closed_bar, contract)

    def _on_bar(self, bar, contract) -> None:
        self._bar_history.append(bar)
        day = bar.timestamp.date()
        if self._current_day != day:
            is_process_startup = self._current_day is None
            logger.info("New trading day: %s — resetting strategy + all account state.", day)
            self.engine.reset_day()
            # list(...) snapshot: the account hot-reload loop can add a new
            # entry to self._account_states from another thread concurrently
            # with this iteration (see _account_refresh_loop) — iterating
            # the dict view directly would risk "dictionary changed size
            # during iteration".
            for state in list(self._account_states.values()):
                state.reset_day()
            # Only meaningful right at process startup — a restart mid-day
            # is exactly the case this recovers from; a genuine day
            # rollover later in the same run has nothing to restore (the
            # zeroed state above is already correct).
            if is_process_startup:
                self._restore_daily_state_if_same_day(day)
            self._current_day = day
            self._save_daily_state(day)

        logger.info(
            "Bar %s O:%.2f H:%.2f L:%.2f C:%.2f phase=%s",
            bar.timestamp.strftime("%H:%M"), bar.open, bar.high, bar.low, bar.close, self.engine.phase.value,
        )

        signal = self.engine.on_bar(bar)
        if signal is None:
            # Only meaningful during continuation/reversion (structure_debug
            # returns None otherwise), so this doesn't add noise outside the
            # actual trading windows — but during them, it answers "why
            # didn't this bar trade" directly from the log instead of
            # requiring an after-the-fact reconstruction days later.
            debug = self.engine.structure_debug()
            if debug:
                logger.info("  -> %s", debug)
            return

        logger.info(
            "SIGNAL %s %s @ %.2f stop=%.2f target=%.2f grade=%s | %s",
            signal.phase.value, signal.direction.value, signal.entry_price,
            signal.stop_price, signal.target_price, signal.grade.value, signal.reason,
        )

        action = "Buy" if signal.direction == Direction.LONG else "Sell"
        any_order_placed = False
        # list(...) snapshot — see the identical comment above in the
        # new-trading-day reset for why.
        for state in list(self._account_states.values()):
            if state.rate_limited:
                logger.info("Skipping account %s: rate limit already hit today.", state.account.name)
                continue
            if state.pending_signal is not None:
                logger.info("Skipping account %s: already in a trade.", state.account.name)
                continue
            try:
                stop_price, target_price = self._scaled_stop_target(signal, state.account)
                # Captured BEFORE placing the order (not after) so a fill
                # that happens fast can never land a hair earlier than this
                # timestamp and get excluded from the P&L query below.
                placed_at = datetime.now(timezone.utc).isoformat()
                result = self.client.place_bracket_order(
                    contract=contract,
                    action=action,
                    qty=self.cfg.risk.contracts_per_trade,
                    stop_price=stop_price,
                    target_price=target_price,
                    account=state.account,
                )
                logger.info("Order placed on %s: %s", state.account.name, result)
                state.pending_entry_price = signal.entry_price
                state.pending_signal = signal
                state.stop_order_id = result.get("stop", {}).get("orderId")
                state.target_order_id = result.get("target", {}).get("orderId")
                state.entry_placed_at = placed_at
                state.placed_stop_price = stop_price
                state.placed_target_price = target_price
                any_order_placed = True
            except Exception:
                logger.exception("Order placement failed on account %s", state.account.name)

        if any_order_placed:
            self.engine.position_open = True

    def _poll_positions(self) -> None:
        """Every 3s, check each account for a flattened position (stop/target
        hit), immediately cancel whichever sibling order is still open (see
        _AccountState docstring — this is NOT optional cleanup, it's the fix
        for a real $905 loss from a stale unprotected order), and feed the
        result back into that account's rate limiter and the shared engine's
        daily counters.

        This is the FALLBACK path. The primary path is now
        TopstepXUserDataStream (see start()), which pushes real-time fill
        notifications and triggers this same check immediately — this 3s
        poll exists in case that stream's assumptions are wrong, drops its
        connection, or a status event is missed. Both paths call
        _check_account_flat(), which now serializes via
        _AccountState.flat_check_lock so running both concurrently is safe.
        CONFIRMED LIVE: before that lock existed, both paths could pass the
        "flat" check before either cleared pending_signal, double-logging
        the same real trade — order cancellation alone being idempotent
        was not enough."""
        while True:
            time_module.sleep(3)
            # list(...) snapshot — see the identical comment in _on_bar.
            for state in list(self._account_states.values()):
                if state.pending_signal is None or state.pending_entry_price is None:
                    continue
                self._check_account_flat(state)

    def _check_account_flat(self, state: _AccountState) -> None:
        # Serializes against the other caller (poll thread vs. push
        # callback) — see flat_check_lock's docstring. Re-check right after
        # acquiring: if the OTHER caller already finished processing this
        # trade while we were waiting for the lock, pending_signal is now
        # None and there's nothing left for us to do.
        with state.flat_check_lock:
            if state.pending_signal is None:
                return
            self._check_account_flat_locked(state)

    def _check_account_flat_locked(self, state: _AccountState) -> None:
        try:
            resp = requests.post(
                f"{REST_BASE}/Position/searchOpen",
                json={"accountId": state.account.id},
                headers=self.client._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            positions = data.get("positions", [])
            open_size = sum(p.get("size", 0) for p in positions if p.get("accountId") == state.account.id)
            if open_size != 0:
                return

            logger.info("Position flat on %s — trade closed. Cancelling any leftover bracket order...", state.account.name)
            # THE FIX: cancel whichever of stop/target didn't cause this
            # fill, immediately, before it can sit live and get hit by a
            # later price move (see _AccountState / _poll_positions docs).
            self.client.cancel_sibling_orders(state.account.id, [state.stop_order_id, state.target_order_id])

            realized_pnl = self._infer_last_pnl(state.account.id, after_iso=state.entry_placed_at)
            signal = state.pending_signal
            if signal is not None and realized_pnl is not None:
                pnl_points = realized_pnl / (self.dollar_per_point * self.cfg.risk.contracts_per_trade)
                win = realized_pnl > 0
                # CONFIRMED: exit_price was previously ASSUMED to be exactly
                # the placed stop/target level, computed completely
                # independently of pnl_points (which comes from TopStep's
                # own real trade/fill data — always accurate). Whenever the
                # real fill differed even slightly from that assumed level
                # (slippage, tick rounding, fees baked into TopStep's
                # reported P&L), entry_price/exit_price/pnl_points stopped
                # being internally consistent — e.g. a displayed Entry/Exit
                # implying -25.00pts next to a displayed P&L of -19.75pts.
                # Deriving exit_price FROM the real, ground-truth pnl_points
                # instead guarantees the three displayed numbers always
                # agree with each other.
                exit_price = (
                    signal.entry_price + pnl_points if signal.direction == Direction.LONG
                    else signal.entry_price - pnl_points
                )
                # Still needed for the chart's dashed stop/target lines,
                # which should show the ACTUAL levels sent to the broker
                # (eval scale-down can shrink these) — just no longer used
                # for exit_price itself.
                target_price = state.placed_target_price if state.placed_target_price is not None else signal.target_price
                stop_price = state.placed_stop_price if state.placed_stop_price is not None else signal.stop_price
                result = TradeResult(
                    signal=signal, exit_price=exit_price, exit_timestamp=datetime.now(),
                    win=win, pnl_points=pnl_points, qty=self.cfg.risk.contracts_per_trade,
                )
                chart_path = render_trade_chart(
                    bars=list(self._bar_history),
                    signal=signal,
                    exit_price=exit_price,
                    # to_et, not the raw naive result.exit_timestamp — bars
                    # are tz-aware America/New_York, and comparing a
                    # wrong-timezone naive "now" against them can silently
                    # pick the wrong candle or filter the window empty.
                    exit_timestamp=to_et(datetime.now(timezone.utc), self.cfg.strategy.timezone),
                    win=win,
                    account_name=state.account.name,
                    stop_price=stop_price,
                    target_price=target_price,
                )
                self.trade_logger.log_trade(
                    result, account_name=state.account.name,
                    chart_path=str(chart_path) if chart_path else None,
                )
                self.engine.record_trade_result(win, pnl_points=pnl_points)

                state.day_pnl_dollars += realized_pnl
                if state.day_pnl_dollars >= self.cfg.risk.daily_profit_cap:
                    state.rate_limited = True
                    logger.info("Account %s hit daily profit cap ($%.2f) — done for the day.", state.account.name, state.day_pnl_dollars)
                elif state.day_pnl_dollars <= -self.cfg.risk.daily_loss_cap:
                    state.rate_limited = True
                    logger.info("Account %s hit daily loss cap ($%.2f) — done for the day.", state.account.name, state.day_pnl_dollars)

                logger.info(
                    "Trade closed on %s: %s pnl=$%.2f win=%s day_pnl=$%.2f",
                    state.account.name, signal.direction.value, realized_pnl, win, state.day_pnl_dollars,
                )
            else:
                logger.warning(
                    "Account %s flat but could not resolve realized P&L; defaulting to loss for safety.",
                    state.account.name,
                )
                self.engine.record_trade_result(False, pnl_points=-self.cfg.risk.stop_points)

            state.pending_entry_price = None
            state.pending_signal = None
            state.entry_placed_at = None
            state.placed_stop_price = None
            state.placed_target_price = None
            if self._current_day is not None:
                self._save_daily_state(self._current_day)
        except Exception:
            logger.exception("Position poll failed for account %s", state.account.name)

    def _infer_last_pnl(self, account_id: int, after_iso: Optional[str] = None) -> float | None:
        """TopstepX's Trade/search returns realized profitAndLoss per trade
        directly (unlike Tradovate, which requires inferring P&L from fill
        prices) — see TopstepXClient.get_recent_trade_pnl.

        after_iso MUST be passed (this account's entry_placed_at) — without
        it, get_recent_trade_pnl sums the account's ENTIRE trade history,
        not just this trade. Confirmed live: a real $1,530 winner got
        logged as $1,365 because it silently netted in two unrelated
        connectivity-test trades (-$105, -$60) from the previous day."""
        try:
            return self.client.get_recent_trade_pnl(account_id, after_iso=after_iso)
        except Exception:
            logger.exception("Could not fetch trade history to resolve realized P&L.")
            return None

    def _scaled_stop_target(self, signal: Signal, account: Account) -> tuple[float, float]:
        """Per-account eval scale-down (RiskConfig.eval_scale_down_enabled,
        off by default). Once this account's REAL live balance (queried
        fresh right now, not a cached/simulated approximation) is within one
        normal-size trade of clearing the eval's profit_target, shrinks the
        stop/target down to exactly what's needed to pass — same
        reward:risk ratio as the static trade, just smaller. Only ever
        shrinks; never returns a bigger trade than the static configured
        size. Falls back to the normal static stop/target (from `signal`,
        already built from risk_cfg.stop_points/target_points) on ANY
        uncertainty — a failed balance lookup, an already-funded/way-off
        balance, or a remaining gap too small to bother with
        (eval_scale_min_target_dollars) all resolve to "just take the
        normal trade", never to guessing a bigger or smaller size than
        intended."""
        if not self.cfg.risk.eval_scale_down_enabled:
            return signal.stop_price, signal.target_price

        try:
            accounts = self.client.get_live_balances()
        except Exception:
            logger.exception("Eval scale-down: could not fetch live balance — using normal static size.")
            return signal.stop_price, signal.target_price

        live = next((a for a in accounts if a.id == account.id), None)
        if live is None:
            logger.warning("Eval scale-down: account %s not found in balance lookup — using normal static size.", account.name)
            return signal.stop_price, signal.target_price

        dollar_per_point = self.dollar_per_point * self.cfg.risk.contracts_per_trade
        normal_target_dollars = self.cfg.risk.target_points * dollar_per_point
        normal_stop_dollars = self.cfg.risk.stop_points * dollar_per_point

        pass_line = self.cfg.topstep_eval.account_size + self.cfg.topstep_eval.profit_target
        remaining = pass_line - live.balance

        if remaining <= 0 or remaining >= normal_target_dollars:
            # Already at/past target (or an unexpectedly-funded/rebilled
            # balance), or the normal trade wouldn't even reach the gap
            # anyway — either way, no reason to shrink.
            return signal.stop_price, signal.target_price
        if remaining < self.cfg.risk.eval_scale_min_target_dollars:
            logger.info(
                "Eval scale-down: only $%.2f left to pass on %s, below the $%.2f floor — taking the normal trade instead.",
                remaining, account.name, self.cfg.risk.eval_scale_min_target_dollars,
            )
            return signal.stop_price, signal.target_price

        ratio = normal_stop_dollars / normal_target_dollars
        scaled_target_dollars = remaining
        scaled_stop_dollars = scaled_target_dollars * ratio
        scaled_target_points = scaled_target_dollars / dollar_per_point
        scaled_stop_points = scaled_stop_dollars / dollar_per_point

        tick = self.cfg.instrument.tick_size
        scaled_target_points = max(tick, round(scaled_target_points / tick) * tick)
        scaled_stop_points = max(tick, round(scaled_stop_points / tick) * tick)

        if signal.direction == Direction.LONG:
            stop_price = signal.entry_price - scaled_stop_points
            target_price = signal.entry_price + scaled_target_points
        else:
            stop_price = signal.entry_price + scaled_stop_points
            target_price = signal.entry_price - scaled_target_points

        logger.info(
            "Eval scale-down on %s: live balance $%.2f, $%.2f left to pass (of $%.2f normal trade) — "
            "shrinking to stop=%.2f target=%.2f (%.2fpts/%.2fpts, was %.2fpts/%.2fpts).",
            account.name, live.balance, remaining, normal_target_dollars,
            stop_price, target_price, scaled_stop_points, scaled_target_points,
            self.cfg.risk.stop_points, self.cfg.risk.target_points,
        )
        return stop_price, target_price
