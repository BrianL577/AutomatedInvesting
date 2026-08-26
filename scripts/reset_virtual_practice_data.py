#!/usr/bin/env python3
"""One-time full reset of virtual-practice-account data: wipes every
source="virtual_practice" trade (local file + Supabase), and deletes
virtual_daily_state.json (per-account traded_today + lifetime net_dollars).

Exists because early virtual-practice sessions ran before a real bug fix
(the "chasing the same continuing move" fix — see jj_bot/virtual_accounts.py)
landed, so their data is contaminated: near-duplicate low-quality entries
that don't reflect what the fixed strategy actually does. This clears that
out so future runs start from a clean, trustworthy slate — it does NOT
touch anything with a different `source` (real trades, connection tests),
regardless of account name or price.

Usage:
    python scripts/reset_virtual_practice_data.py            # dry run: shows what would be deleted
    python scripts/reset_virtual_practice_data.py --apply     # actually deletes it, and resets state
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_TRADES_PATH = REPO_ROOT / "dashboard" / "data" / "trades.json"
STATE_PATH = REPO_ROOT / "virtual_daily_state.json"
CHART_DIR = REPO_ROOT / "trade_charts_virtual"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually delete/reset everything (default is a dry run)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # ---- local trades.json ----
    if LOCAL_TRADES_PATH.exists():
        local_trades = json.loads(LOCAL_TRADES_PATH.read_text())
        virtual_local = [t for t in local_trades if t.get("source") == "virtual_practice"]
        print(f"Local file: {len(virtual_local)} virtual_practice trade(s) of {len(local_trades)} total.")
        if args.apply and virtual_local:
            kept = [t for t in local_trades if t.get("source") != "virtual_practice"]
            tmp_path = LOCAL_TRADES_PATH.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(kept, indent=2, default=str))
            tmp_path.replace(LOCAL_TRADES_PATH)
            print(f"  Removed {len(virtual_local)} row(s) from {LOCAL_TRADES_PATH}.")
    else:
        print(f"Local file not found: {LOCAL_TRADES_PATH} — skipping.")

    # ---- Supabase ----
    if supabase_url and supabase_key:
        headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
        resp = requests.get(
            f"{supabase_url}/rest/v1/trades",
            params={"select": "id,timestamp,account_name,pnl_dollars", "source": "eq.virtual_practice"},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        virtual_remote = resp.json()
        print(f"\nSupabase: {len(virtual_remote)} virtual_practice trade(s).")
        for r in virtual_remote[:20]:
            print(f"  id={r.get('id')}  {r.get('timestamp')}  {r.get('account_name')}  pnl=${r.get('pnl_dollars')}")
        if len(virtual_remote) > 20:
            print(f"  ... and {len(virtual_remote) - 20} more")

        if args.apply and virtual_remote:
            print("\nDeleting from Supabase...")
            resp = requests.delete(
                f"{supabase_url}/rest/v1/trades",
                params={"source": "eq.virtual_practice"},
                headers=headers,
                timeout=30,
            )
            if resp.ok:
                print(f"  Deleted all {len(virtual_remote)} row(s).")
            else:
                print(f"  FAILED: {resp.status_code} {resp.text}")
    else:
        print("\nSUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — skipping Supabase.")

    # ---- state file (daily traded_today + lifetime net_dollars) ----
    if STATE_PATH.exists():
        print(f"\nState file: {STATE_PATH} exists — {STATE_PATH.read_text()}")
        if args.apply:
            STATE_PATH.unlink()
            print(f"  Deleted {STATE_PATH}. Every account starts fresh at $0 / not-traded-today.")
    else:
        print(f"\nState file {STATE_PATH} does not exist — nothing to reset.")

    # ---- chart PNGs (informational only — not deleted automatically) ----
    if CHART_DIR.exists():
        charts = list(CHART_DIR.glob("*.png"))
        print(f"\n{len(charts)} chart(s) in {CHART_DIR} — not deleted automatically (harmless leftovers, delete manually if you want).")

    if not args.apply:
        print("\nDry run only — nothing was deleted. Re-run with --apply to actually reset everything.")


if __name__ == "__main__":
    main()
