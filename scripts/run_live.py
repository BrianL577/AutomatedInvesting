#!/usr/bin/env python3
"""Run the strategy live against a trading account.

Broker is selected via BROKER env var (or config.broker) — "ibkr" (default,
free paper trading via TWS/IB Gateway), "topstepx" (TopStep's own platform —
what a real TopStep account actually runs on; no demo, requires
TOPSTEPX_ALLOW_LIVE=true), or "tradovate" (legacy, only for a Tradovate
account opened outside TopStep; demo by default, or live with
TRADOVATE_ALLOW_LIVE=true).

Usage:
    python scripts/run_live.py --symbol NQ
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.alerts import send_crash_alert_for_exception
from jj_bot.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None, help="Override instrument symbol (default from config.yaml)")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.symbol:
        cfg.instrument.symbol = args.symbol

    if cfg.broker == "ibkr":
        from jj_bot.live_runner_ibkr import IBKRLiveRunner

        runner = IBKRLiveRunner(cfg)
    elif cfg.broker == "topstepx":
        from jj_bot.live_runner_topstepx import TopstepXLiveRunner

        runner = TopstepXLiveRunner(cfg)
    elif cfg.broker == "tradovate":
        from jj_bot.live_runner import LiveRunner

        runner = LiveRunner(cfg)
    else:
        raise SystemExit(f"Unknown BROKER '{cfg.broker}'. Use 'ibkr', 'topstepx', or 'tradovate'.")

    runner.start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except BaseException as exc:
        send_crash_alert_for_exception("scripts/run_live.py main()", exc)
        raise
