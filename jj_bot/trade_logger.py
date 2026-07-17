"""Writes trade results to dashboard/data/trades.json, and — when
SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are configured — also to a Supabase
`trades` table (see supabase/schema.sql).

Supabase is what makes the dashboard show *live* trades instead of a static
file that only updates on redeploy: the Python bot (wherever it's actually
running, e.g. Railway) writes to Supabase, and the Vercel dashboard reads
from Supabase directly on every page load. The local JSON file still gets
written too, so backtests and local runs work without any Supabase setup.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from .models import TradeResult

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = REPO_ROOT / "dashboard" / "data" / "trades.json"


class TradeLogger:
    def __init__(self, path: Optional[Path] = None, dollar_per_point: float = 20.0, source: str = "backtest"):
        self.path = Path(path) if path else DEFAULT_LOG_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.dollar_per_point = dollar_per_point
        self.source = source
        if not self.path.exists():
            self.path.write_text("[]")
        self.supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return []

    def _write(self, trades: list[dict]) -> None:
        # Atomic write (temp file + replace) so a collision with another
        # process/instance holding the file open can't corrupt it, and so a
        # PermissionError here can be handled by the caller instead of
        # propagating up uncaught.
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(trades, indent=2, default=str))
        tmp_path.replace(self.path)

    def log_trade(self, trade: TradeResult, account_name: Optional[str] = None) -> None:
        pnl_dollars = round(trade.pnl_points * self.dollar_per_point, 2)
        record = {
            "timestamp": trade.signal.timestamp.isoformat(),
            "exit_timestamp": trade.exit_timestamp.isoformat(),
            "phase": trade.signal.phase.value,
            "direction": trade.signal.direction.value,
            "grade": trade.signal.grade.value,
            "reason": trade.signal.reason,
            "entry_price": trade.signal.entry_price,
            "exit_price": trade.exit_price,
            "stop_price": trade.signal.stop_price,
            "target_price": trade.signal.target_price,
            "win": trade.win,
            "pnl_points": round(trade.pnl_points, 2),
            "pnl_dollars": pnl_dollars,
            "source": self.source,
            "account_name": account_name,
            "logged_at": datetime.utcnow().isoformat() + "Z",
        }

        # CRITICAL: this is called from _on_fill() on the fills-tailing
        # background thread. An unhandled exception here silently kills
        # that thread forever (Python daemon threads just die on an
        # uncaught exception — no crash, no restart, no entry in the log
        # file). Confirmed live: this is why the dashboard stopped
        # receiving trades on 2026-07-13 after a file-write collision from
        # a duplicate bot instance — the local JSON write threw, the
        # thread died, and every fill after that point (including real
        # money-losing trades) went completely unlogged for the rest of
        # that process's life, with zero visible error anywhere.
        # Every path below must be exception-safe so a write hiccup costs
        # us one missed dashboard row, never the whole fills pipeline.
        try:
            trades = self._read()
            local_record = {"id": len(trades) + 1, **record}
            trades.append(local_record)
            self._write(trades)
        except OSError:
            logger.exception(
                "Failed to write trade to local dashboard file %s — trade result NOT recorded locally. "
                "This trade still executed for real; only the dashboard/local log entry is missing.",
                self.path,
            )

        if self.supabase_url and self.supabase_key:
            self._log_to_supabase(record)

    def _log_to_supabase(self, record: dict) -> None:
        try:
            resp = requests.post(
                f"{self.supabase_url}/rest/v1/trades",
                json=record,
                headers={
                    "apikey": self.supabase_key,
                    "Authorization": f"Bearer {self.supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            # Never let a Supabase hiccup take down the trading loop — the
            # local JSON write above already succeeded (or failed and was
            # already logged above).
            logger.warning("Failed to write trade to Supabase: %s", exc)

    def clear(self) -> None:
        try:
            self._write([])
        except OSError:
            logger.exception("Failed to clear trade log at %s", self.path)