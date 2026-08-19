#!/usr/bin/env python3
"""Live-updating candlestick chart directly in the terminal, built from the
bot's own log output. Read-only: tails jj_bot.log for "Bar ..." lines and
renders them with plotext — never touches the trading process, the
TopstepX connection, or any order/account state, so it's safe to run
alongside a live bot with zero risk.

Usage (from the repo root, in a second PowerShell window):
    python scripts\\live_chart.py
    python scripts\\live_chart.py --log jj_bot.log --bars 60
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import deque
from pathlib import Path

import plotext as plt

REPO_ROOT = Path(__file__).resolve().parent.parent

# Matches the exact format live_runner_topstepx.py logs each closed bar in:
#   "Bar 09:41 O:29488.50 H:29491.50 L:29487.75 C:29488.00 phase=continuation"
BAR_LINE = re.compile(
    r"Bar (?P<time>\d{2}:\d{2}) "
    r"O:(?P<open>[\d.]+) H:(?P<high>[\d.]+) L:(?P<low>[\d.]+) C:(?P<close>[\d.]+) "
    r"phase=(?P<phase>\S+)"
)


def _seed_recent_bars(log_path: Path, max_bars: int) -> deque:
    """Reads the tail of the log file to pre-populate the chart with recent
    bars on startup, instead of showing an empty chart until the next bar
    closes (which can be up to a minute away)."""
    bars: deque = deque(maxlen=max_bars)
    try:
        recent_lines = deque(log_path.open(encoding="utf-8", errors="replace"), maxlen=5000)
    except OSError:
        return bars
    for line in recent_lines:
        m = BAR_LINE.search(line)
        if m:
            bars.append(m.groupdict())
    return bars


def _redraw(bars: deque, account_label: str) -> None:
    if not bars:
        return
    # CONFIRMED on the real (Windows) machine: plotext's date_form/candlestick
    # date parsing calls datetime.fromtimestamp() on an internally-computed
    # epoch offset, which raises OSError("Invalid argument") on Windows for
    # values datetime.fromtimestamp() rejects there but glibc/Linux accepts
    # (e.g. a pre-1970 or otherwise out-of-range timestamp) — this reproduced
    # every redraw once real bars started flowing, even though it worked fine
    # in local (Linux) testing before this shipped. Sidestepping plotext's
    # date engine entirely: candlestick only treats an axis as "dates" (and
    # goes through strings_to_time/fromtimestamp) when the values passed are
    # strings (see plotext's Monitor.to_time) — passing plain integer
    # positions instead skips that path completely, and plt.xticks() still
    # labels those positions with the real HH:MM strings for display.
    positions = list(range(len(bars)))
    times = [b["time"] for b in bars]
    data = {
        "Open": [float(b["open"]) for b in bars],
        "High": [float(b["high"]) for b in bars],
        "Low": [float(b["low"]) for b in bars],
        "Close": [float(b["close"]) for b in bars],
    }
    latest = bars[-1]

    plt.clt()  # clear terminal
    plt.cld()  # clear previous chart data
    plt.candlestick(positions, data)
    plt.xticks(positions, times)
    plt.title(f"{account_label} - {latest['close']} - phase={latest['phase']} - {time.strftime('%H:%M:%S')}")
    plt.xlabel("Time")
    plt.ylabel("Price")
    plt.theme("dark")
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=str(REPO_ROOT / "jj_bot.log"), help="Path to the bot's log file (default: jj_bot.log in the repo root)")
    parser.add_argument("--bars", type=int, default=60, help="How many recent bars to keep on screen (default: 60)")
    parser.add_argument("--refresh", type=float, default=2.0, help="Seconds between redraws while waiting for the next bar (default: 2.0)")
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"Log file not found: {log_path}", file=sys.stderr)
        raise SystemExit(1)

    bars = _seed_recent_bars(log_path, args.bars)
    account_label = "Live"

    with log_path.open(encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # start tailing from the current end of the file
        _redraw(bars, account_label)
        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(args.refresh)
                    _redraw(bars, account_label)
                    continue
                m = BAR_LINE.search(line)
                if m:
                    bars.append(m.groupdict())
                    _redraw(bars, account_label)
                trading_match = re.search(r"Trading \d+ account\(s\): \[('.+?')\]", line)
                if trading_match:
                    account_label = trading_match.group(1).strip("'")
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
