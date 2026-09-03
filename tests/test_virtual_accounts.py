import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.config import load_config
from jj_bot.models import Bar, Direction, Phase, SetupGrade, Signal
from jj_bot.virtual_accounts import VirtualAccountManager

ET = pytz.timezone("America/New_York")


def mkbar(hh, mm, o, h, l, c, day=1):
    ts = ET.localize(datetime(2024, 1, day, hh, mm))
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=100)


def _manager(cfg, num_accounts):
    # Scratch trade log + state file so tests never touch
    # dashboard/data/trades.json or the real virtual_daily_state.json.
    log_path = Path(tempfile.mkstemp(suffix=".json")[1])
    log_path.write_text("[]")
    state_path = Path(tempfile.mkstemp(suffix=".json")[1])
    state_path.unlink()  # must not exist yet — _restore_state_if_same_day treats "exists" as "has saved state"
    return VirtualAccountManager(cfg, num_accounts=num_accounts, trade_log_path=log_path, state_path=state_path)


def _feed_continuation_setup(manager, start_min=30):
    """Same displacement/BOS shape as test_strategy.py's continuation test —
    produces exactly one distinct signal."""
    open_bar = mkbar(9, start_min, 100.0, 100.5, 98.0, 98.5)
    manager.on_bar(open_bar)

    quiet_bars = [
        (start_min + 1, 98.5, 98.6, 98.3, 98.3),
        (start_min + 2, 98.3, 98.35, 98.0, 98.1),
        (start_min + 3, 98.1, 98.35, 98.05, 98.3),
        (start_min + 4, 98.3, 98.5, 98.25, 98.45),
    ]
    for m, o, h, l, c in quiet_bars:
        manager.on_bar(mkbar(9, m, o, h, l, c))
    prev_close = quiet_bars[-1][4]

    disp = mkbar(9, start_min + 5, prev_close, prev_close + 0.1, prev_close - 5, prev_close - 4.9)
    return manager.on_bar(disp)


def test_signal_assigned_to_one_idle_account():
    cfg = load_config()
    manager = _manager(cfg, 10)

    result = _feed_continuation_setup(manager)
    assert result is not None
    account, signal = result
    assert account.name == "Virtual-01"
    assert signal.direction == Direction.SHORT
    assert account.traded_today
    traded = [a for a in manager.accounts if a.traded_today]
    assert len(traded) == 1


def test_only_up_to_num_accounts_trade_per_day():
    cfg = load_config()
    manager = _manager(cfg, 2)
    for a in manager.accounts:
        a.traded_today = True
    manager._current_day = mkbar(9, 30, 1, 1, 1, 1).timestamp.date()

    result = manager.on_bar(mkbar(9, 31, 100, 105, 99, 104))
    assert result is None  # every virtual account already traded today


def test_reset_day_frees_all_accounts():
    cfg = load_config()
    manager = _manager(cfg, 3)
    for a in manager.accounts:
        a.traded_today = True
    manager.reset_day()
    assert all(not a.traded_today for a in manager.accounts)
    assert all(a.pending_signal is None for a in manager.accounts)


def test_check_exits_resolves_target_hit():
    cfg = load_config()
    manager = _manager(cfg, 5)
    _feed_continuation_setup(manager)

    account = manager.accounts[0]
    signal = account.pending_signal
    assert signal is not None

    # Bar that touches the (short) target well below entry.
    target_bar = mkbar(9, 40, signal.entry_price, signal.entry_price + 1, signal.target_price - 1, signal.target_price)
    results = manager.check_exits(target_bar)

    assert len(results) == 1
    assert results[0].win is True
    assert results[0].qty == cfg.risk.contracts_per_trade
    assert account.pending_signal is None


def test_chart_renderer_hook_is_called_and_passed_through():
    cfg = load_config()
    log_path = Path(tempfile.mkstemp(suffix=".json")[1])
    log_path.write_text("[]")
    state_path = Path(tempfile.mkstemp(suffix=".json")[1])
    state_path.unlink()
    calls = []

    def fake_renderer(account, trade):
        calls.append((account.name, trade.win))
        return "/tmp/fake_chart.png"

    manager = VirtualAccountManager(
        cfg, num_accounts=3, trade_log_path=log_path, chart_renderer=fake_renderer, state_path=state_path,
    )
    _feed_continuation_setup(manager)
    account = manager.accounts[0]
    signal = account.pending_signal
    target_bar = mkbar(9, 40, signal.entry_price, signal.entry_price + 1, signal.target_price - 1, signal.target_price)
    manager.check_exits(target_bar)

    assert calls == [("Virtual-01", True)]


