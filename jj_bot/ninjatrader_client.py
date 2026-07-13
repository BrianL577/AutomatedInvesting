"""NinjaTrader 8 client via the free Automated Trading Interface (ATI).

NinjaTrader has no REST/WebSocket API. ATI is a file-drop protocol: we write
command files into NinjaTrader's `incoming` folder and it executes them
(order placement only — one-way). For live bars and fill confirmations
(which ATI doesn't provide), a companion NinjaScript AddOn
(`ninjatrader/JJBotExporter.cs` — install into NinjaTrader) appends rows to
two CSV files that this client tails: `bars.csv` and `fills.csv`.

NinjaTrader itself is Windows-only desktop software — this client (and the
bot process using it) must run on the same Windows machine as NinjaTrader,
or a Windows VPS with NinjaTrader installed. See NINJATRADER.md.
"""
from __future__ import annotations

import csv
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import NinjaTraderCreds


class NinjaTraderError(RuntimeError):
    pass


@dataclass
class Contract:
    symbol: str  # NinjaTrader instrument name, e.g. "NQ 12-26"


@dataclass
class BracketOrderIds:
    entry_id: str
    take_profit_id: str
    stop_loss_id: str
    oco_id: str


class NinjaTraderClient:
    def __init__(self, creds: NinjaTraderCreds):
        self.creds = creds
        self.incoming_dir = Path(creds.incoming_dir)
        self.bars_csv = Path(creds.export_dir) / "bars.csv"
        self.fills_csv = Path(creds.export_dir) / "fills.csv"
        self.accounts: list[str] = []

    # ---- connection ----------------------------------------------------

    def connect(self) -> None:
        if not self.incoming_dir.exists():
            raise NinjaTraderError(
                f"ATI incoming folder not found: {self.incoming_dir}. "
                "Enable ATI in NinjaTrader (Tools > Settings > Automated Trading "
                "Interface) and confirm the folder path. See NINJATRADER.md."
            )
        non_sim = [a for a in self.creds.account_names if not (a.startswith("Sim") or a.startswith("DEMO"))]
        if non_sim:
            raise NinjaTraderError(
                f"Refusing to run: account(s) {non_sim} do not look like simulation accounts "
                "(expected names starting with 'Sim' or 'DEMO', e.g. 'Sim101' or 'DEMO8217187'). "
                "This bot is for paper/sim trading only."
            )
        self.accounts = list(self.creds.account_names)
        # Ensure the fill/bar export files exist so tailing doesn't fail on a fresh setup.
        self.fills_csv.parent.mkdir(parents=True, exist_ok=True)
        self.fills_csv.touch(exist_ok=True)
        self.bars_csv.touch(exist_ok=True)

    def disconnect(self) -> None:
        pass  # No persistent connection to close — ATI is file-based.

    def find_front_month_contract(self, root_symbol: str) -> Contract:
        """NinjaTrader instrument names include the specific contract month
        (e.g. 'NQ 12-26'); configure NT_INSTRUMENT with the current
        front-month name rather than resolving it dynamically, since ATI has
        no contract-lookup endpoint."""
        if not self.creds.instrument:
            raise NinjaTraderError(
                "Set NT_INSTRUMENT to the exact NinjaTrader instrument name for the "
                f"current front-month {root_symbol} contract, e.g. 'NQ 12-26'."
            )
        return Contract(symbol=self.creds.instrument)

    # ---- orders (ATI command files) ------------------------------------

    def _write_command(self, fields: list[str]) -> None:
        # ATI reads one command per file, watches the folder, and deletes
        # the file once processed. The filename must start with "oif"
        # (Order Interface File) — NinjaTrader identifies command files by
        # this prefix, not by content, and silently rejects anything else
        # with "Unknown OIF file type" in its Log tab.
        path = self.incoming_dir / f"oif_{uuid.uuid4().hex}.txt"
        # Exactly 13 semicolon-separated fields, no trailing semicolon —
        # an extra trailing ";" creates a phantom 14th empty field, which
        # ATI rejects with "invalid # of parameters, should be 13 but is 14".
        path.write_text(";".join(fields) + "\n")

    def place_bracket_order(
        self,
        contract: Contract,
        account: str,
        action: str,  # "BUY" or "SELL"
        qty: int,
        stop_price: float,
        target_price: float,
    ) -> BracketOrderIds:
        reverse = "SELL" if action == "BUY" else "BUY"
        entry_id = f"jjbot-entry-{uuid.uuid4().hex[:8]}"
        stop_id = f"jjbot-stop-{uuid.uuid4().hex[:8]}"
        target_id = f"jjbot-target-{uuid.uuid4().hex[:8]}"
        oco_id = f"jjbot-oco-{uuid.uuid4().hex[:8]}"

        # PLACE;<ACCOUNT>;<INSTRUMENT>;<ACTION>;<QTY>;<ORDER TYPE>;<LIMIT PRICE>;
        # <STOP PRICE>;<TIF>;<OCO ID>;<ORDER ID>;<STRATEGY>;<STRATEGY ID>
        self._write_command([
            "PLACE", account, contract.symbol, action, str(qty),
            "MARKET", "0", "0", "DAY", "", entry_id, "", "",
        ])
        self._write_command([
            "PLACE", account, contract.symbol, reverse, str(qty),
            "STOPMARKET", "0", f"{stop_price:.2f}", "DAY", oco_id, stop_id, "", "",
        ])
        self._write_command([
            "PLACE", account, contract.symbol, reverse, str(qty),
            "LIMIT", f"{target_price:.2f}", "0", "DAY", oco_id, target_id, "", "",
        ])
        return BracketOrderIds(entry_id=entry_id, take_profit_id=target_id, stop_loss_id=stop_id, oco_id=oco_id)

    def place_test_trade(
        self,
        contract: Contract,
        account: str,
        action: str = "BUY",
        qty: int = 1,
        stop_points: float = 4.0,
        target_points: float = 6.0,
    ) -> BracketOrderIds:
        price = self.get_last_price(contract)
        if action == "BUY":
            stop_price = price - stop_points
            target_price = price + target_points
        else:
            stop_price = price + stop_points
            target_price = price - target_points
        return self.place_bracket_order(contract, account, action, qty, stop_price, target_price)

    def get_last_price(self, contract: Contract) -> float:
        """Reads the close of the chronologically most recent row in
        bars.csv (written by the companion NinjaScript exporter) — ATI has
        no quote-snapshot command. Sorts by timestamp rather than trusting
        file order, since bars.csv can interleave rows from more than one
        chart/timeframe if JJBotExporter is attached to more than one
        (e.g. a Daily chart alongside the 1-minute one)."""
        rows = _read_csv_rows(self.bars_csv)
        if not rows:
            raise NinjaTraderError(
                "No bars found in bars.csv yet — confirm the JJBotExporter NinjaScript "
                "is running and attached to a chart for this instrument."
            )
        latest = max(rows, key=lambda r: r["timestamp"])
        return float(latest["close"])

    # ---- historical / streaming bars (via the companion exporter) ------

    def get_historical_bars(self, limit: int = 500) -> list[dict]:
        return _read_csv_rows(self.bars_csv)[-limit:]

    def tail_bars(self, on_bar: Callable[[dict], None], poll_seconds: float = 1.0) -> None:
        """Blocking loop: watches bars.csv for new rows appended by the
        NinjaScript exporter and invokes on_bar(row) for each new one."""
        seen = len(_read_csv_rows(self.bars_csv))
        while True:
            rows = _read_csv_rows(self.bars_csv)
            for row in rows[seen:]:
                on_bar(row)
            seen = len(rows)
            time.sleep(poll_seconds)

    def tail_fills(self, on_fill: Callable[[dict], None], poll_seconds: float = 1.0) -> None:
        """Blocking loop: watches fills.csv for new rows appended by the
        NinjaScript exporter and invokes on_fill(row) for each new one.
        Run from a separate thread alongside tail_bars()."""
        seen = len(_read_csv_rows(self.fills_csv))
        while True:
            rows = _read_csv_rows(self.fills_csv)
            for row in rows[seen:]:
                on_fill(row)
            seen = len(rows)
            time.sleep(poll_seconds)


def _read_csv_rows(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])) \
            if path.name == "bars.csv" else \
            list(csv.DictReader(f, fieldnames=["timestamp", "order_id", "account", "action", "price", "qty"]))
