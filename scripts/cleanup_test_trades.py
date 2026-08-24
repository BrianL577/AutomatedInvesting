#!/usr/bin/env python3
"""One-time cleanup: removes synthetic test-fixture trades that leaked into
the real trade log (local dashboard/data/trades.json and/or Supabase's
`trades` table) — e.g. two "Virtual-01" rows dated Jan 1 with entry/exit
prices around 93-95, which match tests/test_virtual_accounts.py's bar
generator almost exactly. No real trading day is ever Jan 1 for this bot
(it didn't exist then), so that date is an unambiguous fingerprint for
leaked test data, not a real trade.

Deliberately narrow and conservative: only ever matches on this specific
fingerprint (entry timestamp's month/day == Jan 1). Never touches anything
else, regardless of source/account_name/price.

Usage:
    python scripts/cleanup_test_trades.py            # dry run: shows what would be deleted
    python scripts/cleanup_test_trades.py --apply     # actually deletes the matched rows
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


def _is_leaked_test_trade(timestamp: str) -> bool:
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.month == 1 and dt.day == 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually delete the matched rows (default is a dry run)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # ---- local file ----
    if LOCAL_TRADES_PATH.exists():
        local_trades = json.loads(LOCAL_TRADES_PATH.read_text())
        leaked_local = [t for t in local_trades if _is_leaked_test_trade(t.get("timestamp", ""))]
        print(f"Local file: {len(leaked_local)} leaked test trade(s) of {len(local_trades)} total.")
        for t in leaked_local:
            print(f"  {t.get('timestamp')}  {t.get('account_name')}  entry={t.get('entry_price')}  exit={t.get('exit_price')}")
        if args.apply and leaked_local:
            kept = [t for t in local_trades if not _is_leaked_test_trade(t.get("timestamp", ""))]
            tmp_path = LOCAL_TRADES_PATH.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(kept, indent=2, default=str))
            tmp_path.replace(LOCAL_TRADES_PATH)
            print(f"  Removed {len(leaked_local)} row(s) from {LOCAL_TRADES_PATH}.")
    else:
        print(f"Local file not found: {LOCAL_TRADES_PATH} — skipping.")

    # ---- Supabase ----
    if not supabase_url or not supabase_key:
        print("\nSUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — skipping Supabase.")
        return

    headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
    resp = requests.get(
        f"{supabase_url}/rest/v1/trades",
        params={"select": "id,timestamp,account_name,entry_price,exit_price"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    all_rows = resp.json()
    leaked_remote = [r for r in all_rows if _is_leaked_test_trade(r.get("timestamp", ""))]
    print(f"\nSupabase: {len(leaked_remote)} leaked test trade(s) of {len(all_rows)} total.")
    for r in leaked_remote:
        print(f"  id={r.get('id')}  {r.get('timestamp')}  {r.get('account_name')}  entry={r.get('entry_price')}  exit={r.get('exit_price')}")

    if not args.apply:
        print("\nDry run only — nothing was deleted. Re-run with --apply to actually remove these.")
        return

    if not leaked_remote:
        print("\nNothing to delete in Supabase.")
        return

    print("\nDeleting from Supabase...")
    deleted, failed = 0, 0
    for r in leaked_remote:
        resp = requests.delete(
            f"{supabase_url}/rest/v1/trades",
            params={"id": f"eq.{r['id']}"},
            headers=headers,
            timeout=15,
        )
        if resp.ok:
            deleted += 1
        else:
            failed += 1
            print(f"  FAILED to delete id={r['id']}: {resp.status_code} {resp.text}")
    print(f"\nDone: deleted {deleted}, failed {failed}.")


if __name__ == "__main__":
    main()
