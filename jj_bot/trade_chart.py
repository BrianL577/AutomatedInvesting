"""Renders a PNG chart for each closed trade: candles around the trade
window, an entry marker (buy/sell + price), a stop/target line each, an
exit marker, and a green/red border for the outcome.

Deliberately built from the bars this process already streamed (see
BarAggregator/`_bar_history` in live_runner_topstepx.py) rather than
screenshotting TopstepX's own web/desktop UI — that would need browser/
window automation staying correctly focused on an always-open chart on a
machine already described as fragile, and would silently produce nothing
useful the moment a window loses focus or a login session expires.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # headless — never opens a window, safe to call from any thread

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .models import Bar, Direction, Signal

logger = logging.getLogger("jj_bot.trade_chart")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHART_DIR = REPO_ROOT / "trade_charts"

# Standard candlestick convention: green = bullish (close > open), red =
# bearish — same colors most trading platforms use, including TopstepX's own
# chart. See Bar.is_green (jj_bot/models.py).
BULL_COLOR = "#26a69a"
BEAR_COLOR = "#ef5350"
WIN_COLOR = "#2e7d32"
LOSS_COLOR = "#c62828"
ENTRY_COLOR = "#1e88e5"


def render_trade_chart(
    bars: Sequence[Bar],
    signal: Signal,
    exit_price: float,
    exit_timestamp: datetime,
    win: bool,
    account_name: str,
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    out_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Builds and saves a candlestick PNG for one closed trade. Returns the
    saved path, or None if it couldn't be built (never raises — a broken
    chart must never take down the trade-logging pipeline that owns the
    real P&L record; the trade itself is always logged regardless).

    stop_price/target_price default to the signal's own values, but pass the
    ACTUAL prices sent to the broker when known (live_runner_topstepx.py's
    eval scale-down can shrink these per-account) — otherwise the chart
    would show a stop/target line the trade was never actually protected
    by."""
    stop_price = signal.stop_price if stop_price is None else stop_price
    target_price = signal.target_price if target_price is None else target_price
    out_dir = Path(out_dir) if out_dir else DEFAULT_CHART_DIR
    try:
        # Bar.timestamp is tz-aware (America/New_York, see BarAggregator);
        # the live runner's exit_timestamp is a naive datetime.now(). Strip
        # tzinfo from both before comparing — comparing naive vs. aware
        # datetimes raises TypeError, and this function only needs
        # approximate windowing, not cross-timezone precision.
        exit_naive = exit_timestamp.replace(tzinfo=None)
        window = [b for b in bars if b.timestamp.replace(tzinfo=None) <= exit_naive]
        if not window:
            logger.warning("No bars available to chart trade on %s at %s.", account_name, signal.timestamp)
            return None

        out_dir.mkdir(parents=True, exist_ok=True)
        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)

        for i, bar in enumerate(window):
            color = BULL_COLOR if bar.is_green else BEAR_COLOR
            ax.plot([i, i], [bar.low, bar.high], color=color, linewidth=1, zorder=2)
            body_height = max(bar.body, (bar.high - bar.low) * 0.02, 0.01)
            ax.add_patch(Rectangle(
                (i - 0.3, min(bar.open, bar.close)), 0.6, body_height,
                facecolor=color, edgecolor=color, zorder=3,
            ))

        entry_idx = _nearest_index(window, signal.timestamp)
        is_long = signal.direction == Direction.LONG
        ax.scatter(
            [entry_idx], [signal.entry_price], marker="^" if is_long else "v",
            s=220, color=ENTRY_COLOR, zorder=5, edgecolors="white", linewidths=1,
        )
        ax.annotate(
            f"{'BUY' if is_long else 'SELL'} @ {signal.entry_price:.2f}",
            (entry_idx, signal.entry_price), textcoords="offset points",
            xytext=(0, 16 if is_long else -24), ha="center", fontsize=9,
            color=ENTRY_COLOR, fontweight="bold",
        )

        exit_idx = _nearest_index(window, exit_timestamp)
        outcome_color = WIN_COLOR if win else LOSS_COLOR
        ax.scatter(
            [exit_idx], [exit_price], marker="X", s=180, color=outcome_color,
            zorder=5, edgecolors="white", linewidths=1,
        )
        ax.annotate(
            f"EXIT @ {exit_price:.2f}", (exit_idx, exit_price), textcoords="offset points",
            xytext=(0, -24 if is_long else 16), ha="center", fontsize=9,
            color=outcome_color, fontweight="bold",
        )

        ax.axhline(stop_price, color=LOSS_COLOR, linestyle="--", linewidth=1.2, label=f"Stop {stop_price:.2f}")
        ax.axhline(target_price, color=WIN_COLOR, linestyle="--", linewidth=1.2, label=f"Target {target_price:.2f}")

        outcome_label = "WIN" if win else "LOSS"
        ax.set_title(
            f"{account_name} — {signal.direction.value.upper()} {outcome_label} — {signal.timestamp:%Y-%m-%d %H:%M}",
            fontsize=13, fontweight="bold", color=outcome_color,
        )
        # A thick colored border is what makes win/loss legible at a glance
        # from a thumbnail, without having to read the title text.
        for spine in ax.spines.values():
            spine.set_edgecolor(outcome_color)
            spine.set_linewidth(3)

        step = max(1, len(window) // 12)
        tick_positions = list(range(0, len(window), step))
        ax.set_xticks(tick_positions)
        ax.set_xticklabels([window[i].timestamp.strftime("%H:%M") for i in tick_positions], rotation=45, ha="right")
        ax.set_ylabel("Price")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
        ax.grid(alpha=0.2)
        fig.tight_layout()

        filename = f"{account_name}_{signal.timestamp:%Y%m%d_%H%M%S}_{outcome_label}.png"
        path = out_dir / filename
        fig.savefig(path)
        plt.close(fig)
        logger.info("Saved trade chart: %s", path)
        return path
    except Exception:
        logger.exception("Failed to render trade chart for %s — trade logging continues without it.", account_name)
        return None


def _nearest_index(bars: Sequence[Bar], ts: datetime) -> int:
    # See the tzinfo comment above render_trade_chart's window filter —
    # ts may be naive (exit_timestamp) or aware (signal.timestamp); bars are
    # always aware. Normalize both to naive before diffing.
    ts_naive = ts.replace(tzinfo=None)
    return min(range(len(bars)), key=lambda i: abs((bars[i].timestamp.replace(tzinfo=None) - ts_naive).total_seconds()))
