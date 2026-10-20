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


def test_marginal_break_is_filtered_as_noise():
    """CONFIRMED LIVE: a full week where every single trade (real and
    virtual, both directions, both phases) lost close to its full stop,
    with a reversal test ruling out a direction/sign bug -- pointed at
    entry quality instead. break_buffer_points was raised 1.0 -> 3.0
    because 1.0 point is within normal noise on NQ, letting the bot enter
    on a marginal, unconfirmed poke past a swing level rather than a real
    break. This close only clears the swing low by ~1.6pts -- would have
    triggered a (noise) break at the old 1.0pt buffer, must NOT trigger
    one now."""
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)

    open_bar = mkbar(9, 30, 100.0, 100.5, 98.0, 98.5)
    engine.on_bar(open_bar)
    quiet_bars = [
        (31, 98.5, 98.6, 98.3, 98.3),
        (32, 98.3, 98.35, 98.0, 98.1),  # swing low here, price=98.0
        (33, 98.1, 98.35, 98.05, 98.3),
        (34, 98.3, 98.5, 98.25, 98.45),
    ]
    for m, o, h, l, c in quiet_bars:
        engine.on_bar(mkbar(9, m, o, h, l, c))

    # Closes only ~1.6pts below the 98.0 swing low -- clears the OLD 1.0pt
    # buffer (would have broken) but not the new 3.0pt buffer.
    marginal = mkbar(9, 35, 98.45, 98.5, 96.3, 96.4)
    signal = engine.on_bar(marginal)
    assert signal is None, "a marginal 1.6pt break must be filtered as noise, not treated as a real BOS"


def test_session_only_trades_once_but_a_later_session_still_gets_its_own_shot():
    """Per explicit user request: max_trades_per_day now means "one trade
    per SESSION", not one trade for the whole day -- each of the 5 daily
    sessions (NY AM, NY PM, Session, Asia, London) gets its own
    independent shot. A session that already traded must not trade again."""
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)
    open_bar = mkbar(9, 30, 100.0, 100.5, 99.5, 100.2)
    engine.on_bar(open_bar)  # anchors NY AM
    engine.sessions[0].traded = True  # simulate NY AM having already taken its one trade
    next_bar = mkbar(9, 31, 100.2, 105.0, 100.1, 104.9)
    assert engine.on_bar(next_bar) is None, "NY AM already traded -- a real BOS must still be ignored"


def test_reset_day_clears_state():
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)
    engine.trades_today = 3
    engine.consecutive_losses = 2
    engine.sessions[0].traded = True
    engine.reset_day()
    assert engine.trades_today == 0
    assert engine.consecutive_losses == 0
    assert engine.open_price is None
    assert engine.sessions[0].traded is False


def test_bar_between_sessions_waits_instead_of_ending_day():
    # Per explicit user request, there are now 5 daily sessions (NY AM
    # 09:30-11:00, NY PM 14:00-16:00, Session 18:00-20:00, Asia 20:00-22:00,
    # London 03:00-05:00 ET) -- this bar (23:30) falls in the one genuine
    # gap between all of them (after Asia closes, before London opens). No
    # session covers this time, so the engine should stay WAITING_FOR_OPEN
    # (for the next session to open) rather than jumping to DONE_FOR_DAY.
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)

    late_bar = mkbar(23, 30, 100.0, 100.5, 99.5, 100.2)
    assert engine.on_bar(late_bar) is None
    assert engine.phase == Phase.WAITING_FOR_OPEN
    assert engine.open_bar is None


def test_second_session_trades_independently_of_an_already_traded_first_session():
    """Per explicit user request: 5 independent daily sessions, not one.
    NY AM having already taken its trade must not block Asia (20:00 ET)
    from taking its own, completely independent trade."""
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)
    engine.sessions[0].traded = True  # NY AM already traded today

    # Opening candle for the Asia session (20:00 ET): red -> SHORT continuation direction
    open_bar = mkbar(20, 0, 100.0, 100.5, 98.0, 98.5)
    assert engine.on_bar(open_bar) is None
    asia = next(s for s in engine.sessions if s.window.name == "Asia")
    assert asia.continuation_direction == Direction.SHORT

    quiet_bars = [
        (20, 1, 98.5, 98.6, 98.3, 98.3),
        (20, 2, 98.3, 98.35, 98.0, 98.1),
        (20, 3, 98.1, 98.35, 98.05, 98.3),
        (20, 4, 98.3, 98.5, 98.25, 98.45),
    ]
    for hh, mm, o, h, l, c in quiet_bars:
        engine.on_bar(mkbar(hh, mm, o, h, l, c))
    prev_close = quiet_bars[-1][4]

    disp = mkbar(20, 5, prev_close, prev_close + 0.1, prev_close - 5, prev_close - 4.9)
    signal = engine.on_bar(disp)
    assert signal is not None
    assert signal.direction == Direction.SHORT
    assert "[Asia]" in signal.reason
    assert asia.traded is True


def test_day_level_consecutive_losses_cap_blocks_every_session():
    """The day-level risk caps (consecutive_losses, rate_limited) are
    shared across all 5 sessions, not reset per session -- a dollar-risk
    circuit breaker, not a per-session opportunity. Confirms this blocks
    entry before a session even gets to anchor."""
    cfg = load_config()
    engine = StrategyEngine(strategy_cfg=cfg.strategy, risk_cfg=cfg.risk)
    engine.consecutive_losses = cfg.risk.stop_after_consecutive_losses
    open_bar = mkbar(9, 30, 100.0, 100.5, 99.5, 100.2)
    assert engine.on_bar(open_bar) is None
    assert engine.phase == Phase.DONE_FOR_DAY
    assert engine.open_bar is None, "must not even anchor once the day-level loss-streak cap is hit"


if __name__ == "__main__":
    test_continuation_short_signal()
    test_marginal_break_is_filtered_as_noise()
    test_session_only_trades_once_but_a_later_session_still_gets_its_own_shot()
    test_reset_day_clears_state()
    test_bar_between_sessions_waits_instead_of_ending_day()
    test_second_session_trades_independently_of_an_already_traded_first_session()
    test_day_level_consecutive_losses_cap_blocks_every_session()
    print("All tests passed.")
