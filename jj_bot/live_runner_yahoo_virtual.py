"""Practice mode WITHOUT a TopStep/TopstepX subscription.

Same VirtualAccountManager + strategy as live_runner_topstepx_virtual.py, but
market data comes from Yahoo Finance's free (delayed) 1-minute NQ=F chart
feed instead of the paid TopstepX API. This is fine for virtual accounts: no
real order is ever placed, every Bar carries its own timestamp, and virtual
trades resolve against later bars -- so a ~10-15 minute data delay only
shifts WHEN a bar is processed, never what the strategy decides or what a
virtual trade's P&L is.

Caveats: Yahoo's chart endpoint is unofficial (no SLA, may rate-limit or
change), and its NQ=F continuous contract can differ by a few points from
TopstepX's front-month quote around contract rolls.
"""
from __future__ import annotations

import threading
import time as time_module
from datetime import datetime, timezone
from typing import Optional

import requests

from .config import AppConfig
from .live_runner_topstepx_virtual import TopstepXVirtualPracticeRunner
from .logging_setup import setup_logging
from .models import Bar
from .time_utils import to_et
from .bar_aggregator import BarAggregator
from .virtual_accounts import VirtualAccountManager
from collections import deque

logger = setup_logging()

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# Yahoo's symbol for continuous front-month futures; NQ -> NQ=F.
_SYMBOL_MAP = {"NQ": "NQ=F", "ES": "ES=F", "MNQ": "NQ=F", "MES": "ES=F"}


def parse_yahoo_bars(payload: dict, tz_name: str = "America/New_York") -> list[Bar]:
    """Turn a Yahoo v8 chart response into 1-minute Bars (ET), oldest first.
    Skips minutes with missing OHLC (Yahoo returns nulls for gaps)."""
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes = (quote.get(k) or [] for k in ("open", "high", "low", "close"))
    vols = quote.get("volume") or []
    bars: list[Bar] = []
    for i, ts in enumerate(stamps):
        try:
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        except IndexError:
            continue
        if None in (o, h, l, c):
            continue
        minute = to_et(datetime.fromtimestamp(ts, tz=timezone.utc), tz_name).replace(second=0, microsecond=0)
        v = vols[i] if i < len(vols) and vols[i] is not None else 0
        bars.append(Bar(timestamp=minute, open=float(o), high=float(h), low=float(l), close=float(c), volume=int(v)))
    bars.sort(key=lambda b: b.timestamp)
    return bars


class YahooVirtualPracticeRunner(TopstepXVirtualPracticeRunner):
    POLL_SECONDS = 30

    def __init__(self, cfg: AppConfig, num_accounts: Optional[int] = None):
        # Deliberately does NOT call the parent __init__: that builds a
        # TopstepXClient (credentials + TOPSTEPX_ALLOW_LIVE gate) we don't want.
        self.cfg = cfg
        self.client = None
        self.aggregator = BarAggregator(tz_name=cfg.strategy.timezone)
        self.manager = VirtualAccountManager(cfg, num_accounts=num_accounts, chart_renderer=self._render_chart)
        self._got_first_tick = False
        self._shutting_down = threading.Event()
        self._bar_history: deque = deque(maxlen=240)
        self._holds_lock = False
        self._last_bar_ts: Optional[datetime] = None
        self.symbol = _SYMBOL_MAP.get(cfg.instrument.symbol.upper(), f"{cfg.instrument.symbol}=F")

    def _fetch_bars(self) -> list[Bar]:
        resp = requests.get(
            YAHOO_CHART_URL.format(symbol=self.symbol),
            params={"interval": "1m", "range": "1d", "includePrePost": "true"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        return parse_yahoo_bars(resp.json(), self.cfg.strategy.timezone)

    def _new_closed_bars(self, bars: list[Bar], now: Optional[datetime] = None) -> list[Bar]:
        """Bars newer than the last one processed, excluding the final bar if
        it is still forming (its minute hasn't finished yet)."""
        now_et = to_et(now or datetime.now(timezone.utc), self.cfg.strategy.timezone)
        out = []
        for b in bars:
            if (now_et - b.timestamp).total_seconds() < 60:
                continue  # still forming
            if self._last_bar_ts is not None and b.timestamp <= self._last_bar_ts:
                continue
            out.append(b)
        return out

    def _start_inner(self) -> None:
        logger.info(
            "Practice mode on FREE delayed Yahoo data (%s) -- no TopStep login or API subscription used. "
            "Simulating %d virtual account(s): %s",
            self.symbol, len(self.manager.accounts), [a.name for a in self.manager.accounts],
        )
        # Seed chart history + the high-water mark from what's already there so a
        # (re)start doesn't replay the whole day's setups onto idle accounts --
        # same as the TopstepX runner, which only ever sees bars from "now" on.
        while not self._shutting_down.is_set():
            try:
                bars = self._fetch_bars()
                if bars:
                    self._bar_history.extend(bars[-240:])
                    self._last_bar_ts = bars[-1].timestamp
                    logger.info("Feed alive. Latest available bar %s; processing only newer bars.",
                                self._last_bar_ts.strftime("%Y-%m-%d %H:%M"))
                    break
            except Exception:
                logger.exception("Initial Yahoo fetch failed; retrying.")
            self._shutting_down.wait(15)

        try:
            backoff = self.POLL_SECONDS
            while not self._shutting_down.wait(backoff):
                try:
                    for bar in self._new_closed_bars(self._fetch_bars()):
                        self._last_bar_ts = bar.timestamp
                        self._on_bar(bar)
                    backoff = self.POLL_SECONDS
                except Exception:
                    logger.exception("Yahoo poll failed -- will retry.")
                    backoff = min(backoff * 2, 300)
        except KeyboardInterrupt:
            logger.info("Stopping.")
            self._shutting_down.set()
