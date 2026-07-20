import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.config import load_config
from jj_bot.models import Bar, Direction, Phase
from jj_bot.strategy import StrategyEngine

ET = pytz.timezone("America/New_York")


def mkbar(hh, mm, o, h, l, c, day=1):
    ts = ET.localize(datetime(2024, 1, day, hh, mm))
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=100)


def test_continuation_short_signal():
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)

    # Opening candle: red (close < open) -> continuation direction should be SHORT
    open_bar = mkbar(9, 30, 100.0, 100.5, 98.0, 98.5)
    assert engine.on_bar(open_bar) is None
    assert engine.continuation_direction == Direction.SHORT

    # A few quiet bars forming a clear V-shaped swing low (structure) near 98.0
    quiet_bars = [
        (31, 98.5, 98.6, 98.3, 98.3),
        (32, 98.3, 98.35, 98.0, 98.1),  # swing low here
        (33, 98.1, 98.35, 98.05, 98.3),
        (34, 98.3, 98.5, 98.25, 98.45),
    ]
    for m, o, h, l, c in quiet_bars:
        engine.on_bar(mkbar(9, m, o, h, l, c))
    prev_close = quiet_bars[-1][4]

    # Big displacement candle breaking below recent lows, closing well below, no wicks
    disp = mkbar(9, 35, prev_close, prev_close + 0.1, prev_close - 5, prev_close - 4.9)
    signal = engine.on_bar(disp)
    assert signal is not None
    assert signal.direction == Direction.SHORT
    assert signal.stop_price > signal.entry_price
    assert signal.target_price < signal.entry_price


def test_max_trades_per_day_enforced():
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)
    engine.trades_today = cfg.risk.max_trades_per_day
    open_bar = mkbar(9, 30, 100.0, 100.5, 99.5, 100.2)
    engine.on_bar(open_bar)
    next_bar = mkbar(9, 31, 100.2, 105.0, 100.1, 104.9)
    assert engine.on_bar(next_bar) is None


def test_reset_day_clears_state():
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)
    engine.trades_today = 3
    engine.consecutive_losses = 2
    engine.reset_day()
    assert engine.trades_today == 0
    assert engine.consecutive_losses == 0
    assert engine.open_price is None


def test_first_bar_of_day_past_cutoff_waits_instead_of_ending_day():
    # Simulates a weekend reopen: the first bar seen for this trading day
    # arrives Sunday evening, long after today's hard_cutoff (11:00) has
    # already passed for that calendar date. No session for this date ever
    # happened, so the engine should stay WAITING_FOR_OPEN (and wait for
    # tomorrow's open) rather than jumping straight to DONE_FOR_DAY.
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)

    late_bar = mkbar(18, 1, 100.0, 100.5, 99.5, 100.2)
    assert engine.on_bar(late_bar) is None
    assert engine.phase == Phase.WAITING_FOR_OPEN
    assert engine.open_bar is None


if __name__ == "__main__":
    test_continuation_short_signal()
    test_max_trades_per_day_enforced()
    test_reset_day_clears_state()
    test_first_bar_of_day_past_cutoff_waits_instead_of_ending_day()
    print("All tests passed.")
