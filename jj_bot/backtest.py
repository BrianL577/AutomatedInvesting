"""Prop-firm-style backtester.

Per JJ's own advice: don't just look at a naive equity curve. Simulate actual
eval attempts against an end-of-day trailing-drawdown account and report a
pass rate, not just average expectancy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from .config import AppConfig
from .models import Bar, Direction, TradeResult
from .risk_manager import EvalAccountState
from .strategy import StrategyEngine
from .time_utils import to_et
from .trade_logger import TradeLogger


def load_bars_csv(path: str, tz_name: str) -> list[Bar]:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    bars = []
    for row in df.itertuples(index=False):
        ts = to_et(row.timestamp.to_pydatetime() if isinstance(row.timestamp, pd.Timestamp) else row.timestamp, tz_name)
        bars.append(
            Bar(
                timestamp=ts,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(getattr(row, "volume", 0.0) or 0.0),
            )
        )
    bars.sort(key=lambda b: b.timestamp)
    return bars


def group_by_day(bars: list[Bar]) -> dict[date, list[Bar]]:
    out: dict[date, list[Bar]] = defaultdict(list)
    for b in bars:
        out[b.timestamp.date()].append(b)
    return dict(sorted(out.items()))


def _simulate_trade_exit(entry_idx: int, day_bars: list[Bar], direction: Direction, stop: float, target: float):
    """Walk forward bars after entry, return (exit_price, exit_ts, win). Flattens at day close if untouched."""
    for bar in day_bars[entry_idx + 1:]:
        if direction == Direction.LONG:
            hit_stop = bar.low <= stop
            hit_target = bar.high >= target
        else:
            hit_stop = bar.high >= stop
            hit_target = bar.low <= target
        if hit_stop and hit_target:
            # Conservative: assume stop hit first when both touched in the same bar.
            return stop, bar.timestamp, False
        if hit_stop:
            return stop, bar.timestamp, False
        if hit_target:
            return target, bar.timestamp, True
    last = day_bars[-1]
    win = (last.close > day_bars[entry_idx].close) == (direction == Direction.LONG)
    return last.close, last.timestamp, win


def run_strategy_on_day(day_bars: list[Bar], engine: StrategyEngine) -> list[TradeResult]:
    engine.reset_day()
    results: list[TradeResult] = []
    for i, bar in enumerate(day_bars):
        signal = engine.on_bar(bar)
        if signal is None:
            continue
        exit_price, exit_ts, win = _simulate_trade_exit(i, day_bars, signal.direction, signal.stop_price, signal.target_price)
        pnl_points = (
            exit_price - signal.entry_price if signal.direction == Direction.LONG
            else signal.entry_price - exit_price
        )
        results.append(TradeResult(signal=signal, exit_price=exit_price, exit_timestamp=exit_ts, win=win, pnl_points=pnl_points))
        engine.record_trade_result(win, pnl_points=pnl_points)
    return results


@dataclass
class BacktestReport:
    trades: list[TradeResult]
    daily_pnl_points: dict[date, float]
    pass_rate: float
    attempts: int
    passes: int
    avg_days_to_result: float


def run_backtest(cfg: AppConfig, bars: list[Bar], log_trades: bool = True) -> BacktestReport:
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk, instrument_cfg=cfg.instrument)
    by_day = group_by_day(bars)
    days = list(by_day.keys())

    dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
    logger = TradeLogger(dollar_per_point=dollar_per_point, source="backtest") if log_trades else None
    if logger:
        logger.clear()

    all_trades: list[TradeResult] = []
    daily_pnl: dict[date, float] = {}
    for d, day_bars in by_day.items():
        trades = run_strategy_on_day(day_bars, engine)
        all_trades.extend(trades)
        daily_pnl[d] = sum(t.pnl_points for t in trades)
        if logger:
            for t in trades:
                logger.log_trade(t)

    # Prop-firm-style pass-rate simulation: start a fresh eval attempt on each
    # day in the dataset and play forward day-by-day until pass or bust.
    attempts = 0
    passes = 0
    days_to_result: list[int] = []
    for start_idx in range(len(days)):
        account = EvalAccountState(cfg=cfg.topstep_eval, instrument=cfg.instrument)
        attempts += 1
        n_days = 0
        for d in days[start_idx:]:
            account.apply_day(daily_pnl[d])
            n_days += 1
            if account.passed or account.busted:
                break
        days_to_result.append(n_days)
        if account.passed:
            passes += 1

    pass_rate = passes / attempts if attempts else 0.0
    avg_days = sum(days_to_result) / len(days_to_result) if days_to_result else 0.0

    return BacktestReport(
        trades=all_trades,
        daily_pnl_points=daily_pnl,
        pass_rate=pass_rate,
        attempts=attempts,
        passes=passes,
        avg_days_to_result=avg_days,
    )


def print_report(report: BacktestReport, cfg: AppConfig) -> None:
    wins = sum(1 for t in report.trades if t.win)
    total = len(report.trades)
    win_rate = wins / total if total else 0.0
    total_points = sum(t.pnl_points for t in report.trades)

    print("=" * 60)
    print("JJ Strategy Backtest Report")
    print("=" * 60)
    print(f"Trades taken:        {total}")
    print(f"Win rate:             {win_rate:.1%}")
    print(f"Total points:         {total_points:+.2f}")
    print(f"Trading days:         {len(report.daily_pnl_points)}")
    print("-" * 60)
    print(f"Prop-firm eval sim ({cfg.topstep_eval.account_size:.0f} acct,"
          f" +{cfg.topstep_eval.profit_target:.0f} target,"
          f" {cfg.topstep_eval.trailing_max_drawdown:.0f} trailing DD)")
    print(f"  Attempts simulated: {report.attempts}")
    print(f"  Pass rate:          {report.pass_rate:.1%}")
    print(f"  Avg days to result: {report.avg_days_to_result:.1f}")
    print("=" * 60)
