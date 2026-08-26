import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.config import load_config
from jj_bot.live_runner_topstepx import TopstepXLiveRunner
from jj_bot.models import Direction, Phase, Signal, SetupGrade
from jj_bot.topstepx_client import Account


def _runner(cfg):
    runner = TopstepXLiveRunner.__new__(TopstepXLiveRunner)
    runner.cfg = cfg
    runner.client = MagicMock()
    runner.dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
    return runner


def _signal(cfg, direction, entry_price=29000.0):
    if direction == Direction.LONG:
        stop, target = entry_price - cfg.risk.stop_points, entry_price + cfg.risk.target_points
    else:
        stop, target = entry_price + cfg.risk.stop_points, entry_price - cfg.risk.target_points
    return Signal(
        timestamp=datetime.now(), direction=direction, entry_price=entry_price,
        stop_price=stop, target_price=target, phase=Phase.CONTINUATION, grade=SetupGrade.A, reason="test",
    )


def test_funded_stage_keeps_normal_stop_but_bigger_target():
    """Per explicit user request: once balance is at/past the pass line,
    risk the normal amount but aim for funded_target_dollars instead of
    the static target."""
    cfg = load_config()
    cfg.risk.eval_scale_down_enabled = True
    cfg.risk.funded_target_dollars = 4000.0
    runner = _runner(cfg)

    account = Account(id=1, name="ACCT1")
    pass_line = cfg.topstep_eval.account_size + cfg.topstep_eval.profit_target
    runner.client.get_live_balances.return_value = [Account(id=1, name="ACCT1", balance=pass_line + 500)]

    signal = _signal(cfg, Direction.LONG)
    stop_price, target_price = runner._scaled_stop_target(signal, account)

    assert stop_price == signal.stop_price, "stop must stay at the normal size for funded-stage trades"

    dollar_per_point = runner.dollar_per_point * cfg.risk.contracts_per_trade
    tick = cfg.instrument.tick_size
    expected_target_points = max(tick, round((cfg.risk.funded_target_dollars / dollar_per_point) / tick) * tick)
    assert target_price == pytest.approx(signal.entry_price + expected_target_points)
    # Confirm it's genuinely bigger than the static target, not just different.
    assert target_price > signal.target_price


def test_funded_stage_target_direction_short():
    cfg = load_config()
    cfg.risk.eval_scale_down_enabled = True
    cfg.risk.funded_target_dollars = 4000.0
    runner = _runner(cfg)

    account = Account(id=1, name="ACCT1")
    pass_line = cfg.topstep_eval.account_size + cfg.topstep_eval.profit_target
    runner.client.get_live_balances.return_value = [Account(id=1, name="ACCT1", balance=pass_line + 500)]

    signal = _signal(cfg, Direction.SHORT)
    stop_price, target_price = runner._scaled_stop_target(signal, account)

    assert stop_price == signal.stop_price
    assert target_price < signal.entry_price
    assert target_price < signal.target_price, "funded target must extend further than the static short target"


def test_not_yet_close_to_pass_line_uses_normal_size():
    """Regression guard: balance far below the pass line must still take
    the normal-size trade, not something scaled or funded-sized."""
    cfg = load_config()
    cfg.risk.eval_scale_down_enabled = True
    runner = _runner(cfg)

    account = Account(id=1, name="ACCT1")
    pass_line = cfg.topstep_eval.account_size + cfg.topstep_eval.profit_target
    dollar_per_point = runner.dollar_per_point * cfg.risk.contracts_per_trade
    normal_target_dollars = cfg.risk.target_points * dollar_per_point
    runner.client.get_live_balances.return_value = [
        Account(id=1, name="ACCT1", balance=pass_line - normal_target_dollars - 1000)
    ]

    signal = _signal(cfg, Direction.LONG)
    stop_price, target_price = runner._scaled_stop_target(signal, account)

    assert stop_price == signal.stop_price
    assert target_price == signal.target_price


if __name__ == "__main__":
    test_funded_stage_keeps_normal_stop_but_bigger_target()
    test_funded_stage_target_direction_short()
    test_not_yet_close_to_pass_line_uses_normal_size()
    print("All tests passed.")
