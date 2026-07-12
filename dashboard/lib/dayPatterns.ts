/**
 * Groups simulated trades by weekday to surface simple pattern context
 * (e.g. "Mondays win less than Fridays") for the strategy chat. This is a
 * cheap aggregation over trades already produced by the backtester — it does
 * not run a second simulation.
 */
import type { SimTrade } from "./backtester";

const WEEKDAY_FMT = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short" });

export type WeekdayStats = {
  weekday: string;
  trades: number;
  wins: number;
  winRate: number; // 0-100
  netPnl: number; // $
  avgPnl: number; // $ per trade
};

export function analyzeWeekdayPatterns(trades: SimTrade[]): WeekdayStats[] {
  const order = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const buckets = new Map<string, { trades: number; wins: number; netPnl: number }>();
  for (const day of order) buckets.set(day, { trades: 0, wins: 0, netPnl: 0 });

  for (const t of trades) {
    const day = WEEKDAY_FMT.format(new Date(t.entryTime));
    const bucket = buckets.get(day);
    if (!bucket) continue; // skip weekends, shouldn't occur for RTH futures data
    bucket.trades += 1;
    if (t.win) bucket.wins += 1;
    bucket.netPnl += t.pnlDollars;
  }

  return order.map((day) => {
    const b = buckets.get(day)!;
    return {
      weekday: day,
      trades: b.trades,
      wins: b.wins,
      winRate: b.trades > 0 ? (b.wins / b.trades) * 100 : 0,
      netPnl: b.netPnl,
      avgPnl: b.trades > 0 ? b.netPnl / b.trades : 0,
    };
  });
}
