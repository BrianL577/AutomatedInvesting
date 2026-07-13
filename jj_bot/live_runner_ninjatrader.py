"""Live paper-trading loop against NinjaTrader 8 (via ATI + companion
NinjaScript exporter — see ninjatrader_client.py and NINJATRADER.md).

Unlike IBKR's reactive fill events, this polls two CSV files the companion
NinjaScript writes: bars.csv (closed 1-min bars) and fills.csv (order
fills), each on its own thread.

Must run on the same Windows machine as NinjaTrader (or a Windows VPS with
NinjaTrader installed) — NinjaTrader has no Linux/headless mode.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import AppConfig
from .models import Bar, Direction, Signal, TradeResult
from .ninjatrader_client import BracketOrderIds, Contract, NinjaTraderClient
from .strategy import StrategyEngine
from .topstep_eval_sim import TopstepEvalSimConfig, TopstepEvalSimulator
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


class NinjaTraderLiveRunner:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.client = NinjaTraderClient(cfg.ninjatrader)
        self.engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk, instrument_cfg=cfg.instrument)
        self.dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
        self.trade_logger = TradeLogger(dollar_per_point=self.dollar_per_point, source="live_paper")
        self._current_day = None
        self._account_states: dict[str, _AccountState] = {}
        self._topstep_sims: dict[str, TopstepEvalSimulator] = {}
        self._contract: Optional[Contract] = None

    def _topstep_sim_config(self) -> TopstepEvalSimConfig:
        te = self.cfg.topstep_eval
        return TopstepEvalSimConfig(
            account_size=te.account_size,
            profit_target=te.profit_target,
            trailing_max_drawdown=te.trailing_max_drawdown,
            eval_fee=te.eval_fee,
            reactivation_fee=te.reactivation_fee,
            monthly_fee=te.monthly_fee,
            activation_fee=te.activation_fee,
            no_activation_fee_monthly_fee=te.no_activation_fee_monthly_fee,
            pass_rate_switch_threshold=te.pass_rate_switch_threshold,
            payout_share=te.payout_share,
            max_payout_per_event=te.max_payout_per_event,
            max_payout_balance_share=te.max_payout_balance_share,
            min_winning_days_for_payout=te.min_winning_days_for_payout,
            min_winning_day_profit=te.min_winning_day_profit,
            consistency_path_min_days=te.consistency_path_min_days,
            consistency_path_max_best_day_share=te.consistency_path_max_best_day_share,
        )

    def start(self) -> None:
        logger.info("Connecting to NinjaTrader ATI ...")
        self.client.connect()
        self._account_states = {a: _AccountState(account=a) for a in self.client.accounts}
        # One independent Topstep eval/funded simulator per account, fed
        # each account's own daily P&L — see jj_bot/topstep_eval_sim.py.
        # Purely informational: never affects real order placement, just
        # logs what today's paper results would mean on a real Topstep
        # account, so you can gauge readiness before ever connecting one.
        sim_cfg = self._topstep_sim_config()
        self._topstep_sims = {
            a: TopstepEvalSimulator(sim_cfg, label=a, state_path=Path(f"topstep_sim_state_{a}.json"))
            for a in self.client.accounts
        }
        logger.info("Trading %d sim account(s): %s", len(self.client.accounts), self.client.accounts)

        self._contract = self.client.find_front_month_contract(self.cfg.instrument.symbol)
        logger.info("Trading instrument: %s", self._contract.symbol)

        fills_thread = threading.Thread(target=self.client.tail_fills, args=(self._on_fill,), daemon=True)
        fills_thread.start()

        logger.info("Streaming bars from bars.csv. Ctrl+C to stop.")
        try:
            self.client.tail_bars(self._on_bar)
        except KeyboardInterrupt:
            logger.info("Stopping.")

    def _to_bar(self, row: dict) -> Bar:
        return Bar(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume") or 0),
        )

    def _on_bar(self, row: dict) -> None:
        bar = self._to_bar(row)
        day = bar.timestamp.date()
        if self._current_day != day:
            logger.info("New trading day: %s — resetting strategy + all account state.", day)
            if self._current_day is not None:
                # Feed yesterday's realized $ P&L into each account's
                # Topstep simulator before wiping it for the new day.
                for state in self._account_states.values():
                    sim = self._topstep_sims.get(state.account)
                    if sim is not None:
                        sim.record_day(state.day_pnl_dollars)
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

    def _on_fill(self, row: dict) -> None:
        order_id = row.get("order_id")
        account = row.get("account")
        state = self._account_states.get(account)
        if state is None or state.order_ids is None or state.pending_signal is None:
            return

        if order_id == state.order_ids.take_profit_id:
            win = True
        elif order_id == state.order_ids.stop_loss_id:
            win = False
        else:
            return  # entry fill or an unrelated execution

        signal = state.pending_signal
        exit_price = float(row["price"])
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
