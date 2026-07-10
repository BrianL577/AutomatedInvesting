"""Live paper-trading loop: wires the strategy engine to one or more
Tradovate accounts. Streams live 1-minute bars, evaluates the strategy on
each closed bar, and fans out a bracket (market entry + OCO stop/target)
order to every configured account when a signal fires.

Signal detection is market-wide (one shared strategy engine), but each
account gets its own daily $ rate-limit tracking and pending-trade state, so
one account hitting its profit/loss cap doesn't stop another from trading.

Position lifecycle (win/loss/flat) is reconciled per account by polling
/position/list, since fills happen asynchronously on the broker/exchange.
"""
from __future__ import annotations

import threading
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

from .bar_aggregator import BarAggregator
from .config import AppConfig
from .models import Direction, Signal, TradeResult
from .strategy import StrategyEngine
from .tradovate_client import Account, TradovateClient, TradovateMarketDataStream
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


class LiveRunner:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.client = TradovateClient(cfg.tradovate)
        self.engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk, instrument_cfg=cfg.instrument)
        self.aggregator = BarAggregator(tz_name=cfg.strategy.timezone)
        self.dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
        self.trade_logger = TradeLogger(dollar_per_point=self.dollar_per_point, source="live_paper")
        self._current_day = None
        self._account_states: dict[str, _AccountState] = {}

    def start(self) -> None:
        logger.info("Authenticating with Tradovate (%s)...", self.cfg.tradovate.env)
        self.client.authenticate()
        accounts = self.client.load_accounts()
        self._account_states = {a.name: _AccountState(account=a) for a in accounts}
        logger.info("Trading %d account(s): %s", len(accounts), [a.name for a in accounts])

        contract = self.client.find_front_month_contract(self.cfg.instrument.symbol)
        logger.info("Trading contract: %s", contract.name)

        stream = TradovateMarketDataStream(
            md_access_token=self.client.md_access_token,
            on_bar=lambda bar: self._on_bar(bar, contract),
        )
        stream.connect()
        stream.subscribe_quote(contract.name)

        threading.Thread(target=self._poll_positions, daemon=True).start()

        logger.info("Streaming live bars. Ctrl+C to stop.")
        try:
            stream.run_forever(self.aggregator)
        except KeyboardInterrupt:
            logger.info("Stopping.")
        finally:
            stream.stop()

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

        # Global cadence (max trades/day, consecutive losses, daily $ cap)
        # tracks the shared signal, not any one account's fill.
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
            resp = requests.get(
                f"{self.client.rest_base}/position/list",
                headers=self.client._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            positions = resp.json()
            open_qty = sum(p.get("netPos", 0) for p in positions if p.get("accountId") == state.account.id)
            if open_qty != 0:
                return

            logger.info("Position flat on %s — trade closed. Fetching fill history...", state.account.name)
            exit_price = self._infer_last_exit_price(state.account.id)
            signal = state.pending_signal
            if signal is not None and exit_price is not None:
                pnl_points = (
                    exit_price - signal.entry_price if signal.direction == Direction.LONG
                    else signal.entry_price - exit_price
                )
                win = pnl_points > 0
                result = TradeResult(
                    signal=signal, exit_price=exit_price, exit_timestamp=datetime.now(),
                    win=win, pnl_points=pnl_points,
                )
                self.trade_logger.log_trade(result, account_name=state.account.name)
                self.engine.record_trade_result(win, pnl_points=pnl_points)

                pnl_dollars = pnl_points * self.dollar_per_point
                state.day_pnl_dollars += pnl_dollars
                if state.day_pnl_dollars >= self.cfg.risk.daily_profit_cap:
                    state.rate_limited = True
                    logger.info("Account %s hit daily profit cap ($%.2f) — done for the day.", state.account.name, state.day_pnl_dollars)
                elif state.day_pnl_dollars <= -self.cfg.risk.daily_loss_cap:
                    state.rate_limited = True
                    logger.info("Account %s hit daily loss cap ($%.2f) — done for the day.", state.account.name, state.day_pnl_dollars)

                logger.info(
                    "Trade closed on %s: %s pnl=%.2f pts ($%.2f) win=%s day_pnl=$%.2f",
                    state.account.name, signal.direction.value, pnl_points, pnl_dollars, win, state.day_pnl_dollars,
                )
            else:
                logger.warning(
                    "Account %s flat but could not resolve exit price/signal; defaulting to loss for safety.",
                    state.account.name,
                )
                self.engine.record_trade_result(False, pnl_points=-self.cfg.risk.stop_points)

            state.pending_entry_price = None
            state.pending_signal = None
        except Exception:
            logger.exception("Position poll failed for account %s", state.account.name)

    def _infer_last_exit_price(self, account_id: int) -> float | None:
        try:
            resp = requests.get(
                f"{self.client.rest_base}/fill/list",
                headers=self.client._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            fills = [f for f in resp.json() if f.get("accountId") == account_id]
            if not fills:
                return None
            fills.sort(key=lambda f: f.get("timestamp", ""))
            return fills[-1].get("price")
        except Exception:
            logger.exception("Could not fetch fill history to resolve exit price.")
            return None
