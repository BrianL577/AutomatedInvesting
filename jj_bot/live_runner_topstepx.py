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

import threading
import time as time_module
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

from .bar_aggregator import BarAggregator
from .config import AppConfig
from .models import Direction, Signal, TradeResult
from .strategy import StrategyEngine
from .topstepx_client import REST_BASE, Account, TopstepXClient, TopstepXMarketDataStream, TopstepXUserDataStream
from .trade_logger import TradeLogger
from .logging_setup import setup_logging

logger = setup_logging()


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

    def reset_day(self) -> None:
        self.pending_entry_price = None
        self.pending_signal = None
        self.stop_order_id = None
        self.target_order_id = None
        self.day_pnl_dollars = 0.0
        self.rate_limited = False


class TopstepXLiveRunner:
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

    def start(self) -> None:
        logger.info("Authenticating with TopstepX...")
        self.client.authenticate()
        accounts = self.client.load_accounts()
        self._account_states = {a.name: _AccountState(account=a) for a in accounts}
        logger.info("Trading %d account(s): %s", len(accounts), [a.name for a in accounts])

        contract = self.client.find_front_month_contract(self.cfg.instrument.symbol)
        logger.info("Trading contract: %s", contract.name)

        # Real-time order-fill notifications, one connection per account —
        # reacts to a fill (and cancels the sibling bracket order)
        # immediately instead of waiting for the next 3s poll tick. Runs
        # ALONGSIDE the poll loop below, not instead of it: if a hub
        # connection drops or an event never arrives, the poll is still
        # there to catch it, just slower.
        for state in self._account_states.values():
            threading.Thread(target=self._run_user_stream_with_reconnect, args=(state,), daemon=True).start()

        threading.Thread(target=self._poll_positions, daemon=True).start()

        logger.info("Streaming live quotes. Ctrl+C to stop.")
        try:
            self._run_market_stream_with_reconnect(contract)
        except KeyboardInterrupt:
            logger.info("Stopping.")
            self._shutting_down.set()

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
            logger.warning("Reconnecting order stream for %s in %ds...", state.account.name, backoff)
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
        day = bar.timestamp.date()
        if self._current_day != day:
            logger.info("New trading day: %s — resetting strategy + all account state.", day)
            self.engine.reset_day()
            for state in self._account_states.values():
                state.reset_day()
            self._current_day = day

        logger.info(
            "Bar %s O:%.2f H:%.2f L:%.2f C:%.2f phase=%s",
            bar.timestamp.strftime("%H:%M"), bar.open, bar.high, bar.low, bar.close, self.engine.phase.value,
        )

        signal = self.engine.on_bar(bar)
        if signal is None:
            return

        logger.info(
            "SIGNAL %s %s @ %.2f stop=%.2f target=%.2f grade=%s | %s",
            signal.phase.value, signal.direction.value, signal.entry_price,
            signal.stop_price, signal.target_price, signal.grade.value, signal.reason,
        )

        action = "Buy" if signal.direction == Direction.LONG else "Sell"
        any_order_placed = False
        for state in self._account_states.values():
            if state.rate_limited:
                logger.info("Skipping account %s: rate limit already hit today.", state.account.name)
                continue
            if state.pending_signal is not None:
                logger.info("Skipping account %s: already in a trade.", state.account.name)
                continue
            try:
                result = self.client.place_bracket_order(
                    contract=contract,
                    action=action,
                    qty=self.cfg.risk.contracts_per_trade,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                    account=state.account,
                )
                logger.info("Order placed on %s: %s", state.account.name, result)
                state.pending_entry_price = signal.entry_price
                state.pending_signal = signal
                state.stop_order_id = result.get("stop", {}).get("orderId")
                state.target_order_id = result.get("target", {}).get("orderId")
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
        _check_account_flat(), which is idempotent (cancelling an
        already-cancelled/filled order is a no-op), so running both
        concurrently is safe, not redundant-in-a-bad-way."""
        while True:
            time_module.sleep(3)
            for state in self._account_states.values():
                if state.pending_signal is None or state.pending_entry_price is None:
                    continue
                self._check_account_flat(state)

    def _check_account_flat(self, state: _AccountState) -> None:
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

            realized_pnl = self._infer_last_pnl(state.account.id)
            signal = state.pending_signal
            if signal is not None and realized_pnl is not None:
                pnl_points = realized_pnl / (self.dollar_per_point * self.cfg.risk.contracts_per_trade)
                win = realized_pnl > 0
                exit_price = signal.target_price if win else signal.stop_price
                result = TradeResult(
                    signal=signal, exit_price=exit_price, exit_timestamp=datetime.now(),
                    win=win, pnl_points=pnl_points, qty=self.cfg.risk.contracts_per_trade,
                )
                self.trade_logger.log_trade(result, account_name=state.account.name)
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
        except Exception:
            logger.exception("Position poll failed for account %s", state.account.name)

    def _infer_last_pnl(self, account_id: int) -> float | None:
        """TopstepX's Trade/search returns realized profitAndLoss per trade
        directly (unlike Tradovate, which requires inferring P&L from fill
        prices) — see TopstepXClient.get_recent_trade_pnl."""
        try:
            return self.client.get_recent_trade_pnl(account_id)
        except Exception:
            logger.exception("Could not fetch trade history to resolve realized P&L.")
            return None
