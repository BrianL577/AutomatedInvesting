"""Live paper-trading loop against Interactive Brokers (TWS/IB Gateway).

Unlike the Tradovate runner, IBKR pushes fill events reactively (no polling
loop needed): each account's pending bracket is resolved by watching
`execDetailsEvent` for a fill on either its take-profit or stop-loss child
order ID.

Requires a running, logged-in TWS or IB Gateway process (paper trading
mode) reachable at IBKR_HOST:IBKR_PORT — see IBKR.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ib_insync import Fill, Trade as IBTrade

from .bar_aggregator import BarAggregator  # noqa: F401 (kept for parity/reference)
from .config import AppConfig
from .ibkr_client import BracketOrderIds, Contract, IBKRClient
from .models import Bar, Direction, Signal, TradeResult
from .strategy import StrategyEngine
from .time_utils import to_et
from .trade_logger import TradeLogger
from .logging_setup import setup_logging

logger = setup_logging()


@dataclass
class _AccountState:
    account: str
    order_ids: Optional[BracketOrderIds] = None
    pending_signal: Optional[Signal] = None
    day_pnl_dollars: float = 0.0
    rate_limited: bool = False

    def reset_day(self) -> None:
        self.order_ids = None
        self.pending_signal = None
        self.day_pnl_dollars = 0.0
        self.rate_limited = False


class IBKRLiveRunner:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.client = IBKRClient(cfg.ibkr)
        self.engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk, instrument_cfg=cfg.instrument)
        self.dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
        self.trade_logger = TradeLogger(dollar_per_point=self.dollar_per_point, source="live_paper")
        self._current_day = None
        self._account_states: dict[str, _AccountState] = {}
        self._contract: Optional[Contract] = None

    def start(self) -> None:
        logger.info("Connecting to IBKR at %s:%s ...", self.cfg.ibkr.host, self.cfg.ibkr.port)
        self.client.connect()
        self._account_states = {a: _AccountState(account=a) for a in self.client.accounts}
        logger.info("Trading %d paper account(s): %s", len(self.client.accounts), self.client.accounts)

        self._contract = self.client.find_front_month_contract(self.cfg.instrument.symbol)
        logger.info("Trading contract: %s", self._contract.ib_contract.localSymbol)

        self.client.ib.execDetailsEvent += self._on_exec_details

        bars = self.client.stream_bars(self._contract, self._on_bar)

        logger.info("Streaming live bars. Ctrl+C to stop.")
        try:
            self.client.ib.run()
        except KeyboardInterrupt:
            logger.info("Stopping.")
        finally:
            self.client.ib.cancelHistoricalData(bars)
            self.client.disconnect()

    def _to_bar(self, ib_bar) -> Bar:
        ts = to_et(ib_bar.date, self.cfg.strategy.timezone)
        return Bar(
            timestamp=ts.replace(second=0, microsecond=0),
            open=float(ib_bar.open),
            high=float(ib_bar.high),
            low=float(ib_bar.low),
            close=float(ib_bar.close),
            volume=float(ib_bar.volume or 0),
        )

    def _on_bar(self, ib_bar) -> None:
        bar = self._to_bar(ib_bar)
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

        action = "BUY" if signal.direction == Direction.LONG else "SELL"
        any_order_placed = False
        for state in self._account_states.values():
            if state.rate_limited:
                logger.info("Skipping account %s: rate limit already hit today.", state.account)
                continue
            if state.pending_signal is not None:
                logger.info("Skipping account %s: already in a trade.", state.account)
                continue
            try:
                order_ids = self.client.place_bracket_order(
                    contract=self._contract,
                    account=state.account,
                    action=action,
                    qty=self.cfg.risk.contracts_per_trade,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                )
                logger.info("Bracket placed on %s: %s", state.account, order_ids)
                state.order_ids = order_ids
                state.pending_signal = signal
                any_order_placed = True
            except Exception:
                logger.exception("Order placement failed on account %s", state.account)

        if any_order_placed:
            self.engine.position_open = True

    def _on_exec_details(self, trade: IBTrade, fill: Fill) -> None:
        order_id = fill.execution.orderId
        account = fill.execution.acctNumber
        state = self._account_states.get(account)
        if state is None or state.order_ids is None or state.pending_signal is None:
            return

        if order_id == state.order_ids.take_profit_id:
            win = True
        elif order_id == state.order_ids.stop_loss_id:
            win = False
        else:
            return  # parent fill or an unrelated execution

        signal = state.pending_signal
        exit_price = float(fill.execution.price)
        pnl_points = (
            exit_price - signal.entry_price if signal.direction == Direction.LONG
            else signal.entry_price - exit_price
        )
        result = TradeResult(
            signal=signal, exit_price=exit_price, exit_timestamp=datetime.now(),
            win=win, pnl_points=pnl_points,
        )
        self.trade_logger.log_trade(result, account_name=account)
        self.engine.record_trade_result(win, pnl_points=pnl_points)

        pnl_dollars = pnl_points * self.dollar_per_point
        state.day_pnl_dollars += pnl_dollars
        if state.day_pnl_dollars >= self.cfg.risk.daily_profit_cap:
            state.rate_limited = True
            logger.info("Account %s hit daily profit cap ($%.2f) — done for the day.", account, state.day_pnl_dollars)
        elif state.day_pnl_dollars <= -self.cfg.risk.daily_loss_cap:
            state.rate_limited = True
            logger.info("Account %s hit daily loss cap ($%.2f) — done for the day.", account, state.day_pnl_dollars)

        logger.info(
            "Trade closed on %s: %s pnl=%.2f pts ($%.2f) win=%s day_pnl=$%.2f",
            account, signal.direction.value, pnl_points, pnl_dollars, win, state.day_pnl_dollars,
        )

        state.order_ids = None
        state.pending_signal = None
