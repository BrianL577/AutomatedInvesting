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

# Supabase Storage bucket the trade-chart PNGs are uploaded to. Public (not
# a signed URL) since the dashboard is the only consumer and these charts
# contain no sensitive data — just OHLC candles and price levels.
CHART_BUCKET = "trade-charts"


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
        self._chart_bucket_ready = False

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

    def _ensure_chart_bucket(self) -> None:
        """Best-effort, idempotent: creates the public storage bucket the
        trade charts get uploaded to if it doesn't already exist. Runs at
        most once per process. Never raises — a failure here just means
        chart uploads fail too (still not fatal, see _upload_chart)."""
        if self._chart_bucket_ready:
            return
        self._chart_bucket_ready = True  # only ever try once per process
        try:
            resp = requests.post(
                f"{self.supabase_url}/storage/v1/bucket",
                json={"id": CHART_BUCKET, "name": CHART_BUCKET, "public": True},
                headers={
                    "apikey": self.supabase_key,
                    "Authorization": f"Bearer {self.supabase_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            # 200 = created, 400 = "Bucket already exists" on every run after
            # the first — both are the desired end state, so only genuinely
            # unexpected statuses get logged.
            if resp.status_code not in (200, 400):
                logger.warning("Unexpected status creating chart bucket: %s %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.warning("Could not ensure chart storage bucket exists: %s", exc)

    def _upload_chart(self, chart_path: str) -> Optional[str]:
        """Uploads the local chart PNG to Supabase Storage and returns its
        public URL, or None if anything about that fails — chart upload
        failures must never block logging the trade itself (the same
        contract render_trade_chart already follows for rendering)."""
        try:
            local_path = Path(chart_path)
            if not local_path.exists():
                return None
            self._ensure_chart_bucket()
            image_bytes = local_path.read_bytes()
            resp = requests.post(
                f"{self.supabase_url}/storage/v1/object/{CHART_BUCKET}/{local_path.name}",
                data=image_bytes,
                headers={
                    "apikey": self.supabase_key,
                    "Authorization": f"Bearer {self.supabase_key}",
                    "Content-Type": "image/png",
                    "x-upsert": "true",
                },
                timeout=20,
            )
            resp.raise_for_status()
            return f"{self.supabase_url}/storage/v1/object/public/{CHART_BUCKET}/{local_path.name}"
        except Exception as exc:
            logger.warning("Failed to upload trade chart to Supabase Storage: %s", exc)
            return None

    def log_trade(self, trade: TradeResult, account_name: Optional[str] = None, chart_path: Optional[str] = None) -> None:
        pnl_dollars = round(trade.pnl_points * self.dollar_per_point * trade.qty, 2)
        chart_url = None
        if chart_path and self.supabase_url and self.supabase_key:
            chart_url = self._upload_chart(chart_path)
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
            # Path to the candlestick screenshot render_trade_chart() saved
            # for this trade (jj_bot/trade_chart.py) — None if charting
            # wasn't wired up for this call site, or rendering failed for
            # this specific trade (never blocks logging the trade itself).
            "chart_path": chart_path,
            # Public Supabase Storage URL for the same chart — this is what
            # the dashboard actually renders. None if Supabase isn't
            # configured, there was no chart to upload, or the upload
            # failed (also never blocks logging the trade itself).
            "chart_url": chart_url,
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