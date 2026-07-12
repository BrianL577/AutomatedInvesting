import { promises as fs } from "fs";
import path from "path";
import type { Trade } from "./types";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

const MAX_TRADES = 50_000;

async function loadTradesFromSupabase(): Promise<Trade[] | null> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null;
  try {
    const all: Trade[] = [];
    // Supabase's PostgREST API caps every request at 1000 rows regardless of
    // the requested `limit`, so this must page in matching 1000-row chunks.
    const pageSize = 1_000;
    for (let offset = 0; offset < MAX_TRADES; offset += pageSize) {
      const res = await fetch(
        `${SUPABASE_URL.replace(/\/$/, "")}/rest/v1/trades?select=*&order=timestamp.asc&limit=${pageSize}&offset=${offset}`,
        {
          headers: {
            apikey: SUPABASE_ANON_KEY,
            Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
          },
          cache: "no-store",
        }
      );
      if (!res.ok) return null;
      const page = (await res.json()) as Trade[];
      all.push(...page);
      if (page.length < pageSize) break;
    }
    return all;
  } catch {
    return null;
  }
}

async function loadTradesFromFile(): Promise<Trade[]> {
  const filePath = path.join(process.cwd(), "data", "trades.json");
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    const trades: Trade[] = JSON.parse(raw);
    return trades.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  } catch {
    return [];
  }
}

/** Prefers Supabase (live data) when configured; falls back to the static
 * bundled JSON file otherwise. */
export async function loadTrades(): Promise<Trade[]> {
  const fromSupabase = await loadTradesFromSupabase();
  if (fromSupabase !== null) return fromSupabase;
  return loadTradesFromFile();
}

export const usingSupabase = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

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

// Connectivity test trades (source=connection_test / phase=test) are excluded
// from performance stats — they're not real strategy signals, just proof the
// pipeline can submit an order.
function isRealTrade(t: Trade): boolean {
  return t.source !== "connection_test" && t.phase !== "test";
}

export function computeStats(trades: Trade[]): Stats {
  const real = trades.filter(isRealTrade);
  const wins = real.filter((t) => t.win);
  const losses = real.filter((t) => !t.win);
  const totalGained = wins.reduce((sum, t) => sum + t.pnl_dollars, 0);
  const totalLost = Math.abs(losses.reduce((sum, t) => sum + t.pnl_dollars, 0));
  const netPnl = totalGained - totalLost;

  const byDay: Record<string, number> = {};
  for (const t of real) {
    const day = t.timestamp.slice(0, 10);
    byDay[day] = (byDay[day] || 0) + t.pnl_dollars;
  }
  const dayPnls = Object.values(byDay);

  return {
    totalTrades: real.length,
    wins: wins.length,
    losses: losses.length,
    successRate: real.length ? (wins.length / real.length) * 100 : 0,
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
