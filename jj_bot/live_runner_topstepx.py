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
from .topstepx_client import REST_BASE, Account, TopstepXClient, TopstepXMarketDataStream
from .trade_logger import TradeLogger
from .logging_setup import setup_logging

logger = setup_logging()


@dataclass
class _AccountState:
    account: Account
    pending_entry_price: Optional[float] = None
    pending_signal: Optional[Signal] = None
    day_pnl_dollars: float = 0.0
    rate_limited: bool = False

    def reset_day(self) -> None:
        self.pending_entry_price = None
        self.pending_signal = None
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

    def start(self) -> None:
        logger.info("Authenticating with TopstepX...")
        self.client.authenticate()
        accounts = self.client.load_accounts()
        self._account_states = {a.name: _AccountState(account=a) for a in accounts}
        logger.info("Trading %d account(s): %s", len(accounts), [a.name for a in accounts])

        contract = self.client.find_front_month_contract(self.cfg.instrument.symbol)
        logger.info("Trading contract: %s", contract.name)

        stream = TopstepXMarketDataStream(
            token=self.client.token,
            on_bar=None,
        )
        # Wire quote ticks into the bar aggregator directly, same as the
        # Tradovate stream does via BarAggregator.add_tick.
        stream.on_quote = lambda price: self._on_tick(price, contract)
        stream.connect()
        stream.subscribe_quotes(contract.id)

        threading.Thread(target=self._poll_positions, daemon=True).start()

        logger.info("Streaming live quotes. Ctrl+C to stop.")
        try:
            stream.run_forever()
        except KeyboardInterrupt:
            logger.info("Stopping.")
        finally:
            stream.stop()

    def _on_tick(self, price: float, contract) -> None:
        closed_bar = self.aggregator.add_tick(price, datetime.now().timestamp())
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
                any_order_placed = True
            except Exception:
                logger.exception("Order placement failed on account %s", state.account.name)

        if any_order_placed:
            self.engine.position_open = True

    def _poll_positions(self) -> None:
        """Every 15s, check each account for a flattened position (stop/target
        hit) and feed the result back into that account's rate limiter and
        the shared engine's daily counters."""
        while True:
            time_module.sleep(15)
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

            logger.info("Position flat on %s — trade closed. Fetching trade history...", state.account.name)
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
        prices) — sums the most recent round-trip's trade legs."""
        try:
            resp = requests.post(
                f"{REST_BASE}/Trade/search",
                json={"accountId": account_id},
                headers=self.client._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            trades = [t for t in data.get("trades", []) if t.get("accountId") == account_id and t.get("profitAndLoss") is not None]
            if not trades:
                return None
            trades.sort(key=lambda t: t.get("creationTimestamp", ""))
            return float(trades[-1]["profitAndLoss"])
        except Exception:
            logger.exception("Could not fetch trade history to resolve realized P&L.")
            return None
