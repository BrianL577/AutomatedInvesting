#!/usr/bin/env python3
"""Run the strategy in "practice mode": N abstract virtual accounts, each
taking its own distinct trade out of the NY-session strategy instead of one
shared trade cloned across every account (what scripts/run_live.py does).
No real order is ever placed by this process — it only reads live TopstepX
market data — so it's safe to run alongside scripts/run_live.py with no
extra setup beyond what run_live.py already needs.

Use this to sanity-check "does the strategy actually work across multiple
accounts" before increasing size on the real strategy.

Usage:
    python scripts/run_virtual_practice.py --accounts 10
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.alerts import send_crash_alert_for_exception
from jj_bot.config import load_config
from jj_bot.live_runner_topstepx_virtual import TopstepXVirtualPracticeRunner
from jj_bot.live_runner_yahoo_virtual import YahooVirtualPracticeRunner


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None, help="Override instrument symbol (default from config.yaml)")
    parser.add_argument("--accounts", type=int, default=None, help="Number of virtual accounts (default from config.yaml virtual_accounts.count)")
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--feed", choices=["topstepx", "yahoo"], default=None,
        help="Market data source. 'yahoo' = free delayed NQ data, needs NO TopStep login or API "
             "subscription (fine for virtual accounts). Default: topstepx, or env VIRTUAL_FEED.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.symbol:
        cfg.instrument.symbol = args.symbol

    feed = (args.feed or os.getenv("VIRTUAL_FEED", "topstepx")).strip().lower()
    if feed == "yahoo":
        YahooVirtualPracticeRunner(cfg, num_accounts=args.accounts).start()
        return

    if cfg.broker != "topstepx":
        raise SystemExit(
            f"BROKER is '{cfg.broker}', but scripts/run_virtual_practice.py only supports "
            "TopstepX practice mode — set BROKER=topstepx in .env."
        )

    runner = TopstepXVirtualPracticeRunner(cfg, num_accounts=args.accounts)
    runner.start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except BaseException as exc:
        send_crash_alert_for_exception("scripts/run_virtual_practice.py main()", exc)
        raise
