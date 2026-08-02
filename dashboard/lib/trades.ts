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
    hitProfitCap: dayPnls.some((p) => p >= PROFIT_CAP),
    hitLossCap: dayPnls.some((p) => p <= -LOSS_CAP),
  };
}

export const RATE_LIMITS = { PROFIT_CAP, LOSS_CAP };

// ---- TopStep eval/funded account simulator --------------------------------
//
// Per-account state machine, replayed trade-by-trade over each account's
// real P&L: starts in "eval", needs +$3,000 to pass and become "funded"
// (balance resets to $0 on the pass). A funded account that drawns down to
// -$2,000 is lost — both the funded account and the eval that earned it —
// and trading resumes from a freshly purchased eval ($100) back at $0.
export type AccountStage = "eval" | "funded";

export type AccountSim = {
  account: string;
  stage: AccountStage;
  balance: number;
  evalsPurchased: number;
  fundedPasses: number;
  fundedLosses: number;
  feesPaid: number;
  tradeCount: number;
};

export const EVAL_SIM = {
  EVAL_TARGET: 3000,
  FUNDED_LOSS_FLOOR: -2000,
  EVAL_COST: 100,
};

export function simulateAccounts(trades: Trade[]): AccountSim[] {
  const real = trades.filter(isRealTrade).filter((t) => t.account_name);
  const byAccount = new Map<string, Trade[]>();
  for (const t of real) {
    const key = t.account_name as string;
    if (!byAccount.has(key)) byAccount.set(key, []);
    byAccount.get(key)!.push(t);
  }

  const sims: AccountSim[] = [];
  for (const [account, accountTrades] of byAccount) {
    let stage: AccountStage = "eval";
    let balance = 0;
    let evalsPurchased = 1;
    let fundedPasses = 0;
    let fundedLosses = 0;

    for (const t of accountTrades) {
      balance += t.pnl_dollars;
      if (stage === "eval" && balance >= EVAL_SIM.EVAL_TARGET) {
        stage = "funded";
        fundedPasses += 1;
        balance = 0;
      } else if (stage === "funded" && balance <= EVAL_SIM.FUNDED_LOSS_FLOOR) {
        stage = "eval";
        fundedLosses += 1;
        evalsPurchased += 1;
        balance = 0;
      }
    }

    sims.push({
      account,
      stage,
      balance,
      evalsPurchased,
      fundedPasses,
      fundedLosses,
      feesPaid: evalsPurchased * EVAL_SIM.EVAL_COST,
      tradeCount: accountTrades.length,
    });
  }

  return sims.sort((a, b) => a.account.localeCompare(b.account));
}
