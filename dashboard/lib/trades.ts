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
// Per-account state machine, replayed day-by-day over each account's real
// P&L, using the SAME mechanics as the Strategy Creator's backtest economics
// (dashboard/lib/backtester.ts's walkAccountEconomics / jj_bot/topstep_eval_sim.py)
// so both pages agree on what "passed", "funded", and "busted" actually mean:
//
//  - Balance is tracked relative to the eval, starting at $0 (equivalent to
//    walkAccountEconomics' accountSize-based balance, just shifted so $0 =
//    breakeven, matching what's shown here).
//  - Eval profit target ESCALATES: effectiveTarget = max($3,000, best single
//    day so far / 0.5) — a big day raises the bar for the rest of that
//    attempt (Topstep's real Consistency Target rule).
//  - Bust/drawdown is a TRAILING high-water-mark floor (peak - $2,000),
//    capped so it never rises above breakeven ($0) — NOT a flat -$2,000 from
//    zero. Once $2,000+ profitable, the floor locks at breakeven.
//  - Funded payouts require 5 winning days of $150+ net (Standard path
//    only — the Consistency path is deliberately not modeled, per explicit
//    user choice), capped at $4,000 (PROMO VALUE, base $2,000 — see
//    JJ_DEFAULT_STRATEGY.eval.maxPayoutPerEvent in strategySchema.ts) or
//    50% of balance, 90% to the trader. The drawdown buffer resets to $0
//    (no cushion) immediately after a payout.
//  - Cost per eval attempt: a flat $85 (PROMO PRICE, may change — Topstep's
//    real reset fee for this account type). The ongoing $85/mo subscription
//    runs separately regardless of busts; see backtester.ts's
//    walkAccountEconomics for the full monthly-billing simulation. No
//    separate activation fee — Topstep's Express Funded Activation Fee is
//    currently free.
export type AccountStage = "eval" | "funded";

export type AccountSim = {
  account: string;
  stage: AccountStage;
  balance: number;
  effectiveProfitTarget: number; // current eval target (escalates); n/a once funded
  floor: number; // current bust line, relative to the $0 breakeven baseline
  evalsPurchased: number;
  fundedPasses: number;
  fundedLosses: number;
  payoutsReceived: number;
  cashPayouts: number;
  feesPaid: number;
  netResult: number; // cashPayouts - feesPaid: the real-money bottom line
  tradingDays: number;
};

// Moved to evalSimConfig.ts (a pure, `fs`-free module) so client components
// can import EVAL_SIM without pulling this file's Node `fs` dependency
// (loadTradesFromFile, below) into the browser bundle — confirmed this was
// a real webpack build failure, not just a style preference.
export { EVAL_SIM } from "./evalSimConfig";
import { EVAL_SIM } from "./evalSimConfig";

/** ET calendar-day key (YYYY-MM-DD) for grouping trades into the daily P&L
 * series the economics model operates on — matches the Strategy Creator's
 * day-grouping timezone so both pages bucket the same trade into the same
 * trading day. */
function etDateKey(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

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
    const byDay = new Map<string, number>();
    for (const t of accountTrades) {
      const key = etDateKey(t.timestamp);
      byDay.set(key, (byDay.get(key) ?? 0) + t.pnl_dollars);
    }
    const days = [...byDay.keys()].sort();
    const series = days.map((day) => byDay.get(day)!);

    let evalsPurchased = 0;
    let fundedPasses = 0;
    let fundedLosses = 0;
    let feesPaid = 0;
    let cashPayouts = 0;
    let fundedAttemptsWithPayout = 0;

    let d = 0;
    let funded = false;
    let balance = 0;
    let highWater = 0;
    let floor = -EVAL_SIM.TRAILING_DRAWDOWN;
    let effectiveProfitTarget = EVAL_SIM.EVAL_TARGET;

    while (d < series.length) {
      evalsPurchased += 1;
      feesPaid += EVAL_SIM.EVAL_COST;

      balance = 0;
      highWater = 0;
      floor = -EVAL_SIM.TRAILING_DRAWDOWN;
      funded = false;
      let bestDaySoFar = 0;
      effectiveProfitTarget = EVAL_SIM.EVAL_TARGET;
      let winningDaysSincePayout = 0;
      let profitSincePayout = 0;
      let daysSincePayout = 0;
      let bestDaySincePayout = -Infinity;
      let balanceAtLastPayout = 0;
      let thisAttemptGotPayout = false;
      let busted = false;

      for (; d < series.length; d++) {
        const dayPnl = series[d];
        balance += dayPnl;
        if (balance <= floor) {
          busted = true;
          d += 1;
          break;
        }
        if (balance > highWater) {
          highWater = balance;
          floor = Math.min(highWater - EVAL_SIM.TRAILING_DRAWDOWN, 0);
        }
        if (!funded && dayPnl > bestDaySoFar) {
          bestDaySoFar = dayPnl;
          effectiveProfitTarget = Math.max(EVAL_SIM.EVAL_TARGET, bestDaySoFar / 0.5);
        }
        if (!funded && balance >= effectiveProfitTarget) {
          funded = true;
          fundedPasses += 1;
          winningDaysSincePayout = 0;
          profitSincePayout = 0;
          daysSincePayout = 0;
          bestDaySincePayout = -Infinity;
          balanceAtLastPayout = balance;
        }
        if (funded) {
          profitSincePayout += dayPnl;
          daysSincePayout += 1;
          if (dayPnl > bestDaySincePayout) bestDaySincePayout = dayPnl;
          if (dayPnl >= EVAL_SIM.MIN_WINNING_DAY_PROFIT) winningDaysSincePayout += 1;

          // Standard path only (Option 1) — per explicit user choice, the
          // Consistency path (Option 2) is deliberately not modeled.
          const standardPathEligible = winningDaysSincePayout >= EVAL_SIM.MIN_WINNING_DAYS_FOR_PAYOUT;
          const aboveLastPayoutBalance = balance > balanceAtLastPayout;

          if (standardPathEligible && aboveLastPayoutBalance) {
            const payout = Math.max(
              0,
              Math.min(EVAL_SIM.MAX_PAYOUT, profitSincePayout * EVAL_SIM.PAYOUT_SHARE, balance * EVAL_SIM.MAX_PAYOUT_BALANCE_SHARE)
            );
            if (payout > 0) {
              cashPayouts += payout;
              balance -= payout;
              balanceAtLastPayout = balance;
              if (!thisAttemptGotPayout) {
                fundedAttemptsWithPayout += 1;
                thisAttemptGotPayout = true;
              }
              // Real rule: the drawdown buffer resets to $0 the moment funds
              // are withdrawn — zero cushion until balance climbs again.
              floor = balance;
              highWater = balance;
            }
            winningDaysSincePayout = 0;
            profitSincePayout = 0;
            daysSincePayout = 0;
            bestDaySincePayout = -Infinity;
          }
        }
      }

      if (busted && funded) fundedLosses += 1;
      if (!busted) break; // ran out of trade history mid-attempt — this is the live, ongoing state
    }

    sims.push({
      account,
      stage: funded ? "funded" : "eval",
      balance,
      effectiveProfitTarget,
      floor,
      evalsPurchased,
      fundedPasses,
      fundedLosses,
      payoutsReceived: fundedAttemptsWithPayout,
      cashPayouts,
      feesPaid,
      netResult: cashPayouts - feesPaid,
      tradingDays: series.length,
    });
  }

  return sims.sort((a, b) => a.account.localeCompare(b.account));
}
