import { promises as fs } from "fs";
import path from "path";
import type { Trade } from "../app/api/trades/route";

export async function loadTrades(): Promise<Trade[]> {
  const filePath = path.join(process.cwd(), "data", "trades.json");
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    const trades: Trade[] = JSON.parse(raw);
    return trades.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  } catch {
    return [];
  }
}

export type Stats = {
  totalTrades: number;
  wins: number;
  losses: number;
  successRate: number;
  totalGained: number;
  totalLost: number;
  netPnl: number;
  bestDayPnl: number;
  worstDayPnl: number;
  hitProfitCap: boolean;
  hitLossCap: boolean;
};

const PROFIT_CAP = 1520;
const LOSS_CAP = 1000;

export function computeStats(trades: Trade[]): Stats {
  const wins = trades.filter((t) => t.win);
  const losses = trades.filter((t) => !t.win);
  const totalGained = wins.reduce((sum, t) => sum + t.pnl_dollars, 0);
  const totalLost = Math.abs(losses.reduce((sum, t) => sum + t.pnl_dollars, 0));
  const netPnl = totalGained - totalLost;

  const byDay: Record<string, number> = {};
  for (const t of trades) {
    const day = t.timestamp.slice(0, 10);
    byDay[day] = (byDay[day] || 0) + t.pnl_dollars;
  }
  const dayPnls = Object.values(byDay);

  return {
    totalTrades: trades.length,
    wins: wins.length,
    losses: losses.length,
    successRate: trades.length ? (wins.length / trades.length) * 100 : 0,
    totalGained,
    totalLost,
    netPnl,
    bestDayPnl: dayPnls.length ? Math.max(...dayPnls) : 0,
    worstDayPnl: dayPnls.length ? Math.min(...dayPnls) : 0,
    hitProfitCap: dayPnls.some((p) => p >= PROFIT_CAP),
    hitLossCap: dayPnls.some((p) => p <= -LOSS_CAP),
  };
}

export const RATE_LIMITS = { PROFIT_CAP, LOSS_CAP };
