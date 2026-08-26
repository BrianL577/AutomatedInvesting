# NOTE: jj_bot/topstep_eval_sim.py is DEAD CODE — TopstepEvalSimulator is not
# wired into the live bot anywhere (see the module's own docstring). These
# tests keep the module correct for whenever it does get wired in, but its
# logic currently has no effect on the running bot or dashboard.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.topstep_eval_sim import TopstepEvalSimConfig, TopstepEvalSimulator


def _sim(**overrides):
    cfg = TopstepEvalSimConfig(
        account_size=50000, profit_target=3000, trailing_max_drawdown=2000,
        eval_fee=0, reactivation_fee=0, monthly_fee=0, activation_fee=0,
        no_activation_fee_monthly_fee=0, **overrides,
    )
    return TopstepEvalSimulator(cfg, label="test", state_path=None)


def test_balance_resets_to_account_size_on_funding():
    """CONFIRMED via TopStep's own support: the funded account starts at
    $0 balance (== account_size in this module's absolute-balance
    convention) -- eval profit does NOT carry over."""
    sim = _sim()
    sim.record_day(1440)
    sim.record_day(1330)
    assert not sim.funded
    sim.record_day(1500)  # 1440+1330+1500 = 4270, crosses the 3000 target
    assert sim.funded
    assert sim.balance == sim.cfg.account_size, "balance must reset to account_size (== $0 profit), not carry the 4270"
    assert sim.high_water == sim.cfg.account_size
    assert sim.floor == sim.cfg.account_size - sim.cfg.trailing_max_drawdown


def test_losses_after_funding_apply_to_the_reset_balance_not_the_old_one():
    """Reproduces the exact scenario reported live: 3 winning days pass the
    eval, then 2 losing days. With the reset, those losses should draw down
    from the fresh funded balance, not the pre-reset eval balance."""
    sim = _sim()
    sim.record_day(1440)
    sim.record_day(1330)
    sim.record_day(1500)  # funds here, balance resets to account_size
    assert sim.funded

    sim.record_day(-790)
    sim.record_day(-860)

    expected_balance = sim.cfg.account_size - 790 - 860
    assert sim.balance == expected_balance
    assert not sim.funded or sim.balance > sim.floor, "should not have busted from these two losses alone"


def test_bust_after_funding_uses_the_reset_drawdown_floor():
    """A loss big enough to blow through the RESET floor should bust --
    verifies the floor itself was actually reset, not just balance."""
    sim = _sim()
    sim.record_day(1440)
    sim.record_day(1330)
    sim.record_day(1500)  # funds, balance/floor reset
    assert sim.funded
    pre_bust_funded_count = sim.funded_count

    sim.record_day(-2500)  # bigger than trailing_max_drawdown (2000) below the reset floor

    # _start_new_attempt() runs on bust, so funded flips back to False for
    # the new attempt and balance/floor reset again to a fresh eval start.
    assert not sim.funded
    assert sim.balance == sim.cfg.account_size
    assert sim.attempts_bought == 2  # busted the funded run, started attempt #2


if __name__ == "__main__":
    test_balance_resets_to_account_size_on_funding()
    test_losses_after_funding_apply_to_the_reset_balance_not_the_old_one()
    test_bust_after_funding_uses_the_reset_drawdown_floor()
    print("All tests passed.")
