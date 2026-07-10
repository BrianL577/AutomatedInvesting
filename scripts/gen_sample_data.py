#!/usr/bin/env python3
"""Generates a small synthetic 1-minute NQ bar CSV so run_backtest.py works
out of the box for a smoke test. Not real market data — replace with actual
historical bars (Tradovate, Databento, etc.) for real backtesting."""
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytz

ET = pytz.timezone("America/New_York")
OUT = Path(__file__).resolve().parent.parent / "data" / "NQ_1min.csv"
OUT.parent.mkdir(exist_ok=True)


def gen_day(day: datetime, rng: random.Random):
    rows = []
    price = 18000 + rng.uniform(-100, 100)
    t = ET.localize(datetime(day.year, day.month, day.day, 9, 30))
    open_price = price
    direction_bias = rng.choice([-1, 1])
    for i in range(150):  # 9:30 -> 12:00
        drift = direction_bias * rng.uniform(0, 1.2) if i < 15 else rng.uniform(-1, 1) * 0.8
        o = price
        c = o + drift + rng.uniform(-1.5, 1.5)
        h = max(o, c) + rng.uniform(0, 1.0)
        l = min(o, c) - rng.uniform(0, 1.0)
        rows.append([t + timedelta(minutes=i), round(o, 2), round(h, 2), round(l, 2), round(c, 2), rng.randint(200, 2000)])
        price = c
    return rows


def main():
    rng = random.Random(42)
    start = datetime(2024, 1, 2)
    all_rows = []
    d = start
    days_done = 0
    while days_done < 40:
        if d.weekday() < 5:
            all_rows.extend(gen_day(d, rng))
            days_done += 1
        d += timedelta(days=1)

    with open(OUT, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for row in all_rows:
            writer.writerow(row)
    print(f"Wrote {len(all_rows)} bars to {OUT}")


if __name__ == "__main__":
    main()
