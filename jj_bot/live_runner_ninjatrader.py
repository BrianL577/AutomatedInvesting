"""Live paper-trading loop against NinjaTrader 8 (via ATI + companion
NinjaScript exporter — see ninjatrader_client.py and NINJATRADER.md).

Unlike IBKR's reactive fill events, this polls two CSV files the companion
NinjaScript writes: bars.csv (closed 1-min bars) and fills.csv (order
fills), each on its own thread.

Must run on the same Windows machine as NinjaTrader (or a Windows VPS with
NinjaTrader installed) — NinjaTrader has no Linux/headless mode.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .config import AppConfig, reload_strategy_and_risk
from .models import Bar, Direction, Signal, TradeResult
from .ninjatrader_client import BracketOrderIds, Contract, NinjaTraderClient
from .strategy import StrategyEngine
from .topstep_eval_sim import TopstepEvalSimConfig, TopstepEvalSimulator
from .trade_logger import TradeLogger
from .logging_setup import setup_logging

logger = setup_logging()


def _pid_is_running(pid: int) -> bool:
    """Windows-safe liveness check for a PID (os.kill(pid, 0) doesn't work
    the same way on Windows, so use OpenProcess instead)."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


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
    # Daily risk counters (trades taken, consecutive losses, running P&L,
    # rate-limit flags) live only on this object in memory, so a process
    # restart mid-day would otherwise silently reset every daily limit back
    # to zero — confirmed live: a restart let the bot re-enter after it
    # should have already been stopped for the day. Persisted here, keyed by
    # calendar date, and restored on startup only if the file's date matches
    # today; a genuinely new day still starts from zero as before.
    _DAILY_STATE_PATH = Path("daily_risk_state.json")

    # Single-instance guard. Confirmed live: running a second copy of
    # run_live.py (e.g. a leftover terminal + the scheduled task both alive)
    # let two processes each think they were under the daily trade/loss cap
    # independently, stacking multiple brackets on the same signal. This
    # lock makes a second instance refuse to start instead of silently
    # trading alongside the first one.
    _LOCK_PATH = Path("run_live.lock")

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
        self._holds_lock = False

    def _acquire_single_instance_lock(self) -> None:
        if self._LOCK_PATH.exists():
            try:
                old_pid = int(self._LOCK_PATH.read_text().strip())
            except (ValueError, OSError):
                old_pid = None
            if old_pid is not None and _pid_is_running(old_pid):
                logger.error(
                    "Another instance is already running (PID %d, lock file %s). "
                    "Refusing to start a second instance — check Task Manager / Task Scheduler "
                    "before forcing this.",
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
                # The file may be owned by a different user/session (e.g. a
                # prior run under Task Scheduler's SYSTEM/other-user
                # context) and not deletable by this user. Don't crash the
                # whole bot over a lock-file permission issue — log it
                # loudly and continue rather than exiting, since a false
                # "can't start" is worse than a rare missed duplicate check
                # here (the PID-liveness check above already confirmed the
                # old owner is dead).
                logger.exception(
                    "Could not remove stale lock file %s — you may need to delete it manually "
                    "(e.g. 'del %s' from an elevated prompt). Continuing anyway since the prior "
                    "owner (PID %s) is confirmed not running.",
                    self._LOCK_PATH, self._LOCK_PATH, old_pid,
                )
        try:
            self._LOCK_PATH.write_text(str(os.getpid()))
            self._holds_lock = True
        except OSError:
            logger.exception(
                "Could not write lock file %s — single-instance protection is NOT active "
                "for this run. Fix file permissions before relying on this safeguard.",
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

    def _save_daily_state(self, day: date) -> None:
        payload = {
            "date": day.isoformat(),
            "trades_today": self.engine.trades_today,
            "consecutive_losses": self.engine.consecutive_losses,
            "day_pnl_points": self.engine.day_pnl_points,
            "rate_limited": self.engine.rate_limited,
            "accounts": {
                account: {"day_pnl_dollars": state.day_pnl_dollars, "rate_limited": state.rate_limited}
                for account, state in self._account_states.items()
            },
        }
        # Atomic write: write to a temp file, then replace() the real file
        # in one filesystem op. Avoids the PermissionError / partial-write
        # corruption seen when something else (a second instance, an
        # antivirus scan, etc.) briefly holds the file open.
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

    def _restore_daily_state_if_same_day(self, day: date) -> None:
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
        self.engine.day_pnl_points = payload.get("day_pnl_points", 0.0)
        self.engine.rate_limited = payload.get("rate_limited", False)
        for account, saved in (payload.get("accounts") or {}).items():
            state = self._account_states.get(account)
            if state is not None:
                state.day_pnl_dollars = saved.get("day_pnl_dollars", 0.0)
                state.rate_limited = saved.get("rate_limited", False)
        logger.info(
            "Restored today's risk state after restart: trades_today=%d consecutive_losses=%d day_pnl=%.2f rate_limited=%s",
            self.engine.trades_today, self.engine.consecutive_losses, self.engine.day_pnl_points, self.engine.rate_limited,
        )

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
        self._acquire_single_instance_lock()
        try:
            self._start_inner()
        finally:
            self._release_single_instance_lock()

    def _start_inner(self) -> None:
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
            is_process_startup = self._current_day is None
            logger.info("New trading day: %s — resetting strategy + all account state.", day)
            if self._current_day is not None:
                # Feed yesterday's realized $ P&L into each account's
                # Topstep simulator before wiping it for the new day.
                for state in self._account_states.values():
                    sim = self._topstep_sims.get(state.account)
                    if sim is not None:
                        sim.record_day(state.day_pnl_dollars)
            # Pick up a strategy switched on the dashboard since this
            # process started — only at this once-a-day boundary, never
            # mid-session, so a strategy swap never lands on top of an
            # already-open position or a bracket sized under the old
            # strategy's stop/target.
            try:
                new_strategy, new_risk = reload_strategy_and_risk(self.cfg.config_path)
                if new_strategy != self.cfg.strategy or new_risk != self.cfg.risk:
                    logger.info("Active strategy/risk changed since startup — applying for the new trading day.")
                self.cfg.strategy = new_strategy
                self.cfg.risk = new_risk
                self.engine.strategy_cfg = new_strategy
                self.engine.risk_cfg = new_risk
            except Exception:
                logger.exception("Failed to reload active strategy for the new trading day — keeping prior config.")
            self.engine.reset_day()
            for state in self._account_states.values():
                state.reset_day()
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

        if self._current_day is not None:
            self._save_daily_state(self._current_day)