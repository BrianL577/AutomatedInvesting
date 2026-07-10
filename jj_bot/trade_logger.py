"""Writes trade results to a JSON file the Vercel dashboard reads.

The dashboard (dashboard/) is a separate Next.js app deployed on Vercel. It
reads dashboard/data/trades.json to render the trade log, success rate, and
gained/lost totals. Both the backtester and the live paper-trading runner
call `TradeLogger.log_trade` so the same file backs both modes.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import TradeResult

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

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, trades: list[dict]) -> None:
        self.path.write_text(json.dumps(trades, indent=2, default=str))

    def log_trade(self, trade: TradeResult) -> None:
        trades = self._read()
        pnl_dollars = round(trade.pnl_points * self.dollar_per_point, 2)
        trades.append(
            {
                "id": len(trades) + 1,
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
                "logged_at": datetime.utcnow().isoformat() + "Z",
            }
        )
        self._write(trades)

    def clear(self) -> None:
        self._write([])
