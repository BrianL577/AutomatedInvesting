/**
 * Deterministic backtest engine for strategy configs (see strategySchema.ts).
 *
 * A TypeScript port of the Python engine in jj_bot/strategy.py +
 * jj_bot/backtest.py, generalized so any config matching the schema can run:
 * session-open anchor, continuation/reversion phase windows, true-range
 * displacement detection, swing-pivot break-of-structure with a noise buffer,
 * fixed-R:R bracket exits, per-day trade caps, and the daily $ rate limiter.
 *
 * Reports results the prop-firm way as well: simulated evaluation attempts
 * against an end-of-day trailing-drawdown account (pass rate), not just a
 * naive equity curve.
 */
import { StrategyConfig, DOLLARS_PER_POINT } from "./strategySchema";

export type Bar = {
  t: string; // ISO timestamp
  o: number;
  h: number;
  l: number;
  c: number;
  v?: number;
};

export type SimTrade = {
  entryTime: string;
  exitTime: string;
  phase: "continuation" | "reversion";
  direction: "long" | "short";
  entry: number;
  exit: number;
  stop: number;
  target: number;
  win: boolean;
  pnlPoints: number;
  pnlDollars: number;
  reason: string;
};

export type BacktestResult = {
  trades: SimTrade[];
  totalTrades: number;
  wins: number;
  losses: number;
  winRate: number; // 0-100
  totalGained: number; // $
  totalLost: number; // $ (positive number)
  netPnl: number; // $
  netPnlPct: number; // % of eval account size
  totalPoints: number;
  tradingDays: number;
  profitableDays: number;
  bestDay: number; // $
  worstDay: number; // $
  maxDrawdown: number; // $ peak-to-trough on the running equity curve
  evalAttempts: number;
  evalPasses: number;
  evalPassRate: number; // 0-100
  avgDaysToEvalResult: number;
  daysHitProfitCap: number;
  daysHitLossCap: number;
  // Real-world prop-firm economics: a single chronological walk through the
  // whole period (not the per-start-day probability sweep above), tracking
  // actual eval/reactivation fees paid and actual funded-stage cash payouts
  // received. See StrategyConfig.eval's fee/payout fields for the
  // assumptions this uses — verify against the real firm's current rules.
  realWorldFeesPaid: number; // $
  realWorldCashPayouts: number; // $
  realWorldNetPnl: number; // $ (payouts - fees; the actual money that changed hands)
  chronologicalAttempts: number; // how many eval attempts were actually bought, in order
  timesFunded: number; // how many times an attempt reached the funded stage
};

// ---------- time helpers (all wall-clock America/New_York) ----------

const ET_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function etParts(iso: string): { dateKey: string; minutes: number } {
  const parts = ET_FMT.formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  const hour = parseInt(get("hour"), 10) % 24;
  return {
    dateKey: `${get("year")}-${get("month")}-${get("day")}`,
    minutes: hour * 60 + parseInt(get("minute"), 10),
  };
}

function hhmmToMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// ---------- candle helpers ----------

const range = (b: Bar) => b.h - b.l;
const isGreen = (b: Bar) => b.c > b.o;
const wickRatio = (b: Bar) => {
  const r = range(b);
  if (r <= 0) return 1;
  const upper = b.h - Math.max(b.o, b.c);
  const lower = Math.min(b.o, b.c) - b.l;
  return (upper + lower) / r;
};
const trueRange = (b: Bar, prevClose: number | null) =>
  prevClose === null ? range(b) : Math.max(range(b), Math.abs(b.h - prevClose), Math.abs(b.l - prevClose));

// ---------- per-day simulation ----------

type Pivot = { minutes: number; price: number };

