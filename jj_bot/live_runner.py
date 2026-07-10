"""Live paper-trading loop: wires the strategy engine to Tradovate's demo
account. Streams live 1-minute bars, evaluates the strategy on each closed
bar, and places bracket (market entry + OCO stop/target) orders when a
signal fires.

Position lifecycle (win/loss/flat) is reconciled by polling
/position/list, since fills happen asynchronously on the broker/exchange.
"""
from __future__ import annotations

import threading
import time as time_module
from datetime import datetime

import requests

from .bar_aggregator import BarAggregator
from .config import AppConfig
from .models import Direction
from .strategy import StrategyEngine
from .tradovate_client import TradovateClient, TradovateMarketDataStream
from .logging_setup import setup_logging

logger = setup_logging()


class LiveRunner:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.client = TradovateClient(cfg.tradovate)
        self.engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)
        self.aggregator = BarAggregator(tz_name=cfg.strategy.timezone)
        self._current_day = None
        self._pending_entry_price: float | None = None

    def start(self) -> None:
        logger.info("Authenticating with Tradovate (%s)...", self.cfg.tradovate.env)
        self.client.authenticate()
        self.client.load_account()
        logger.info("Using account %s (id=%s)", self.client.account_spec, self.client.account_id)

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
            logger.info("New trading day: %s — resetting strategy state.", day)
            self.engine.reset_day()
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
        try:
            result = self.client.place_bracket_order(
                contract=contract,
                action=action,
                qty=self.cfg.risk.contracts_per_trade,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
            )
            logger.info("Order placed: %s", result)
            self._pending_entry_price = signal.entry_price
        except Exception:
            logger.exception("Order placement failed; releasing position lock.")
            self.engine.position_open = False

    def _poll_positions(self) -> None:
        """Every 15s, check if the open position has flattened (stop/target hit)
        and feed the result back into the strategy engine's daily counters."""
        while True:
            time_module.sleep(15)
            if not self.engine.position_open or self._pending_entry_price is None:
                continue
            try:
                resp = requests.get(
                    f"{self.client.rest_base}/position/list",
                    headers=self.client._headers(),
                    timeout=15,
                )
                resp.raise_for_status()
                positions = resp.json()
                open_qty = sum(p.get("netPos", 0) for p in positions if p.get("accountId") == self.client.account_id)
                if open_qty == 0:
                    # Flat again — figure out win/loss from realized P&L isn't
                    # directly available here; a full implementation should read
                    # /cashBalance or /fill history. As a reasonable proxy we
                    # look at the account's realized P&L delta via /cashBalance.
                    logger.info("Position flat — trade closed. Fetching fill history for result...")
                    win = self._infer_last_trade_win()
                    self.engine.record_trade_result(win)
                    self._pending_entry_price = None
            except Exception:
                logger.exception("Position poll failed")

    def _infer_last_trade_win(self) -> bool:
        try:
            resp = requests.get(
                f"{self.client.rest_base}/fill/list",
                headers=self.client._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            fills = [f for f in resp.json() if f.get("accountId") == self.client.account_id]
            if not fills:
                return False
            fills.sort(key=lambda f: f.get("timestamp", ""))
            last_fill = fills[-1]
            exit_price = last_fill.get("price", self._pending_entry_price)
            return exit_price is not None and self._pending_entry_price is not None and (
                exit_price != self._pending_entry_price
            ) and (exit_price > self._pending_entry_price)
        except Exception:
            logger.exception("Could not infer trade result from fills; defaulting to loss for safety.")
            return False
