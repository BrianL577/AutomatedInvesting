#!/usr/bin/env python3
"""Deletes virtual-practice trade rows that did NOT come from the live
strategy engine -- lighter-weight than reset_virtual_practice_data.py,
which wipes every virtual_practice row wholesale. This only removes the
contaminated ones, leaving genuine trade history intact.

Every real signal jj_bot/strategy.py ever produces has a `reason` string
prefixed "[SessionName] ..." (see StrategyEngine.on_bar -- both the
continuation and reversion branches build reason this way). A
source="virtual_practice" row with no such prefix did not come from the
engine at all -- it's synthetic/manually-inserted test data or a corrupted
write that slipped into Supabase. Because the dashboard's Stage/Funded
badge is computed by replaying an account's trades in date order (see
dashboard/app/virtual-practice/page.tsx's replayStage), even one such row
with a bogus/early date can silently "fund" an account that never actually
crossed the real $3,000 target.

Usage:
    python scripts/clean_contaminated_virtual_trades.py            # dry run
    python scripts/clean_contaminated_virtual_trades.py --apply    # actually delete
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_TRADES_PATH = REPO_ROOT / "dashboard" / "data" / "trades.json"

_REASON_PREFIX = re.compile(r"^\[[^\]]+\]")


def _is_contaminated(reason: object) -> bool:
    return not _REASON_PREFIX.match(reason) if isinstance(reason, str) else True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually delete (default is a dry run)")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    # ---- local trades.json ----
    if LOCAL_TRADES_PATH.exists():
        local_trades = json.loads(LOCAL_TRADES_PATH.read_text())
        bad_local = [
            t for t in local_trades
            if t.get("source") == "virtual_practice" and _is_contaminated(t.get("reason"))
        ]
        print(f"Local file: {len(bad_local)} contaminated virtual_practice row(s).")
        for t in bad_local[:20]:
            print(f"  {t.get('timestamp')}  {t.get('account_name')}  reason={t.get('reason')!r}")
        if args.apply and bad_local:
            bad_ids = {id(t) for t in bad_local}
            kept = [t for t in local_trades if id(t) not in bad_ids]
            tmp_path = LOCAL_TRADES_PATH.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(kept, indent=2, default=str))
            tmp_path.replace(LOCAL_TRADES_PATH)
            print(f"  Removed {len(bad_local)} row(s) from {LOCAL_TRADES_PATH}.")
    else:
        print(f"Local file not found: {LOCAL_TRADES_PATH} — skipping.")

    # ---- Supabase ----
    if supabase_url and supabase_key:
        headers = {"apikey": supabase_key, "Authorization": f"Bearer {supabase_key}"}
        resp = requests.get(
            f"{supabase_url}/rest/v1/trades",
            params={"select": "id,timestamp,account_name,reason,pnl_dollars", "source": "eq.virtual_practice"},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        bad_remote = [r for r in rows if _is_contaminated(r.get("reason"))]
        print(f"\nSupabase: {len(bad_remote)} contaminated virtual_practice row(s) out of {len(rows)} total.")
        for r in bad_remote[:20]:
            print(f"  id={r.get('id')}  {r.get('timestamp')}  {r.get('account_name')}  reason={r.get('reason')!r}  pnl=${r.get('pnl_dollars')}")
        if len(bad_remote) > 20:
            print(f"  ... and {len(bad_remote) - 20} more")

        if args.apply and bad_remote:
            print("\nDeleting from Supabase...")
            for r in bad_remote:
                del_resp = requests.delete(
                    f"{supabase_url}/rest/v1/trades",
                    params={"id": f"eq.{r['id']}"},
                    headers=headers,
                    timeout=30,
                )
                if not del_resp.ok:
                    print(f"  FAILED id={r['id']}: {del_resp.status_code} {del_resp.text}")
            print(f"  Deleted {len(bad_remote)} row(s).")
    else:
        print("\nSUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — skipping Supabase.")

    if not args.apply:
        print("\nDry run only — nothing was deleted. Re-run with --apply to actually delete the contaminated rows.")


if __name__ == "__main__":
    main()