function simulateDay(dayBars: Bar[], cfg: StrategyConfig): SimTrade[] {
  const openMin = hhmmToMinutes(cfg.session.open);
  const cutoffMin = hhmmToMinutes(cfg.session.hardCutoff);
  const trades: SimTrade[] = [];

  let openPrice: number | null = null;
  let continuationDir: "long" | "short" | null = null;
  let consecutiveLosses = 0;
  let dayPnlDollars = 0;
  const pivotHighs: Pivot[] = [];
  const pivotLows: Pivot[] = [];
  const seen: Bar[] = [];
  let inTradeUntilIdx = -1;

  const updatePivots = (barMinutes: number) => {
    const s = cfg.entry.swingStrength;
    const idx = seen.length - 1 - s;
    if (idx < s) return;
    const window = seen.slice(idx - s, idx + s + 1);
    const center = seen[idx];
    const centerMin = etParts(center.t).minutes;
    if (center.h === Math.max(...window.map((b) => b.h))) pivotHighs.push({ minutes: centerMin, price: center.h });
    if (center.l === Math.min(...window.map((b) => b.l))) pivotLows.push({ minutes: centerMin, price: center.l });
    const cutoff = barMinutes - cfg.entry.structureLookbackMin;
    while (pivotHighs.length && pivotHighs[0].minutes < cutoff) pivotHighs.shift();
    while (pivotLows.length && pivotLows[0].minutes < cutoff) pivotLows.shift();
  };

  const isDisplacement = (i: number): boolean => {
    if (i === 0) return false;
    const lookback = seen.slice(Math.max(0, i - 10), i);
    if (!lookback.length) return false;
    const trs = lookback.map((b, j) => trueRange(b, j === 0 ? null : lookback[j - 1].c));
    const avgTr = trs.reduce((a, x) => a + x, 0) / trs.length;
    const bar = seen[i];
    const prev = seen[i - 1];
    const barTr = trueRange(bar, prev.c);
    const prevTr = trueRange(prev, i >= 2 ? seen[i - 2].c : null);
    if (avgTr <= 0 || prevTr <= 0) return false;
    if (barTr < cfg.entry.displacementSizeRatio * avgTr) return false;
    if (barTr < cfg.entry.displacementPrevRatio * prevTr) return false;
    if (wickRatio(bar) > cfg.entry.maxWickRatio) return false;
    return true;
  };

  const breakOfStructure = (bar: Bar, dir: "long" | "short"): number | null => {
    const buffer = cfg.entry.breakBufferPoints;
    if (dir === "short") {
      if (!pivotLows.length) return null;
      const level = Math.min(...pivotLows.map((p) => p.price));
      return bar.c < level - buffer ? level : null;
    }
    if (!pivotHighs.length) return null;
    const level = Math.max(...pivotHighs.map((p) => p.price));
    return bar.c > level + buffer ? level : null;
  };

  const simulateExit = (entryIdx: number, dir: "long" | "short", stop: number, target: number) => {
    for (let j = entryIdx + 1; j < dayBars.length; j++) {
      const b = dayBars[j];
      const hitStop = dir === "long" ? b.l <= stop : b.h >= stop;
      const hitTarget = dir === "long" ? b.h >= target : b.l <= target;
      // Conservative: when both are touched inside one bar, assume stop first.
      if (hitStop) return { exit: stop, exitTime: b.t, exitIdx: j, win: false };
      if (hitTarget) return { exit: target, exitTime: b.t, exitIdx: j, win: true };
    }
    const last = dayBars[dayBars.length - 1];
    const entry = dayBars[entryIdx].c;
    const win = dir === "long" ? last.c > entry : last.c < entry;
    return { exit: last.c, exitTime: last.t, exitIdx: dayBars.length - 1, win };
  };

  for (let i = 0; i < dayBars.length; i++) {
    const bar = dayBars[i];
    const { minutes } = etParts(bar.t);
    seen.push(bar);
    updatePivots(minutes);

    if (openPrice === null) {
      if (minutes === openMin) {
        openPrice = bar.o;
        continuationDir = isGreen(bar) ? "long" : "short";
      }
      continue;
    }

    if (trades.length >= cfg.risk.maxTradesPerDay) break;
    if (consecutiveLosses >= cfg.risk.stopAfterConsecutiveLosses) break;
    if (cfg.risk.dailyProfitCap > 0 && dayPnlDollars >= cfg.risk.dailyProfitCap) break;
    if (cfg.risk.dailyLossCap > 0 && dayPnlDollars <= -cfg.risk.dailyLossCap) break;
    if (minutes >= cutoffMin) break;
    if (i <= inTradeUntilIdx) continue;

    const minsSinceOpen = minutes - openMin;
    let dir: "long" | "short" | null = null;
    let phase: "continuation" | "reversion" | null = null;
    let reason = "";

    if (cfg.phases.tradeContinuation && minsSinceOpen <= cfg.phases.continuationEndMin) {
      dir = continuationDir;
      phase = "continuation";
      reason = `Continuation of ${dir} opening flow`;
    } else if (cfg.phases.tradeReversion && minsSinceOpen <= cfg.phases.reversionEndMin) {
      const extension = bar.c - openPrice;
      if (Math.abs(extension) >= cfg.entry.minExtensionPoints) {
        dir = extension > 0 ? "short" : "long";
        phase = "reversion";
        reason = `Mean reversion toward open ${openPrice.toFixed(2)} (extended ${extension.toFixed(2)} pts)`;
      }
    } else if (minsSinceOpen > Math.max(cfg.phases.continuationEndMin, cfg.phases.reversionEndMin)) {
      break;
    }

    if (!dir || !phase) continue;
    if (!isDisplacement(seen.length - 1)) continue;
    const level = breakOfStructure(bar, dir);
    if (level === null) continue;

    const entry = bar.c;
    const stop = dir === "long" ? entry - cfg.risk.stopPoints : entry + cfg.risk.stopPoints;
    const target = dir === "long" ? entry + cfg.risk.targetPoints : entry - cfg.risk.targetPoints;
    const { exit, exitTime, exitIdx, win } = simulateExit(i, dir, stop, target);
    const pnlPoints = dir === "long" ? exit - entry : entry - exit;
    const pnlDollars = pnlPoints * DOLLARS_PER_POINT * cfg.risk.contractsPerTrade;

    trades.push({
      entryTime: bar.t,
      exitTime,
      phase,
      direction: dir,
      entry,
      exit,
      stop,
      target,
      win,
      pnlPoints: Math.round(pnlPoints * 100) / 100,
      pnlDollars: Math.round(pnlDollars * 100) / 100,
      reason: `${reason}, displacement + close through structure ${level.toFixed(2)}`,
    });

    consecutiveLosses = win ? 0 : consecutiveLosses + 1;
    dayPnlDollars += pnlDollars;
    inTradeUntilIdx = exitIdx;
  }

  return trades;
}