def test_restart_mid_day_does_not_let_same_account_retrade():
    """CONFIRMED LIVE: a real process restart mid-session let Virtual-01
    take 3 trades in one day instead of 1 — traded_today lived only in
    memory, so a fresh process's first bar treated the already-in-progress
    calendar day as brand new. A second manager sharing the same
    state_path (simulating a restart) must see Virtual-01 as already-traded
    and never offer it up as idle again."""
    cfg = load_config()
    log_path = Path(tempfile.mkstemp(suffix=".json")[1])
    log_path.write_text("[]")
    state_path = Path(tempfile.mkstemp(suffix=".json")[1])
    state_path.unlink()

    manager1 = VirtualAccountManager(cfg, num_accounts=3, trade_log_path=log_path, state_path=state_path)
    result1 = _feed_continuation_setup(manager1, start_min=30)
    assert result1 is not None
    account1, _ = result1
    assert account1.name == "Virtual-01"

    # Simulate a process restart: a brand-new manager, same state_path,
    # fed a bar on the exact same calendar day. Without the fix, this
    # manager would have no memory that Virtual-01 already traded today.
    manager2 = VirtualAccountManager(cfg, num_accounts=3, trade_log_path=log_path, state_path=state_path)
    manager2.on_bar(mkbar(9, 30, 100, 100.5, 98.0, 98.5))  # any bar on day=1, only the date matters here

    restored_account1 = manager2.accounts[0]
    assert restored_account1.name == "Virtual-01"
    assert restored_account1.traded_today is True, "restart forgot Virtual-01 already traded today"
    assert manager2._idle_account().name == "Virtual-02", "restart would let Virtual-01 be picked again"


def _canned_signal(hh, mm, direction, entry_price, stop_offset=25.0, target_offset=38.0):
    ts = mkbar(hh, mm, 1, 1, 1, 1).timestamp
    if direction == Direction.SHORT:
        stop, target = entry_price + stop_offset, entry_price - target_offset
    else:
        stop, target = entry_price - stop_offset, entry_price + target_offset
    return Signal(
        timestamp=ts, direction=direction, entry_price=entry_price, stop_price=stop, target_price=target,
        phase=Phase.CONTINUATION, grade=SetupGrade.A, reason="test",
    )


def test_same_move_retrigger_is_skipped_but_genuine_extension_is_not():
    """CONFIRMED LIVE: virtual accounts were entering ~1 bar apart, chasing
    one continuing move instead of taking genuinely distinct setups --
    _nearest_structure's rolling-extreme fallback trivially re-breaks on
    the very next bar during any trend, which the real bot never exposes
    (it stops looking after its one trade/day). Mocking the engine's raw
    signal output isolates the gate itself from needing to hand-craft two
    real, independently-valid structural breaks."""
    cfg = load_config()
    manager = _manager(cfg, 5)
    day = mkbar(9, 30, 1, 1, 1, 1).timestamp.date()
    manager._current_day = day
    manager._is_first_day_check = False

    manager.engine.on_bar = lambda bar: _canned_signal(9, 31, Direction.SHORT, 29150.0)
    result1 = manager.on_bar(mkbar(9, 31, 1, 1, 1, 1))
    assert result1 is not None
    account1, signal1 = result1
    assert account1.name == "Virtual-01"

    # Same direction, only 10pts further than the last assigned entry
    # (< stop_points, 25 by default) -- the same continuing move, not a
    # fresh setup. Must be skipped, not handed to Virtual-02.
    manager.engine.on_bar = lambda bar: _canned_signal(9, 32, Direction.SHORT, 29140.0)
    result2 = manager.on_bar(mkbar(9, 32, 1, 1, 1, 1))
    assert result2 is None, "same-move retrigger should have been skipped"

    # Same direction, genuinely extended >= stop_points beyond the last
    # assigned entry -- real fresh continuation, must be accepted.
    manager.engine.on_bar = lambda bar: _canned_signal(9, 33, Direction.SHORT, 29100.0)
    result3 = manager.on_bar(mkbar(9, 33, 1, 1, 1, 1))
    assert result3 is not None
    account3, signal3 = result3
    assert account3.name == "Virtual-02", "genuine extension should reach the next idle account"
    assert signal3.entry_price == 29100.0


