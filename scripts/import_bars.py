#!/usr/bin/env python3
"""Imports historical 1-minute NQ bars from a CSV into the Supabase `bars`
table, which backs the dashboard's Strategy Creator backtests.

CSV columns: timestamp,open,high,low,close,volume (tz-aware timestamps, or
UTC assumed). Get real historical NQ data from e.g. Databento, Polygon,
FirstRate Data, or export from Tradovate/TradingView.

Usage:
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
      python scripts/import_bars.py --csv path/to/NQ_1min.csv

Requires the schema in supabase/schema.sql to be applied first.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BATCH = 2000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (env or .env).")

    df = pd.read_csv(args.csv, parse_dates=["timestamp"])
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    df = df.sort_values("timestamp")

    rows = [
        {
            "t": ts.isoformat(),
            "o": float(o), "h": float(h), "l": float(l), "c": float(c),
            "v": float(v) if pd.notna(v) else 0.0,
        }
        for ts, o, h, l, c, v in zip(
            df["timestamp"], df["open"], df["high"], df["low"], df["close"],
            df.get("volume", pd.Series([0] * len(df))),
        )
    ]

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Upsert on the primary key so re-imports are idempotent.
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        resp = requests.post(f"{url}/rest/v1/bars?on_conflict=t", json=chunk, headers=headers, timeout=60)
        resp.raise_for_status()
        print(f"Upserted {min(i + BATCH, len(rows))}/{len(rows)} bars", end="\r")

    print(f"\nDone: {len(rows)} bars imported into Supabase `bars`.")


if __name__ == "__main__":
    main()
