#!/usr/bin/env python3
"""One-time backfill: finds trades that are recorded locally
(dashboard/data/trades.json — TradeLogger always writes here first,
independent of Supabase) but missing from Supabase's `trades` table, and
inserts exactly those. Safe to re-run: never touches a row that's already
in Supabase, only adds ones that aren't there.

Exists because a trade can log successfully locally while its Supabase
write fails for a reason that has nothing to do with the trade itself —
e.g. the trades table was missing a column (see
supabase/migrations/003_add_trade_chart_path.sql) — so the dashboard
silently never showed it, with no error visible anywhere except a
"Failed to write trade to Supabase" WARNING line in jj_bot.log.

Usage:
    python scripts/backfill_supabase_trades.py            # dry run: shows what would be inserted
    python scripts/backfill_supabase_trades.py --apply     # actually inserts the missing rows
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_TRADES_PATH = REPO_ROOT / "dashboard" / "data" / "trades.json"

# Fields this uniquely identifies a trade by, for deduping against what's
# already in Supabase. A given account can't close two different trades
# with the exact same entry timestamp, exit timestamp, and account name.
DEDUPE_KEYS = ("timestamp", "exit_timestamp", "account_name")


def _parse_ts(value):
    """CONFIRMED: comparing raw timestamp strings was wrong — Postgres can
    round-trip a timestamptz back through PostgREST in a different string
    form than what Python originally sent (different offset notation,
    added/dropped fractional seconds) for the exact same instant, which
    made already-present trades get wrongly flagged as missing. Parsing to
    an aware datetime and comparing those compares the actual moment in
    time, not the text representation of it."""
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

# The exact set of columns the Supabase `trades` table has (see
# supabase/schema.sql + migrations/003_add_trade_chart_path.sql) — local
# trades.json rows also carry a local-only `id`, which must NOT be sent
# (Supabase generates its own bigint identity for that column).
SUPABASE_COLUMNS = (
    "timestamp", "exit_timestamp", "phase", "direction", "grade", "reason",
    "entry_price", "exit_price", "stop_price", "target_price", "win",
    "pnl_points", "pnl_dollars", "source", "account_name", "logged_at",
    "chart_path",
)


def _dedupe_key(trade: dict) -> tuple:
    return tuple(_parse_ts(trade.get(k)) if "timestamp" in k else trade.get(k) for k in DEDUPE_KEYS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually insert the missing rows (default is a dry run that only prints what it would do)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not supabase_url or not supabase_key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set in .env — nothing to backfill against.", file=sys.stderr)
        raise SystemExit(1)

    if not LOCAL_TRADES_PATH.exists():
        print(f"Local trades file not found: {LOCAL_TRADES_PATH}", file=sys.stderr)
        raise SystemExit(1)
    local_trades = json.loads(LOCAL_TRADES_PATH.read_text())
    print(f"Loaded {len(local_trades)} trade(s) from {LOCAL_TRADES_PATH}")

    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    existing_keys: set[tuple] = set()
    offset = 0
    page_size = 1000
    while True:
        resp = requests.get(
            f"{supabase_url}/rest/v1/trades",
            params={"select": ",".join(DEDUPE_KEYS), "limit": page_size, "offset": offset},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json()
        existing_keys.update(_dedupe_key(row) for row in page)
        if len(page) < page_size:
            break
        offset += page_size
    print(f"Found {len(existing_keys)} trade(s) already in Supabase.")

    missing = [t for t in local_trades if _dedupe_key(t) not in existing_keys]
    if not missing:
        print("Nothing to backfill — every local trade is already in Supabase.")
        return

    print(f"\n{len(missing)} trade(s) missing from Supabase:")
    for t in missing:
        print(f"  {t.get('timestamp')}  {t.get('account_name')}  {t.get('direction')}  "
              f"win={t.get('win')}  pnl=${t.get('pnl_dollars')}")

    if not args.apply:
        print("\nDry run only — nothing was inserted. Re-run with --apply to actually backfill these.")
        return

    print("\nInserting...")
    inserted, failed = 0, 0
    for t in missing:
        record = {k: t.get(k) for k in SUPABASE_COLUMNS}
        resp = requests.post(
            f"{supabase_url}/rest/v1/trades",
            json=record,
            headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
            timeout=15,
        )
        if resp.ok:
            inserted += 1
        else:
            failed += 1
            print(f"  FAILED to insert {t.get('timestamp')} / {t.get('account_name')}: {resp.status_code} {resp.text}")

    print(f"\nDone: inserted {inserted}, failed {failed}.")


if __name__ == "__main__":
    main()
