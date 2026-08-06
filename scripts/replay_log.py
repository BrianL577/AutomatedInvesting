"""Replay a live bot_log.txt through the real strategy engine + backtester,
using a named saved strategy's exact parameters — answers "if this strategy
had been running, would it have made or lost money on these days" using the
bars actually seen live, instead of requiring a synthetic backtest dataset
or manual bar transcription (both painful and error-prone for a short,
specific window — confirmed live investigating a single missing-trade day).

Usage:
    python scripts/replay_log.py bot_log.txt "TestV5"
    python scripts/replay_log.py bot_log.txt          # uses config.yaml's JJ default

Parses every "Bar HH:MM O:.. H:.. L:.. C:.." line, using the log's own
"New trading day: YYYY-MM-DD" markers to anchor each bar's real ET calendar
date (this is what the live process itself uses internally — no timezone
guessing needed).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jj_bot.backtest import run_backtest, print_report
from jj_bot.config import (
    REPO_ROOT,
    AppConfig,
    InstrumentConfig,
    RiskConfig,
    StrategyConfig,
    TopstepEvalConfig,
)
from jj_bot.models import Bar
from jj_bot.time_utils import to_et
from dotenv import load_dotenv

BAR_RE = re.compile(r"Bar (\d{2}:\d{2}) O:([\d.]+) H:([\d.]+) L:([\d.]+) C:([\d.]+)")
DAY_RE = re.compile(r"New trading day: (\d{4}-\d{2}-\d{2})")


def _fetch_strategy_by_name(name: str) -> dict | None:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set in .env — can't fetch a named strategy.")
        return None
    resp = requests.get(
        f"{url}/rest/v1/strategies",
        params={"select": "config", "config->>name": f"eq.{name}"},
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        print(f"No saved strategy named '{name}' found in Supabase.")
        return None
    return rows[0]["config"]


def parse_log(path: str) -> list[Bar]:
    bars: list[Bar] = []
    current_day: date | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            day_match = DAY_RE.search(line)
            if day_match:
                current_day = datetime.strptime(day_match.group(1), "%Y-%m-%d").date()
                continue
            bar_match = BAR_RE.search(line)
            if bar_match and current_day:
                hhmm, o, h, l, c = bar_match.groups()
                hh, mm = map(int, hhmm.split(":"))
                ts = to_et(datetime(current_day.year, current_day.month, current_day.day, hh, mm), "America/New_York")
                bars.append(Bar(timestamp=ts, open=float(o), high=float(h), low=float(l), close=float(c)))
    bars.sort(key=lambda b: b.timestamp)
    return bars


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log_path")
    ap.add_argument("strategy_name", nargs="?", default=None)
    args = ap.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    with open(REPO_ROOT / "config.yaml") as f:
        raw = yaml.safe_load(f)

    if args.strategy_name:
        active = _fetch_strategy_by_name(args.strategy_name)
        if active is None:
            sys.exit(1)
        # _apply_active_strategy (imported below) pulls whatever's marked
        # is_active in Supabase, not by name — apply its same field-mapping
        # logic directly against this specific named config instead, so
        # this works regardless of what's currently toggled active on the
        # dashboard.
        from jj_bot.config import _ACTIVE_STRATEGY_FIELD_MAP, _ACTIVE_RISK_FIELD_MAP

        strategy_dict = dict(raw["strategy"])
        risk_dict = dict(raw["risk"])
        for (section, key), field_name in _ACTIVE_STRATEGY_FIELD_MAP.items():
            value = (active.get(section) or {}).get(key)
            if value is not None:
                strategy_dict[field_name] = value
        for key, field_name in _ACTIVE_RISK_FIELD_MAP.items():
            value = (active.get("risk") or {}).get(key)
            if value is not None:
                risk_dict[field_name] = value
        print(f"Replaying with strategy '{args.strategy_name}':")
    else:
        strategy_dict, risk_dict = raw["strategy"], raw["risk"]
        print("Replaying with config.yaml's default (JJ) strategy:")

    strategy_cfg = StrategyConfig(**strategy_dict)
    risk_cfg = RiskConfig(**risk_dict)
    instrument_cfg = InstrumentConfig(**raw["instrument"])
    eval_cfg = TopstepEvalConfig(**raw.get("topstep_eval", {}))

    cfg = AppConfig(strategy=strategy_cfg, risk=risk_cfg, instrument=instrument_cfg, topstep_eval=eval_cfg)

    bars = parse_log(args.log_path)
    if not bars:
        print(f"No 'Bar HH:MM ...' lines with a preceding 'New trading day' marker found in {args.log_path}.")
        sys.exit(1)
    print(f"Parsed {len(bars)} bars spanning {bars[0].timestamp.date()} to {bars[-1].timestamp.date()}.\n")

    report = run_backtest(cfg, bars, log_trades=False)
    print_report(report, cfg)

    dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size * cfg.risk.contracts_per_trade
    total_dollars = sum(t.pnl_points for t in report.trades) * dollar_per_point
    print(f"\nTotal $ P&L over this window ({cfg.risk.contracts_per_trade} contracts/trade): ${total_dollars:+,.2f}")
    for t in report.trades:
        pnl_d = t.pnl_points * dollar_per_point
        print(f"  {t.signal.timestamp}  {t.signal.direction.value:5s}  {'WIN ' if t.win else 'LOSS'}  {t.pnl_points:+.2f}pts  ${pnl_d:+,.2f}")


if __name__ == "__main__":
    main()
