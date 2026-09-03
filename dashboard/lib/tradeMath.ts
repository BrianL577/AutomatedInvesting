// Pure helpers only — no `fs` or other server-only imports, safe to import
// from client components (see evalSimConfig.ts for why that split matters).
import type { Trade } from "./types";

// Fallback $/point when a trade's own pnl can't tell us (pnl_points === 0,
// i.e. an exact breakeven exit) — NQ at 2 contracts, $5/tick * 4 ticks/pt.
const FALLBACK_DOLLAR_PER_POINT = 20 * 2;

/** Planned gain:loss in dollars for one trade — e.g. "1520:1000" — derived
 * from the actual stop/target price levels the trade was placed with, not
 * the outcome. Uses this trade's own realized $/point (pnl_dollars /
 * pnl_points) so it's correct even for eval-scaled-down or funded-stage
 * trades whose stop/target sizing differs from the static config values. */
export function gainLossDollars(t: Trade): { gain: number; loss: number } {
  const dollarPerPoint = t.pnl_points !== 0 ? Math.abs(t.pnl_dollars / t.pnl_points) : FALLBACK_DOLLAR_PER_POINT;
  const gainPoints = Math.abs(t.target_price - t.entry_price);
  const lossPoints = Math.abs(t.entry_price - t.stop_price);
  return {
    gain: Math.round(gainPoints * dollarPerPoint),
    loss: Math.round(lossPoints * dollarPerPoint),
  };
}

export function gainLossRatioLabel(t: Trade): string {
  const { gain, loss } = gainLossDollars(t);
  return `${gain}:${loss}`;
}
