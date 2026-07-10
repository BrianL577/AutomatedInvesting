#!/usr/bin/env python3
"""Run the prop-firm-style backtest against a CSV of 1-minute bars.

Usage:
    python scripts/run_backtest.py --bars data/NQ_1min.csv
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.backtest import load_bars_csv, run_backtest, print_report
from jj_bot.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", required=True, help="CSV with timestamp,open,high,low,close,volume")
    parser.add_argument("--config", default=None, help="Path to config.yaml (defaults to repo root)")
    parser.add_argument("--account-size", type=float, default=None, help="Override eval account size")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.account_size:
        cfg.topstep_eval.account_size = args.account_size

    bars = load_bars_csv(args.bars, cfg.strategy.timezone)
    if not bars:
        print("No bars loaded.")
        return

    report = run_backtest(cfg, bars)
    print_report(report, cfg)


if __name__ == "__main__":
    main()
