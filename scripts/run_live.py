#!/usr/bin/env python3
"""Run the strategy live against a Tradovate DEMO account.

Usage:
    python scripts/run_live.py --symbol NQ
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.config import load_config
from jj_bot.live_runner import LiveRunner


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None, help="Override instrument symbol (default from config.yaml)")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.symbol:
        cfg.instrument.symbol = args.symbol

    if cfg.tradovate.env != "demo":
        raise SystemExit("TRADOVATE_ENV must be 'demo'. Refusing to start.")

    runner = LiveRunner(cfg)
    runner.start()


if __name__ == "__main__":
    main()
