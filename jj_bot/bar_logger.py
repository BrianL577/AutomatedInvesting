"""Writes every 1-min bar the live bot sees to Supabase's `bars` table
(supabase/schema.sql) — the same table scripts/import_bars.py fills
one-off from a CSV for the Strategy Creator's backtests, which until now
was the ONLY thing populating it. The live bot itself never recorded a
single bar of what it actually saw while trading.

Exists because a real, non-hypothetical need came up: after a losing
streak, there was no way to answer "what would a strategy change have
done against the actual price action from that week" — the `trades` table
only has each trade's own entry/exit/stop/target, not the full price
series around it, so nothing could be replayed. This closes that gap
going forward: once bars are flowing in here, jj_bot/backtest.py can be
pointed at any past window and get a real, bar-by-bar replay instead of
guessing from trade-level summaries.

Never wired into the live TRADING decision path — this is pure logging,
same contract as trade_logger.py: a write failure here must never affect
order placement or the strategy engine.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from .models import Bar

logger = logging.getLogger("jj_bot.bar_logger")


class BarLogger:
    def __init__(self) -> None:
        self.supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    @property
    def enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    def log_bar(self, bar: Bar) -> None:
        """Best-effort, fire-and-forget: logs one closed bar. Never raises —
        a Supabase hiccup here must never take down the bar/strategy loop
        that owns real trading decisions."""
        if not self.enabled:
            return
        try:
            resp = requests.post(
                f"{self.supabase_url}/rest/v1/bars",
                json={
                    "t": bar.timestamp.isoformat(),
                    "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close,
                    "v": bar.volume,
                },
                headers={
                    "apikey": self.supabase_key,
                    "Authorization": f"Bearer {self.supabase_key}",
                    "Content-Type": "application/json",
                    # t is the primary key -- a restart re-processing a bar
                    # whose timestamp was already logged must overwrite it,
                    # not 409 and get silently dropped by the except below.
                    "Prefer": "resolution=merge-duplicates,return=minimal",
                },
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to log bar to Supabase: %s", exc)
