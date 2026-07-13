#!/usr/bin/env python3
"""Continuously syncs bars.csv (written by the JJBotExporter NinjaScript —
see ninjatrader/JJBotExporter.cs) into Supabase's `bars` table, which backs
the dashboard's Strategy Creator backtests.

Unlike a one-time scripts/import_bars.py run, this is meant to run
indefinitely alongside scripts/run_live.py: JJBotExporter writes its full
historical backlog (as far back as the chart's "Days to load" setting
allows) the first time it's attached, plus every new live bar afterward —
this script picks up both, so the Supabase dataset only grows over time and
never needs a manual re-export.

IMPORTANT — timezone: NinjaTrader's chart timestamps are in whatever
timezone the chart displays (commonly America/Chicago, CME's exchange
timezone), NOT UTC. Set NT_BAR_TIMEZONE to match your chart's actual
timezone (check NinjaTrader: Tools > General Options > Time Zone, or the
instrument's default exchange timezone if you haven't changed it) — getting
this wrong will silently shift every session boundary in the backtester.

Usage:
    python scripts/sync_bars_to_supabase.py
"""
from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

BATCH = 2000
POLL_SECONDS = 30
# Byte-offset based (not the old row-count ".sync_bars_offset"), so each
# poll only reads bytes appended since last time instead of re-reading and
# re-parsing the entire file — that used to get slower every cycle as
# bars.csv grew into the millions of rows. New filename so an old row-count
# offset is never misread as a byte position (which would corrupt the
# resume point). First run after upgrading does one full re-scan (upserts
# are idempotent, so this is safe, just a one-time cost); every run after
# that only reads the new tail.
OFFSET_FILE_NAME = ".sync_bars_byte_offset"


def main() -> None:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    export_dir = os.getenv("NT_EXPORT_DIR", "")
    tz_name = os.getenv("NT_BAR_TIMEZONE", "America/Chicago")
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (env or .env).")
    if not export_dir:
        raise SystemExit("Set NT_EXPORT_DIR (must match the JJBotExporter indicator's Export Directory).")

    bars_csv = Path(export_dir) / "bars.csv"
    offset_file = Path(export_dir) / OFFSET_FILE_NAME
    tz = ZoneInfo(tz_name)

    print(f"Watching {bars_csv} (timezone: {tz_name}). Ctrl+C to stop.")
    byte_offset = int(offset_file.read_text()) if offset_file.exists() else 0

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    total_synced = 0
    while True:
        if bars_csv.exists():
            byte_offset, total_synced = _sync_new_rows(bars_csv, byte_offset, tz, url, headers, offset_file, total_synced)
        time.sleep(POLL_SECONDS)


def _sync_new_rows(
    bars_csv: Path, byte_offset: int, tz: ZoneInfo, url: str, headers: dict, offset_file: Path, total_synced: int
) -> tuple[int, int]:
    """Reads bars.csv starting at byte_offset and uploads it to Supabase in
    BATCH-sized chunks, printing progress and checkpointing the byte offset
    after EACH chunk (not just once at the end) — so a large one-time
    catch-up (e.g. after widening NinjaTrader's "Days to load") shows live
    progress instead of going silent for minutes, and a Ctrl+C mid-catch-up
    only loses at most one chunk of work instead of the whole thing.
    Returns the updated (byte_offset, total_synced)."""
    with open(bars_csv, newline="") as f:
        f.seek(byte_offset)
        reader = csv.reader(f)
        chunk: list[dict] = []
        for row in reader:
            if len(row) < 6:
                continue
            ts_naive, o, h, l, c, v = row[:6]
            try:
                local_dt = _parse_naive(ts_naive).replace(tzinfo=tz)
            except ValueError:
                continue
            chunk.append({
                "t": local_dt.isoformat(),
                "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                "v": float(v) if v else 0.0,
            })
            if len(chunk) >= BATCH:
                resp = requests.post(f"{url}/rest/v1/bars?on_conflict=t", json=chunk, headers=headers, timeout=60)
                resp.raise_for_status()
                byte_offset = f.tell()
                offset_file.write_text(str(byte_offset))
                total_synced += len(chunk)
                print(f"Synced {len(chunk)} new bar(s), {total_synced} total so far.")
                chunk = []
        if chunk:
            resp = requests.post(f"{url}/rest/v1/bars?on_conflict=t", json=chunk, headers=headers, timeout=60)
            resp.raise_for_status()
            byte_offset = f.tell()
            offset_file.write_text(str(byte_offset))
            total_synced += len(chunk)
            print(f"Synced {len(chunk)} new bar(s), {total_synced} total so far.")
    return byte_offset, total_synced


def _parse_naive(ts: str):
    from datetime import datetime

    return datetime.fromisoformat(ts)


if __name__ == "__main__":
    main()