def test_idle_account_prioritizes_untraded_then_highest_then_lowest():
    """CONFIRMED LIVE BUG: a plain "highest balance wins" scan starves any
    account still sitting at its untouched $0 starting balance -- $0 always
    loses to any account that has ever posted even a small win, so only a
    handful of early winners ever traded, and the rest only got a look-in
    once every winner had gone net-negative. Priority must be: untraded
    ($0) accounts first, then highest balance among the rest, then lowest
    (most negative) last."""
    cfg = load_config()
    manager = _manager(cfg, 5)
    manager.accounts[0].net_dollars = 100.0   # Virtual-01 -- has traded, small win
    manager.accounts[1].net_dollars = 2500.0  # Virtual-02 -- has traded, biggest win
    manager.accounts[2].net_dollars = -300.0  # Virtual-03 -- has traded, net loss
    # Virtual-04, Virtual-05 stay at 0.0 -- never traded yet

    chosen = manager._idle_account()
    assert chosen.name == "Virtual-04", "an untouched $0 account must win over any account with a balance"

    manager.accounts[3].traded_today = True  # simulate Virtual-04 just got assigned
    chosen2 = manager._idle_account()
    assert chosen2.name == "Virtual-05", "the other untouched $0 account is still next, ahead of any balance"

    manager.accounts[4].traded_today = True  # simulate Virtual-05 just got assigned
    chosen3 = manager._idle_account()
    assert chosen3.name == "Virtual-02", "once no $0 accounts remain, highest balance wins"

    manager.accounts[1].traded_today = True  # simulate Virtual-02 just got assigned
    chosen4 = manager._idle_account()
    assert chosen4.name == "Virtual-01", "next-highest balance among the remaining idle accounts"

    manager.accounts[0].traded_today = True  # simulate Virtual-01 just got assigned
    chosen5 = manager._idle_account()
    assert chosen5.name == "Virtual-03", "the only account left is negative -- it still gets picked, last"


def test_net_dollars_persists_across_a_restart_and_a_new_day():
    """net_dollars is a running lifetime total, unlike traded_today (a
    daily flag) — it must survive both a restart AND a day rollover,
    where traded_today correctly resets to False."""
    cfg = load_config()
    log_path = Path(tempfile.mkstemp(suffix=".json")[1])
    log_path.write_text("[]")
    state_path = Path(tempfile.mkstemp(suffix=".json")[1])
    state_path.unlink()

    manager1 = VirtualAccountManager(cfg, num_accounts=3, trade_log_path=log_path, state_path=state_path)
    manager1.on_bar(mkbar(9, 30, 100, 100, 100, 100, day=1))  # anchors day=1
    manager1.accounts[1].net_dollars = 1520.0
    manager1._save_state(manager1._current_day)

    # Restart on the SAME day: net_dollars restored, traded_today untouched (still False).
    manager2 = VirtualAccountManager(cfg, num_accounts=3, trade_log_path=log_path, state_path=state_path)
    manager2.on_bar(mkbar(9, 30, 100, 100, 100, 100, day=1))
    assert manager2.accounts[1].net_dollars == 1520.0
    assert manager2.accounts[1].traded_today is False

    # A genuinely new day (day=2): net_dollars still carries over, traded_today stays reset.
    manager3 = VirtualAccountManager(cfg, num_accounts=3, trade_log_path=log_path, state_path=state_path)
    manager3.on_bar(mkbar(9, 30, 100, 100, 100, 100, day=2))
    assert manager3.accounts[1].net_dollars == 1520.0, "lifetime balance must not reset on a new day"
    assert manager3.accounts[1].traded_today is False


if __name__ == "__main__":
    test_signal_assigned_to_one_idle_account()
    test_only_up_to_num_accounts_trade_per_day()
    test_reset_day_frees_all_accounts()
    test_check_exits_resolves_target_hit()
    test_chart_renderer_hook_is_called_and_passed_through()
    test_restart_mid_day_does_not_let_same_account_retrade()
    test_same_move_retrigger_is_skipped_but_genuine_extension_is_not()
    test_idle_account_prioritizes_untraded_then_highest_then_lowest()
    test_net_dollars_persists_across_a_restart_and_a_new_day()
    print("All tests passed.")
