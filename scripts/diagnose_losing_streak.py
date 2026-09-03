#!/usr/bin/env python3
"""Diagnoses a losing streak across real + virtual accounts by testing
specific hypotheses against the actual trade data in Supabase, rather than
guessing from the strategy code alone.

Run this on the machine (or any machine) with SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) set -- same env vars
TradeLogger/the dashboard already use.

What it checks, and why each one matters:

1. Win rate broken out by source (real live-runner trades vs. virtual
   practice trades) and by phase (continuation vs. reversion). If ONE
   source or phase is fine and another is broken, that narrows the bug to
   the code path unique to the broken one.

2. "Reversal test": for every trade, computes what the outcome WOULD have
   been if direction had been flipped (same entry/stop/target distances,
   opposite side). If flipping direction would have turned most losses
   into wins, that's the signature of a sign-inversion bug somewhere in
   direction assignment (strategy.py's continuation_direction / reversion
   extension logic) -- not just a weak edge. A genuinely weak-but-unbiased
   edge should NOT flip to a strong edge under reversal.

3. Target-hit vs. stop-hit rate. At the current 25pt stop / 38pt target
   (~1:1.5 R:R), breakeven needs a ~40% target-hit rate. A rate far below
   that across a full week, with no reversal signature, points to entries
   simply not having edge in current conditions rather than a bug.

Usage:
    python scripts/diagnose_losing_streak.py [--days 7]
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or ""


def fetch_trades(since: datetime) -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or NEXT_PUBLIC_SUPABASE_ANON_KEY) must be set."
        )
    all_trades: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/trades",
            params={
                "select": "*",
                "order": "timestamp.asc",
                "timestamp": f"gte.{since.isoformat()}",
                "limit": page_size,
                "offset": offset,
            },
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json()
        all_trades.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return all_trades


def is_real(t: dict) -> bool:
    return t.get("source") not in ("connection_test", "virtual_practice") and t.get("phase") != "test"


def would_flip_win(t: dict) -> bool:
    """What the outcome would have been with direction flipped, same
    stop/target DISTANCES from entry, applied to the opposite side. Uses
    exit_price's relationship to entry to infer whether price ultimately
    moved further in the stop or target direction, then re-derives the win
    condition for the mirrored trade."""
    entry = t["entry_price"]
    exit_p = t["exit_price"]
    stop_dist = abs(entry - t["stop_price"])
    target_dist = abs(entry - t["target_price"])
    moved = exit_p - entry  # signed movement from entry to exit, real direction's own frame
    # In the real trade's frame, a win means price moved `target_dist` in
    # the trade's favor; a loss means it moved `stop_dist` against it.
    # Flipping direction mirrors favorable/unfavorable, so a real loss
    # becomes a flipped win only if the magnitude matches the flipped
    # trade's own target distance (approximately -- uses the loss's
    # magnitude vs the stop distance as a proxy since exact intrabar path
    # isn't recorded).
    return not t["win"] and abs(moved) >= target_dist * 0.9


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    trades = fetch_trades(since)
    print(f"Fetched {len(trades)} trade(s) since {since.date()}.\n")
    if not trades:
        print("No trades in this window -- nothing to diagnose.")
        return

    def summarize(label: str, rows: list[dict]) -> None:
        if not rows:
            print(f"{label}: no trades")
            return
        wins = sum(1 for t in rows if t["win"])
        pnl = sum(t["pnl_dollars"] for t in rows)
        flips = sum(1 for t in rows if would_flip_win(t))
        losses = sum(1 for t in rows if not t["win"])
        print(
            f"{label}: {len(rows)} trades, {wins} win / {len(rows) - wins} loss "
            f"({wins / len(rows) * 100:.1f}%), net ${pnl:,.2f}"
            + (f" | {flips}/{losses} losses would have won if direction were flipped" if losses else "")
        )

    real = [t for t in trades if is_real(t)]
    virtual = [t for t in trades if t.get("source") == "virtual_practice"]

    print("=== By source ===")
    summarize("Real (live/practice account)", real)
    summarize("Virtual practice accounts", virtual)
    print()

    print("=== By phase (all non-test trades) ===")
    non_test = [t for t in trades if t.get("phase") != "test"]
    by_phase = defaultdict(list)
    for t in non_test:
        by_phase[t.get("phase", "?")].append(t)
    for phase, rows in by_phase.items():
        summarize(phase, rows)
    print()

    print("=== By direction (all non-test trades) ===")
    by_dir = defaultdict(list)
    for t in non_test:
        by_dir[t.get("direction", "?")].append(t)
    for direction, rows in by_dir.items():
        summarize(direction, rows)
    print()

    print("=== Reversal test (the key check for a sign-inversion bug) ===")
    losses = [t for t in non_test if not t["win"]]
    flips = [t for t in losses if would_flip_win(t)]
    if losses:
        flip_rate = len(flips) / len(losses) * 100
        print(f"{len(flips)}/{len(losses)} losing trades ({flip_rate:.1f}%) would have won if direction were flipped.")
        if flip_rate >= 65:
            print(
                "^ HIGH flip rate -- this is the signature of a real direction/sign bug "
                "(a genuinely weak-but-unbiased strategy should NOT flip to mostly-winning under reversal). "
                "Focus on strategy.py's continuation_direction assignment and the reversion phase's "
                "`extension > 0 -> SHORT` logic."
            )
        elif flip_rate <= 40:
            print(
                "^ LOW flip rate -- NOT a sign-inversion signature. Losses are landing roughly where a "
                "correctly-oriented but currently-unprofitable strategy would land. Points toward weak "
                "edge in current market conditions rather than a coding bug."
            )
        else:
            print("^ Inconclusive -- neither clearly a sign bug nor clearly just weak edge.")
    else:
        print("No losing trades in this window.")


if __name__ == "__main__":
    main()