// ---------- full backtest + prop-firm eval simulation ----------

export function runBacktest(cfg: StrategyConfig, bars: Bar[]): BacktestResult {
  const byDay = new Map<string, Bar[]>();
  for (const b of bars) {
    const { dateKey } = etParts(b.t);
    if (!byDay.has(dateKey)) byDay.set(dateKey, []);
    byDay.get(dateKey)!.push(b);
  }
  const days = [...byDay.keys()].sort();

  const allTrades: SimTrade[] = [];
  const dailyPnl: number[] = [];
  let daysHitProfitCap = 0;
  let daysHitLossCap = 0;

  for (const day of days) {
    const dayBars = byDay.get(day)!.sort((a, b) => a.t.localeCompare(b.t));
    const trades = simulateDay(dayBars, cfg);
    allTrades.push(...trades);
    const dayPnl = trades.reduce((s, t) => s + t.pnlDollars, 0);
    dailyPnl.push(dayPnl);
    if (cfg.risk.dailyProfitCap > 0 && dayPnl >= cfg.risk.dailyProfitCap) daysHitProfitCap++;
    if (cfg.risk.dailyLossCap > 0 && dayPnl <= -cfg.risk.dailyLossCap) daysHitLossCap++;
  }

  // Prop-firm eval simulation: start a fresh attempt on each day and play
  // forward with end-of-day trailing drawdown until pass or bust.
  let attempts = 0;
  let passes = 0;
  const daysToResult: number[] = [];
  for (let start = 0; start < days.length; start++) {
    attempts++;
    let balance = cfg.eval.accountSize;
    let highWater = balance;
    let floor = balance - cfg.eval.trailingMaxDrawdown;
    let n = 0;
    for (let d = start; d < days.length; d++) {
      balance += dailyPnl[d];
      n++;
      if (balance <= floor) break; // busted
      if (balance > highWater) {
        highWater = balance;
        floor = Math.min(highWater - cfg.eval.trailingMaxDrawdown, cfg.eval.accountSize);
      }
      if (balance >= cfg.eval.accountSize + cfg.eval.profitTarget) {
        passes++;
        break;
      }
    }
    daysToResult.push(n);
  }

  // Real-world economics: one chronological pass through the whole period.
  // Buy an eval ($evalFeeDollars); on bust, pay $reactivationFeeDollars and
  // start a new attempt the next day; on reaching the profit target, switch
  // to "funded" and keep playing the same trailing-drawdown rule forward.
  // Once funded cumulative profit crosses fundedProfitThreshold, take one
  // payout of min(maxPayoutPerEvent, profit * payoutShareRatio) and reset
  // the funded high-water mark to current balance (so further profit above
  // that can trigger another payout later) — a simplification of real
  // prop-firm payout windows/scaling, not an exact model of any one firm.
  const evalFee = cfg.eval.evalFeeDollars ?? 50;
  const reactivationFee = cfg.eval.reactivationFeeDollars ?? 50;
  const fundedThreshold = cfg.eval.fundedProfitThreshold ?? 3000;
  const payoutShare = cfg.eval.payoutShareRatio ?? 0.5;
  const maxPayout = cfg.eval.maxPayoutPerEvent ?? 2000;

  let feesPaid = 0;
  let cashPayouts = 0;
  let chronologicalAttempts = 0;
  let timesFunded = 0;
  let d = 0;
  let firstAttempt = true;
  while (d < days.length) {
    chronologicalAttempts++;
    feesPaid += firstAttempt ? evalFee : reactivationFee;
    firstAttempt = false;

    let balance = cfg.eval.accountSize;
    let highWater = balance;
    let floor = balance - cfg.eval.trailingMaxDrawdown;
    let funded = false;
    let fundedHighWater = 0;
    let fundedWindowDailyPnls: number[] = [];
    let busted = false;

    for (; d < days.length; d++) {
      balance += dailyPnl[d];
      if (funded) fundedWindowDailyPnls.push(dailyPnl[d]);
      if (balance <= floor) {
        busted = true;
        d++;
        break;
      }
      if (balance > highWater) {
        highWater = balance;
        floor = Math.min(highWater - cfg.eval.trailingMaxDrawdown, cfg.eval.accountSize);
      }
      if (!funded && balance >= cfg.eval.accountSize + cfg.eval.profitTarget) {
        funded = true;
        timesFunded++;
        fundedHighWater = balance;
        fundedWindowDailyPnls = [];
      }
      if (funded) {
        const fundedProfit = balance - fundedHighWater;
        if (fundedProfit >= fundedThreshold) {
          // 50% consistency rule: no single day in this funded window can
          // account for more than half the window's total profit, or the
          // payout doesn't qualify yet — keep accruing until it does.
          const maxSingleDayProfit = Math.max(0, ...fundedWindowDailyPnls);
          const consistent = maxSingleDayProfit <= fundedProfit * 0.5;
          if (consistent) {
            const payout = Math.min(maxPayout, fundedProfit * payoutShare);
            cashPayouts += payout;
            fundedHighWater = balance; // reset so further profit can trigger another payout
            fundedWindowDailyPnls = [];
          }
        }
      }
    }
    if (!busted) break; // ran out of data mid-attempt, nothing more to simulate
  }

  const wins = allTrades.filter((t) => t.win);
  const losses = allTrades.filter((t) => !t.win);
  const totalGained = wins.reduce((s, t) => s + t.pnlDollars, 0);
  const totalLost = Math.abs(losses.reduce((s, t) => s + t.pnlDollars, 0));
  const netPnl = totalGained - totalLost;

  let equity = 0;
  let peak = 0;
  let maxDrawdown = 0;
  for (const t of allTrades) {
    equity += t.pnlDollars;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, peak - equity);
  }

  const round2 = (x: number) => Math.round(x * 100) / 100;
  return {
    trades: allTrades,
    totalTrades: allTrades.length,
    wins: wins.length,
    losses: losses.length,
    winRate: allTrades.length ? round2((wins.length / allTrades.length) * 100) : 0,
    totalGained: round2(totalGained),
    totalLost: round2(totalLost),
    netPnl: round2(netPnl),
    netPnlPct: round2((netPnl / cfg.eval.accountSize) * 100),
    totalPoints: round2(allTrades.reduce((s, t) => s + t.pnlPoints, 0)),
    tradingDays: days.length,
    profitableDays: dailyPnl.filter((p) => p > 0).length,
    bestDay: dailyPnl.length ? round2(Math.max(...dailyPnl)) : 0,
    worstDay: dailyPnl.length ? round2(Math.min(...dailyPnl)) : 0,
    maxDrawdown: round2(maxDrawdown),
    evalAttempts: attempts,
    evalPasses: passes,
    evalPassRate: attempts ? round2((passes / attempts) * 100) : 0,
    avgDaysToEvalResult: daysToResult.length
      ? round2(daysToResult.reduce((a, x) => a + x, 0) / daysToResult.length)
      : 0,
    daysHitProfitCap,
    daysHitLossCap,
    realWorldFeesPaid: round2(feesPaid),
    realWorldCashPayouts: round2(cashPayouts),
    realWorldNetPnl: round2(cashPayouts - feesPaid),
    chronologicalAttempts,
    timesFunded,
  };
}
