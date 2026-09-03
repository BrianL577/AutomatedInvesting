#!/usr/bin/env python3
"""Replays the strategy (old vs. new break_buffer_points) against real
recent 1-min bar data pulled from Supabase's public `bars` table, so a
config change can be checked against actual historical price action instead
of just waiting to see what happens live.

Requires SUPABASE_URL and an anon/service key (same as
diagnose_losing_streak.py) -- `bars` has a public read policy.

Usage:
    python scripts/backtest_recent_bars.py [--days 14]
"""
from __future__ import annotations

import argparse
import dataclasses
import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from jj_bot.backtest import run_backtest
from jj_bot.config import load_config
from jj_bot.models import Bar
from jj_bot.time_utils import to_et

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or ""


def fetch_bars(since: datetime) -> list[Bar]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("SUPABASE_URL and an anon/service key must be set.")
    all_rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/bars",
            params={
                "select": "*",
                "order": "t.asc",
                "t": f"gte.{since.isoformat()}",
                "limit": page_size,
                "offset": offset,
            },
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json()
        all_rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    bars = [
        Bar(
            timestamp=to_et(datetime.fromisoformat(row["t"].replace("Z", "+00:00")), "America/New_York"),
            open=float(row["o"]), high=float(row["h"]), low=float(row["l"]), close=float(row["c"]),
            volume=float(row.get("v") or 0.0),
        )
        for row in all_rows
    ]
    bars.sort(key=lambda b: b.timestamp)
    return bars


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    bars = fetch_bars(since)
    print(f"Fetched {len(bars)} bar(s) since {since.date()}.\n")
    if not bars:
        print("No bars in this window -- nothing to backtest.")
        return

    cfg_new = load_config()  # picks up whatever's currently in config.yaml
    cfg_old = dataclasses.replace(cfg_new, strategy=dataclasses.replace(cfg_new.strategy, break_buffer_points=1.0))

    for label, cfg in (("OLD (break_buffer_points=1.0)", cfg_old), ("NEW (break_buffer_points=%.1f)" % cfg_new.strategy.break_buffer_points, cfg_new)):
        report = run_backtest(cfg, bars, log_trades=False)
        wins = sum(1 for t in report.trades if t.win)
        total = len(report.trades)
        win_rate = wins / total * 100 if total else 0.0
        net_dollars = sum(t.pnl_points for t in report.trades) * (cfg.instrument.tick_value / cfg.instrument.tick_size) * cfg.risk.contracts_per_trade
        print(f"=== {label} ===")
        print(f"Trades taken: {total}  |  Wins: {wins} ({win_rate:.1f}%)  |  Net: ${net_dollars:,.2f}")
        if report.incomplete_trades:
            print(f"(Excluded {report.incomplete_trades} unresolved trade(s) still open at data end.)")
        for t in report.trades:
            print(
                f"  {t.signal.timestamp:%Y-%m-%d %H:%M} {t.signal.direction.value:5s} entry={t.signal.entry_price:.2f} "
                f"exit={t.exit_price:.2f} pts={t.pnl_points:+.2f} {'WIN' if t.win else 'loss'}"
            )
        print()


if __name__ == "__main__":
    main()
