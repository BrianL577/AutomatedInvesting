"""Simulates Topstep's real eval/funded economics day-by-day, fed one day's
net $ P&L at a time from a live/paper trading run — so you can watch how
today's actual results would translate to a real Topstep account before
ever connecting one for real money.

Mirrors dashboard/lib/backtester.ts's walkAccountEconomics() rule-for-rule.
Keep both in sync if Topstep changes its rules — see config.yaml for where
these numbers came from and what's still unconfirmed.

DEAD CODE (as of 2026-08-26): TopstepEvalSimulator is not imported or
instantiated anywhere outside tests/test_topstep_eval_sim.py. Nothing in
live_runner_topstepx.py, run_virtual_practice.py, or api_server.py calls
record_day() on it. The logic here is correct and tested, but it has zero
effect on the running bot or dashboard right now — the dashboard's own
Eval Simulator (dashboard/lib/trades.ts's simulateAccounts()) is the live,
user-visible equivalent of this module.

Intended use (never wired in): instantiate one of these per real/virtual
account inside the live runner, call record_day(daily_pnl) at end-of-day,
so jj_bot.log gets a live eval/funded/payout narration alongside the real
trades. Left unwired deliberately — do NOT delete this file or "clean it
up" without checking with the user first, and do NOT wire it into
live_runner_topstepx.py (the real-money order-placing path) without
explicit user sign-off, since that adds new code to the one process where
bugs cost real money.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jj_bot.topstep_sim")

_STATE_FIELDS = (
    "attempts_bought", "funded_count", "funded_attempts_with_payout", "fees_paid", "cash_payouts",
    "_eval_days_since_bill", "_needs_fresh_subscription", "_first_attempt",
    "balance", "high_water", "floor", "funded", "best_day_so_far",
    "effective_profit_target", "winning_days_since_payout", "profit_since_payout",
    "days_since_payout", "best_day_since_payout", "_this_attempt_got_payout",
    "_balance_at_last_payout", "_current_monthly_fee", "_current_activation_fee",
)


@dataclass
class TopstepEvalSimConfig:
    account_size: float = 50000
    profit_target: float = 3000
    trailing_max_drawdown: float = 2000
    # PROMO PRICING — Topstep's current pricing page (50K, Daily Loss Limit
    # account) shows $85/mo (promo, may change) for this account type, $85
    # reset fee on a bust (mirrors the monthly price), Express Funded
    # Activation Fee is FREE. Topstep no longer offers a separate
    # cheaper-monthly/pricier-activation plan — both "plans" below are set
    # identically so the plan-switch logic (harmless if unused) can't
    # diverge from real cost. Easy single-line edits here if Topstep
    # changes pricing — check their current page periodically.
    eval_fee: float = 85
    reactivation_fee: float = 85
    monthly_fee: float = 85
    trading_days_per_month: int = 21
    activation_fee: float = 0
    no_activation_fee_monthly_fee: float = 85
    pass_rate_switch_threshold: float = 0.0
    # Funded-stage payout math. Only the Standard path (Option 1) is
    # modeled — the Consistency path (Option 2) is deliberately not
    # simulated, per explicit user choice.
    payout_share: float = 0.9
    # PROMO VALUE — Topstep's page currently shows a promotional payout cap
    # of $4,000 (base $2,000). Update this single line if the promo changes.
    max_payout_per_event: float = 4000
    max_payout_balance_share: float = 0.5
    min_winning_days_for_payout: int = 5
    min_winning_day_profit: float = 150
    consistency_path_min_days: int = 3
    consistency_path_max_best_day_share: float = 0.4


class TopstepEvalSimulator:
    """Call record_day(net_dollar_pnl) once per completed trading day. Logs
    every state transition (bust/fund/payout/fee) so it's visible in
    jj_bot.log alongside the real trades that drove it."""

    def __init__(self, cfg: TopstepEvalSimConfig, label: str = "", state_path: Optional[Path] = None):
        self.cfg = cfg
        self.label = label
        self.state_path = state_path
        self.attempts_bought = 0
        self.funded_count = 0
        self.funded_attempts_with_payout = 0
        self.fees_paid = 0.0
        self.cash_payouts = 0.0
        self._eval_days_since_bill = cfg.trading_days_per_month  # bill immediately
        self._needs_fresh_subscription = False
        self._first_attempt = True

        if self.state_path is not None and self._load_state():
            logger.info(
                "%s Resumed from saved state: attempt #%d, balance=$%.2f, funded=%s",
                self._tag(), self.attempts_bought, self.balance, self.funded,
            )
        else:
            self._start_new_attempt()
            self._save_state()

    def _tag(self) -> str:
        return f"[TopstepSim{f' {self.label}' if self.label else ''}]"

    def _load_state(self) -> bool:
        """Restores state saved by a previous run (e.g. before a Task
        Scheduler crash-recovery restart) so multi-day progress toward a
        real eval isn't silently lost on every restart. Returns False (and
        leaves nothing set) if there's no valid saved state to resume."""
        try:
            if not self.state_path.exists():
                return False
            data = json.loads(self.state_path.read_text())
            # Validate every field is present before mutating self — an old
            # state file missing newly-added fields must leave self
            # untouched (truly fresh), not partially applied.
            values = {field_name: data[field_name] for field_name in _STATE_FIELDS}
            for field_name, value in values.items():
                setattr(self, field_name, value)
            return True
        except Exception:
            logger.warning("%s Could not load saved state from %s — starting fresh.", self._tag(), self.state_path)
            return False

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        try:
            data = {field_name: getattr(self, field_name) for field_name in _STATE_FIELDS}
            self.state_path.write_text(json.dumps(data))
        except Exception:
            logger.warning("%s Could not save state to %s.", self._tag(), self.state_path)

    def _start_new_attempt(self) -> None:
        self.attempts_bought += 1
        self.fees_paid += self.cfg.eval_fee if self._first_attempt else self.cfg.reactivation_fee
        self._first_attempt = False
        if self._needs_fresh_subscription:
            self._eval_days_since_bill = self.cfg.trading_days_per_month
            self._needs_fresh_subscription = False

        # Which pricing plan applies to THIS attempt, based on the
        # empirical pass rate from every attempt before it.
        attempts_before_this = self.attempts_bought - 1
        empirical_pass_rate = self.funded_count / attempts_before_this if attempts_before_this > 0 else 0.0
        using_no_activation_plan = empirical_pass_rate >= self.cfg.pass_rate_switch_threshold
        self._current_monthly_fee = self.cfg.no_activation_fee_monthly_fee if using_no_activation_plan else self.cfg.monthly_fee
        self._current_activation_fee = 0.0 if using_no_activation_plan else self.cfg.activation_fee

        self.balance = self.cfg.account_size
        self.high_water = self.balance
        self.floor = self.balance - self.cfg.trailing_max_drawdown
        self.funded = False
        self.best_day_so_far = 0.0
        self.effective_profit_target = self.cfg.profit_target
        self.winning_days_since_payout = 0
        self.profit_since_payout = 0.0
        self.days_since_payout = 0
        self.best_day_since_payout = float("-inf")
        self._this_attempt_got_payout = False
        self._balance_at_last_payout = 0.0

        plan_name = "No-Activation-Fee" if self._current_activation_fee == 0 else "Standard"
        logger.info(
            "%s Attempt #%d started (%s plan, $%.2f/mo + $%.2f activation): balance=$%.2f target=$%.2f "
            "floor=$%.2f (fees paid so far: $%.2f)",
            self._tag(), self.attempts_bought, plan_name, self._current_monthly_fee, self._current_activation_fee,
            self.balance, self.cfg.profit_target, self.floor, self.fees_paid,
        )

    def record_day(self, day_pnl: float) -> None:
        if not self.funded:
            self._eval_days_since_bill += 1
            if self._eval_days_since_bill >= self.cfg.trading_days_per_month:
                self.fees_paid += self._current_monthly_fee
                self._eval_days_since_bill = 0
                logger.info(
                    "%s Monthly subscription charged: $%.2f (total fees paid: $%.2f)",
                    self._tag(), self._current_monthly_fee, self.fees_paid,
                )

        self.balance += day_pnl
        if self.balance <= self.floor:
            logger.warning(
                "%s BUSTED: balance $%.2f <= floor $%.2f. Starting a new attempt.",
                self._tag(), self.balance, self.floor,
            )
            if self.funded:
                self._needs_fresh_subscription = True
            self._start_new_attempt()
            self._save_state()
            return

        if self.balance > self.high_water:
            self.high_water = self.balance
            self.floor = min(self.high_water - self.cfg.trailing_max_drawdown, self.cfg.account_size)

        if not self.funded and day_pnl > self.best_day_so_far:
            self.best_day_so_far = day_pnl
            self.effective_profit_target = max(self.cfg.profit_target, self.best_day_so_far / 0.5)

        if not self.funded and self.balance >= self.cfg.account_size + self.effective_profit_target:
            self.funded = True
            self.funded_count += 1
            self.fees_paid += self._current_activation_fee
            # CONFIRMED via TopStep's own support: the funded (Express
            # Funded) account starts at $0 balance -- eval/Combine profit
            # does NOT carry over, only the pass itself does. Account size
            # (buying power) is unchanged; balance and the trailing
            # drawdown floor both reset here exactly like _start_new_attempt
            # does above, not just at an actual payout.
            self.balance = self.cfg.account_size
            self.high_water = self.balance
            self.floor = self.balance - self.cfg.trailing_max_drawdown
            self.winning_days_since_payout = 0
            self.profit_since_payout = 0.0
            self.days_since_payout = 0
            self.best_day_since_payout = float("-inf")
            # A payout removes the requested amount from balance; the NEXT
            # payout requires balance to climb back ABOVE this reference,
            # not merely back to it. Starts at the (now-reset) funding
            # balance itself.
            self._balance_at_last_payout = self.balance
            logger.info(
                "%s FUNDED! (attempt #%d, cleared $%.2f target) Balance reset to $%.2f. Total fees paid so far: $%.2f",
                self._tag(), self.attempts_bought, self.effective_profit_target, self.balance, self.fees_paid,
            )

        if self.funded:
            self.profit_since_payout += day_pnl
            self.days_since_payout += 1
            if day_pnl > self.best_day_since_payout:
                self.best_day_since_payout = day_pnl
            if day_pnl >= self.cfg.min_winning_day_profit:
                self.winning_days_since_payout += 1

            # Standard path only (Option 1) — per explicit user choice, the
            # Consistency path (Option 2) is deliberately not modeled even
            # though Topstep offers it.
            standard_eligible = self.winning_days_since_payout >= self.cfg.min_winning_days_for_payout
            # Confirmed directly: eligibility ALSO requires balance to be
            # strictly above the post-previous-payout reference, not just
            # hitting the day-count criteria.
            above_last_payout_balance = self.balance > self._balance_at_last_payout

            if standard_eligible and above_last_payout_balance:
                payout = max(0.0, min(
                    self.cfg.max_payout_per_event,
                    self.profit_since_payout * self.cfg.payout_share,
                    self.balance * self.cfg.max_payout_balance_share,
                ))
                path = "Standard"
                if payout > 0:
                    self.cash_payouts += payout
                    # Confirmed directly: the payout amount is REMOVED from
                    # the account balance immediately, not just a
                    # drawdown-buffer reset — e.g. request $2,000 on a
                    # $4,000 balance leaves $2,000.
                    self.balance -= payout
                    self._balance_at_last_payout = self.balance
                    if not self._this_attempt_got_payout:
                        self.funded_attempts_with_payout += 1
                        self._this_attempt_got_payout = True
                    # Real rule: Maximum Loss Limit resets to $0 the moment
                    # funds are withdrawn.
                    self.floor = self.balance
                    self.high_water = self.balance
                    logger.info(
                        "%s PAYOUT via %s path: $%.2f (total payouts: $%.2f, net real money: $%.2f)",
                        self._tag(), path, payout, self.cash_payouts, self.cash_payouts - self.fees_paid,
                    )
                self.winning_days_since_payout = 0
                self.profit_since_payout = 0.0
                self.days_since_payout = 0
                self.best_day_since_payout = float("-inf")

        logger.info(
            "%s Day close: pnl=$%.2f balance=$%.2f floor=$%.2f funded=%s target=$%.2f | "
            "net real money so far: $%.2f (payouts $%.2f - fees $%.2f) | "
            "eval pass rate: %.1f%% (%d/%d) | funded payout rate: %.1f%% (%d/%d)",
            self._tag(), day_pnl, self.balance, self.floor, self.funded, self.effective_profit_target,
            self.cash_payouts - self.fees_paid, self.cash_payouts, self.fees_paid,
            self.eval_pass_rate, self.funded_count, self.attempts_bought,
            self.funded_payout_rate, self.funded_attempts_with_payout, self.funded_count,
        )
        self._save_state()

    @property
    def eval_pass_rate(self) -> float:
        """Evals WON / evals PURCHASED — e.g. purchased 10, passed 5 = 50%."""
        return (self.funded_count / self.attempts_bought * 100) if self.attempts_bought else 0.0

    @property
    def funded_payout_rate(self) -> float:
        """Of every time ever funded (including ones that later busted
        having withdrawn $0), what fraction ever cashed out at least one
        payout."""
        return (self.funded_attempts_with_payout / self.funded_count * 100) if self.funded_count else 0.0
