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
  // The "HH:MM" ET session-open anchor this trade belongs to (session.open,
  // or one of session.additionalSessions) — lets results be broken down by
  // which session window produced them.
  sessionOpen: string;
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

/** Win rate / P&L broken down by a grouping key (phase or session), so a
 * caller can see e.g. "reversion trades are dragging win rate down" or
 * "the 8pm Asian session is unprofitable" instead of only a pooled total. */
export type GroupBreakdown = {
  key: string;
  trades: number;
  wins: number;
  losses: number;
  winRate: number; // 0-100
  netPnl: number; // $
};

export type StageSummary = {
  trades: number;
  wins: number;
  losses: number;
  winRate: number; // 0-100
  totalGained: number; // $
  totalLost: number; // $ (positive number)
  netPnl: number; // $
  tradingDays: number;
};

export type BacktestResult = {
  trades: SimTrade[];
  // Trades where the bar data ran out before the bracket hit its stop or
  // target — excluded from every stat below rather than force-closed at a
  // made-up price, since the strategy's rule is stop-or-target-only, never
  // an early/discretionary exit.
  incompleteTrades: number;
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
  // The actual calendar span of historical data this backtest ran against
  // (ET calendar dates of the first/last trading day with data) — every $
  // figure above is total-over-this-period, NOT per-year, unless you divide
  // by dataRangeYears yourself. null when there were 0 trading days.
  dataRangeStart: string | null; // "YYYY-MM-DD"
  dataRangeEnd: string | null; // "YYYY-MM-DD"
  dataRangeCalendarDays: number; // end - start, inclusive
  dataRangeYears: number; // dataRangeCalendarDays / 365.25, for framing $ totals
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
  // Trading performance split by account phase (using the single
  // non-portfolio account's chronological timeline) — NOT pooled across
  // account resets like totalGained/totalLost/netPnl above. This answers
  // "how did the strategy actually perform while still in eval" vs "how did
  // it perform once funded" as two separate simulations.
  evalStage: StageSummary;
  fundedStage: StageSummary;
  // Win rate / P&L split by entry phase and by session-open anchor, computed
  // over all pooled trades — helps identify e.g. "reversion trades are
  // dragging win rate below breakeven" or "the 8pm session is unprofitable"
  // without having to eyeball the raw trade list.
  byPhase: GroupBreakdown[];
  bySession: GroupBreakdown[];
  // Portfolio-of-accounts economics (null unless the strategy config sets
  // portfolio.accountCount > 1): N staggered accounts, each with its own
  // eval lifecycle, optionally restricted to one trade per day.
  portfolio: {
    accountCount: number;
    staggerDays: number;
    oneTradePerDay: boolean;
    feesPaid: number;
    cashPayouts: number;
    netPnl: number;
    attemptsBought: number;
    timesFunded: number;
  } | null;
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

/** Day-level risk state shared across every session window in one day:
 * trade caps, loss caps, and consecutive-loss stops are daily rules, so a
 * loss in the London window still counts against the NY window. */
type DayRiskState = { tradesCount: number; consecutiveLosses: number; dayPnlDollars: number };

/** The strategy's session windows (main open + additionalSessions), in
 * chronological order. Shared by simulateDay (all sessions, one account)
 * and runSessionSplitBacktest (each account restricted to one window). */
function getSessionWindows(cfg: StrategyConfig): { open: string; hardCutoff: string }[] {
  return [
    { open: cfg.session.open, hardCutoff: cfg.session.hardCutoff },
    ...(cfg.session.additionalSessions ?? []),
  ].sort((a, b) => hhmmToMinutes(a.open) - hhmmToMinutes(b.open));
}

function simulateDay(dayBars: Bar[], cfg: StrategyConfig): { trades: SimTrade[]; incompleteTrades: number } {
  const windows = getSessionWindows(cfg);

  const state: DayRiskState = { tradesCount: 0, consecutiveLosses: 0, dayPnlDollars: 0 };
  const trades: SimTrade[] = [];
  let incompleteTrades = 0;
  for (const w of windows) {
    const result = simulateSession(dayBars, cfg, hhmmToMinutes(w.open), hhmmToMinutes(w.hardCutoff), state, w.open);
    trades.push(...result.trades);
    incompleteTrades += result.incompleteTrades;
  }
  return { trades, incompleteTrades };
}

function simulateSession(
  dayBars: Bar[],
  cfg: StrategyConfig,
  openMin: number,
  cutoffMin: number,
  state: DayRiskState,
  sessionOpen: string
): { trades: SimTrade[]; incompleteTrades: number } {
  const trades: SimTrade[] = [];
  let incompleteTrades = 0;

  let openPrice: number | null = null;
  let continuationDir: "long" | "short" | null = null;
  const pivotHighs: Pivot[] = [];
  const pivotLows: Pivot[] = [];
  const seen: Bar[] = [];
  let inTradeUntilIdx = -1;

  // Skip bars well before this window's open — with near-continuous 1-min
  // data, a day's bars can span ~1,380 rows, but a later session (e.g.
  // 20:00 ET) only ever needs bars from shortly before its own open
  // onward (enough lookback for structure/pivots and the displacement
  // average). Without this, every bar from the start of the day gets
  // pushed through seen/updatePivots for nothing — wasted on its own, and
  // multiplied by however many accounts/sessions call this per day in a
  // session-split multi-account backtest.
  const lookbackBuffer = cfg.entry.structureLookbackMin + 10;
  const startIdx = dayBars.findIndex((b) => etParts(b.t).minutes >= openMin - lookbackBuffer);
  const scanStart = startIdx === -1 ? dayBars.length : startIdx;

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

  // Per JJ's rule: the bracket is never moved, and a trade only ends by
  // hitting its full stop or full target — never a discretionary or
  // end-of-day flatten. If the data runs out before either is touched, the
  // trade is unresolved: it is excluded from results rather than faked with
  // a made-up exit price, exactly as a real bracket order would just stay
  // open past the bars we have.
  const simulateExit = (
    entryIdx: number,
    dir: "long" | "short",
    stop: number,
    target: number
  ): { exit: number; exitTime: string; exitIdx: number; win: boolean } | null => {
    for (let j = entryIdx + 1; j < dayBars.length; j++) {
      const b = dayBars[j];
      const hitStop = dir === "long" ? b.l <= stop : b.h >= stop;
      const hitTarget = dir === "long" ? b.h >= target : b.l <= target;
      // Conservative: when both are touched inside one bar, assume stop first.
      if (hitStop) return { exit: stop, exitTime: b.t, exitIdx: j, win: false };
      if (hitTarget) return { exit: target, exitTime: b.t, exitIdx: j, win: true };
    }
    return null;
  };

  for (let i = scanStart; i < dayBars.length; i++) {
    const bar = dayBars[i];
    const { minutes } = etParts(bar.t);
    seen.push(bar);
    updatePivots(minutes);

    if (openPrice === null) {
      // First bar AT OR AFTER the session open anchors "fair price" — not
      // an exact-equality match. A gap in the historical data at exactly
      // openMin (a missing 1-minute bar) would otherwise skip anchoring
      // for the entire day, same bug confirmed live in strategy.py. Always
      // `continue` here (same as before) so the anchor bar itself is never
      // also evaluated as a possible entry in this same iteration.
      if (minutes >= openMin) {
        openPrice = bar.o;
        continuationDir = isGreen(bar) ? "long" : "short";
      }
      continue;
    }

    if (state.tradesCount >= cfg.risk.maxTradesPerDay) break;
    if (state.consecutiveLosses >= cfg.risk.stopAfterConsecutiveLosses) break;
    if (cfg.risk.dailyProfitCap > 0 && state.dayPnlDollars >= cfg.risk.dailyProfitCap) break;
    if (cfg.risk.dailyLossCap > 0 && state.dayPnlDollars <= -cfg.risk.dailyLossCap) break;
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
    const resolved = simulateExit(i, dir, stop, target);
    if (resolved === null) {
      // Ran out of bars before the bracket resolved — exclude, don't fake
      // an exit. No further trades can be evaluated this day/session since
      // this one is still open as far as we know.
      incompleteTrades++;
      break;
    }
    const { exit, exitTime, exitIdx, win } = resolved;
    const pnlPoints = dir === "long" ? exit - entry : entry - exit;
    const pnlDollars = pnlPoints * DOLLARS_PER_POINT * cfg.risk.contractsPerTrade;

    trades.push({
      entryTime: bar.t,
      exitTime,
      phase,
      sessionOpen,
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

    state.tradesCount++;
    state.consecutiveLosses = win ? 0 : state.consecutiveLosses + 1;
    state.dayPnlDollars += pnlDollars;
    inTradeUntilIdx = exitIdx;
  }

  return { trades, incompleteTrades };
}

// ---------- full backtest + prop-firm eval simulation ----------

/** One account's full chronological lifecycle over a daily P&L series: buy
 * a Combine (monthly subscription, not a one-time fee), bust -> restart
 * next day (still billed monthly), pass -> pay the one-time funded
 * activation fee and enter the funded stage with day-count-based payout
 * eligibility. `startDay` lets staggered/portfolio accounts begin partway
 * through the series. Module-level (not a runBacktest closure) so it can
 * be reused by session-split multi-account backtests too.
 *
 * Payout mechanic matches Topstep's actual Standard Path rule (confirmed
 * via Topstep's help center, not guessed): a payout becomes eligible once
 * minWinningDaysForPayout days (default 5) each cleared
 * minWinningDayProfit (default $150) net P&L — NOT a cumulative-dollar
 * threshold. The payout itself is min(maxPayoutPerEvent, profit-since-
 * last-payout x payoutShareRatio) — payoutShareRatio defaults to 0.9 (90%
 * trader / 10% Topstep, the flat split for accounts opened on/after Jan
 * 12, 2026). After every payout, the winning-day counter AND the trailing
 * drawdown buffer both reset — Topstep's real rule is that your Maximum
 * Loss Limit resets to $0 the moment funds are withdrawn, so the very next
 * losing day can bust the account outright with zero cushion. */
function walkAccountEconomics(cfg: StrategyConfig, series: number[], startDay: number) {
  // TWO INDEPENDENT, ADDITIVE fee streams, per direct user confirmation:
  // 1) A per-attempt fee charged every time you start or restart a Combine
  //    attempt — busting and resetting costs this again ($49, both the
  //    initial purchase and every reactivation).
  // 2) A SEPARATE, continuous monthly subscription charge that accrues
  //    regardless of busts/restarts, for as long as you're still in eval
  //    ($49 every ~21 trading days). Pauses once funded (the one-time
  //    activation fee applies instead), resumes if a funded account later
  //    busts and a new Combine is purchased.
  const evalFee = cfg.eval.evalFeeDollars ?? 49;
  const reactivationFee = cfg.eval.reactivationFeeDollars ?? 49;
  const monthlyFee = cfg.eval.monthlyFeeDollars ?? 49;
  const activationFee = cfg.eval.fundedActivationFeeDollars ?? 149;
  const TRADING_DAYS_PER_MONTH = 21;
  const payoutShare = cfg.eval.payoutShareRatio ?? 0.9;
  const maxPayout = cfg.eval.maxPayoutPerEvent ?? 2000;
  const maxPayoutBalanceShare = cfg.eval.maxPayoutBalanceShare ?? 0.5;
  // Two independent funded-stage payout paths, confirmed by Topstep
  // support — NOT the same Consistency Target as the Combine/eval stage
  // (that one is profitTarget-based and does not apply once funded).
  const minWinningDaysForPayout = cfg.eval.minWinningDaysForPayout ?? 5;
  const minWinningDayProfit = cfg.eval.minWinningDayProfit ?? 150;
  const consistencyPathMinDays = cfg.eval.consistencyPathMinDays ?? 3;
  const consistencyPathMaxBestDayShare = cfg.eval.consistencyPathMaxBestDayShare ?? 0.4;

  let feesPaid = 0;
  let cashPayouts = 0;
  let attemptsBought = 0;
  let fundedCount = 0;
  // Per-day phase for THIS account's timeline, index-aligned to `series`:
  // "eval" = this day's P&L happened while still working toward the profit
  // target; "funded" = after reaching it. Days before startDay or after the
  // account permanently busts with no more attempts are left "none".
  const dayPhase: ("eval" | "funded" | "none")[] = new Array(series.length).fill("none");
  let d = Math.min(startDay, series.length);
  // The monthly billing clock persists ACROSS eval bust/restart attempts
  // (declared outside the while loop, not reset each attempt) since it's a
  // separate charge from the per-attempt fee below — it only pauses while
  // funded, and only restarts (a genuinely new Combine) if a FUNDED
  // account later busts and a new Combine is purchased from scratch.
  let evalDaysSinceLastBill = TRADING_DAYS_PER_MONTH; // charge immediately on the very first attempt
  let needsFreshSubscription = false;
  let firstAttempt = true;
  while (d < series.length) {
    attemptsBought++;
    // Per-attempt fee: initial purchase, or a reactivation after busting —
    // charged every attempt regardless of the monthly clock above.
    feesPaid += firstAttempt ? evalFee : reactivationFee;
    firstAttempt = false;
    if (needsFreshSubscription) {
      evalDaysSinceLastBill = TRADING_DAYS_PER_MONTH;
      needsFreshSubscription = false;
    }

    let balance = cfg.eval.accountSize;
    let highWater = balance;
    let floor = balance - cfg.eval.trailingMaxDrawdown;
    let funded = false;
    let busted = false;
    let winningDaysSincePayout = 0;
    let profitSincePayout = 0;
    let daysSincePayout = 0;
    let bestDaySincePayout = -Infinity;
    // Topstep Consistency Target, exact formula confirmed by a Topstep
    // trader: New Profit Target = Best Day / 0.5 (a full recalculation off
    // the single best day so far this attempt, not a step/proportional
    // buffer). Effectively forces total eval profit to be >= 2x your best
    // day. Only escalates upward — a big single day raises the bar for the
    // rest of THIS eval attempt, resets on a fresh attempt. CONFIRMED (by
    // Topstep support) this applies ONLY to the Combine/eval stage — a
    // funded account has no profit target at all, so this never applies
    // once `funded` is true; funded payout eligibility uses the entirely
    // separate Standard/Consistency paths below instead.
    let bestDaySoFar = 0;
    let effectiveProfitTarget = cfg.eval.profitTarget;

    for (; d < series.length; d++) {
      dayPhase[d] = funded ? "funded" : "eval";
      if (!funded) {
        evalDaysSinceLastBill++;
        if (evalDaysSinceLastBill >= TRADING_DAYS_PER_MONTH) {
          feesPaid += monthlyFee;
          evalDaysSinceLastBill = 0;
        }
      }
      const dayPnl = series[d];
      balance += dayPnl;
      if (balance <= floor) {
        busted = true;
        d++;
        break;
      }
      if (balance > highWater) {
        highWater = balance;
        floor = Math.min(highWater - cfg.eval.trailingMaxDrawdown, cfg.eval.accountSize);
      }
      if (!funded && dayPnl > bestDaySoFar) {
        bestDaySoFar = dayPnl;
        effectiveProfitTarget = Math.max(cfg.eval.profitTarget, bestDaySoFar / 0.5);
      }
      if (!funded && balance >= cfg.eval.accountSize + effectiveProfitTarget) {
        funded = true;
        fundedCount++;
        feesPaid += activationFee;
        winningDaysSincePayout = 0;
        profitSincePayout = 0;
      }
      if (funded) {
        profitSincePayout += dayPnl;
        daysSincePayout++;
        if (dayPnl > bestDaySincePayout) bestDaySincePayout = dayPnl;
        if (dayPnl >= minWinningDayProfit) winningDaysSincePayout++;

        // Standard path: N winning days of $X+ each.
        const standardPathEligible = winningDaysSincePayout >= minWinningDaysForPayout;
        // Consistency path: as few as N trading days, as long as the best
        // single day stays under the share cap of total profit so far —
        // faster, but only usable while genuinely well-distributed.
        const consistencyPathEligible =
          daysSincePayout >= consistencyPathMinDays &&
          profitSincePayout > 0 &&
          bestDaySincePayout <= profitSincePayout * consistencyPathMaxBestDayShare;

        if (standardPathEligible || consistencyPathEligible) {
          const payout = Math.max(
            0,
            Math.min(maxPayout, profitSincePayout * payoutShare, balance * maxPayoutBalanceShare)
          );
          if (payout > 0) {
            cashPayouts += payout;
            // Real rule: Maximum Loss Limit resets to $0 the moment funds
            // are withdrawn — zero drawdown buffer until the balance
            // climbs again, so the very next losing day can bust the
            // account outright.
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
    // If this attempt reached funded before busting, the next attempt (if
    // any) is a brand-new Combine purchase, not a free eval reset.
    if (funded) needsFreshSubscription = true;
    if (!busted) break; // ran out of data mid-attempt, nothing more to simulate
  }
  return { feesPaid, cashPayouts, attemptsBought, fundedCount, dayPhase };
}

/** Groups bars by ET calendar day, dropping any date in
 * cfg.filters.excludeDates (no reliable offline economic calendar, so
 * verified news dates must come from the user). Shared by runBacktest and
 * runSessionSplitBacktest. */
function groupBarsByDay(cfg: StrategyConfig, bars: Bar[]): { byDay: Map<string, Bar[]>; days: string[] } {
  const byDay = new Map<string, Bar[]>();
  const excluded = new Set(cfg.filters?.excludeDates ?? []);
  for (const b of bars) {
    const { dateKey } = etParts(b.t);
    if (excluded.has(dateKey)) continue;
    if (!byDay.has(dateKey)) byDay.set(dateKey, []);
    byDay.get(dateKey)!.push(b);
  }
  return { byDay, days: [...byDay.keys()].sort() };
}

export function runBacktest(cfg: StrategyConfig, bars: Bar[]): BacktestResult {
  const { byDay, days } = groupBarsByDay(cfg, bars);

  const allTrades: SimTrade[] = [];
  const dailyPnl: number[] = [];
  // Per-day P&L using only the first trade of each day — the sizing style
  // where each account takes one trade/day and scale comes from running
  // more accounts (see StrategyConfig.portfolio.oneTradePerDay).
  const dailyPnlFirstTradeOnly: number[] = [];
  let daysHitProfitCap = 0;
  let daysHitLossCap = 0;
  let incompleteTrades = 0;

  for (const day of days) {
    const dayBars = byDay.get(day)!.sort((a, b) => a.t.localeCompare(b.t));
    const { trades, incompleteTrades: dayIncomplete } = simulateDay(dayBars, cfg);
    incompleteTrades += dayIncomplete;
    allTrades.push(...trades);
    const dayPnl = trades.reduce((s, t) => s + t.pnlDollars, 0);
    dailyPnl.push(dayPnl);
    dailyPnlFirstTradeOnly.push(trades.length ? trades[0].pnlDollars : 0);
    if (cfg.risk.dailyProfitCap > 0 && dayPnl >= cfg.risk.dailyProfitCap) daysHitProfitCap++;
    if (cfg.risk.dailyLossCap > 0 && dayPnl <= -cfg.risk.dailyLossCap) daysHitLossCap++;
  }

  const round2 = (x: number) => Math.round(x * 100) / 100;

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
  // See walkAccountEconomics above for the fee/payout mechanics.
  const single = walkAccountEconomics(cfg, dailyPnl, 0);
  const feesPaid = single.feesPaid;
  const cashPayouts = single.cashPayouts;
  const chronologicalAttempts = single.attemptsBought;
  const timesFunded = single.fundedCount;

  // Split every trade into eval-stage vs funded-stage using the single
  // account's chronological day phases, so "Success Rate"/"Net P&L"/etc can
  // be reported separately for each — not pooled across account resets.
  const dayIndexByDate = new Map(days.map((day, i) => [day, i]));
  const evalTrades: SimTrade[] = [];
  const fundedTrades: SimTrade[] = [];
  for (const t of allTrades) {
    const idx = dayIndexByDate.get(etParts(t.entryTime).dateKey);
    const phase = idx === undefined ? "eval" : single.dayPhase[idx];
    (phase === "funded" ? fundedTrades : evalTrades).push(t);
  }
  const summarizeStage = (stageTrades: SimTrade[]) => {
    const w = stageTrades.filter((t) => t.win);
    const l = stageTrades.filter((t) => !t.win);
    const gained = w.reduce((s, t) => s + t.pnlDollars, 0);
    const lost = Math.abs(l.reduce((s, t) => s + t.pnlDollars, 0));
    const stageDays = new Set(stageTrades.map((t) => etParts(t.entryTime).dateKey)).size;
    return {
      trades: stageTrades.length,
      wins: w.length,
      losses: l.length,
      winRate: stageTrades.length ? round2((w.length / stageTrades.length) * 100) : 0,
      totalGained: round2(gained),
      totalLost: round2(lost),
      netPnl: round2(gained - lost),
      tradingDays: stageDays,
    };
  };
  const evalStage = summarizeStage(evalTrades);
  const fundedStage = summarizeStage(fundedTrades);

  // Portfolio of accounts, staggered starts, optionally one trade/day each —
  // how prop traders actually scale (more accounts, not more size per trade).
  let portfolio: BacktestResult["portfolio"] = null;
  if (cfg.portfolio && cfg.portfolio.accountCount > 1) {
    const series = cfg.portfolio.oneTradePerDay ? dailyPnlFirstTradeOnly : dailyPnl;
    let pFees = 0;
    let pPayouts = 0;
    let pAttempts = 0;
    let pFunded = 0;
    for (let a = 0; a < cfg.portfolio.accountCount; a++) {
      const w = walkAccountEconomics(cfg, series, a * cfg.portfolio.staggerDays);
      pFees += w.feesPaid;
      pPayouts += w.cashPayouts;
      pAttempts += w.attemptsBought;
      pFunded += w.fundedCount;
    }
    portfolio = {
      accountCount: cfg.portfolio.accountCount,
      staggerDays: cfg.portfolio.staggerDays,
      oneTradePerDay: cfg.portfolio.oneTradePerDay,
      feesPaid: Math.round(pFees * 100) / 100,
      cashPayouts: Math.round(pPayouts * 100) / 100,
      netPnl: Math.round((pPayouts - pFees) * 100) / 100,
      attemptsBought: pAttempts,
      timesFunded: pFunded,
    };
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

  const groupBy = (keyFn: (t: SimTrade) => string): GroupBreakdown[] => {
    const groups = new Map<string, SimTrade[]>();
    for (const t of allTrades) {
      const k = keyFn(t);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k)!.push(t);
    }
    return [...groups.entries()]
      .map(([key, ts]) => {
        const w = ts.filter((t) => t.win).length;
        return {
          key,
          trades: ts.length,
          wins: w,
          losses: ts.length - w,
          winRate: ts.length ? round2((w / ts.length) * 100) : 0,
          netPnl: round2(ts.reduce((s, t) => s + t.pnlDollars, 0)),
        };
      })
      .sort((a, b) => b.trades - a.trades);
  };
  const byPhase = groupBy((t) => t.phase);
  const bySession = groupBy((t) => t.sessionOpen);

  return {
    trades: allTrades,
    incompleteTrades,
    byPhase,
    bySession,
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
    dataRangeStart: days.length ? days[0] : null,
    dataRangeEnd: days.length ? days[days.length - 1] : null,
    dataRangeCalendarDays: days.length
      ? Math.round((new Date(days[days.length - 1]).getTime() - new Date(days[0]).getTime()) / 86400000) + 1
      : 0,
    dataRangeYears: days.length
      ? round2(
          ((new Date(days[days.length - 1]).getTime() - new Date(days[0]).getTime()) / 86400000 + 1) / 365.25
        )
      : 0,
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
    evalStage,
    fundedStage,
    portfolio,
  };
}

/** One account's results in a session-split multi-account backtest — every
 * stat here belongs to just this account's assigned session (and, for
 * accounts 0/1 when accountCount >= 2, a specific phase within it). */
export type SessionSplitAccount = {
  accountIndex: number;
  sessionOpen: string; // which session window (e.g. "09:30") this account trades
  phaseFilter: "continuation" | "reversion" | null; // null = trades the whole session, both phases
  trades: number;
  wins: number;
  losses: number;
  winRate: number; // 0-100
  pooledNetPnl: number; // $, raw sum of this account's trade P&L (not real economics)
  feesPaid: number; // $
  cashPayouts: number; // $
  realWorldNetPnl: number; // $ (cashPayouts - feesPaid) — the actual money this account made
  attemptsBought: number;
  timesFunded: number;
};

export type SessionSplitResult = {
  accountCount: number;
  sessions: string[]; // the strategy's own session-open times, in order
  accounts: SessionSplitAccount[];
  totalFeesPaid: number;
  totalCashPayouts: number;
  totalRealWorldNetPnl: number;
  totalTrades: number;
  incompleteTrades: number;
};

/**
 * Multi-account backtest where each account trades exactly one of the
 * strategy's session windows, round-robin: account 0 gets the first
 * session, account 1 the second, etc.; once every session has an account,
 * it wraps back to the first session again (so N accounts on M sessions
 * puts ceil(N/M) accounts on each session, as evenly as N/M divides —
 * "equally proportionate" per the user's rule). Each account is its own
 * independent eval/funded lifecycle (its own fees, payouts, bust/reset),
 * exactly as if it were a separate real account — this is NOT the same as
 * StrategyConfig.portfolio (staggered starts, every account trading every
 * session); this is a per-request backtest parameter, not saved config.
 *
 * SPECIAL CASE, per user rule: whenever accountCount >= 2, account 0 and
 * account 1 don't get separate sessions — they split the MAIN session
 * (cfg.session.open, e.g. 09:30 ET) by phase instead: account 0 trades
 * ONLY that session's continuation phase, account 1 trades ONLY its
 * reversion phase (the move back toward the session's opening/"fair"
 * price). This reuses the strategy's own phases.tradeContinuation/
 * tradeReversion toggles to filter entries, rather than any new mechanic.
 * Accounts 2+ round-robin across the strategy's OTHER sessions (since the
 * main session is now spoken for by accounts 0/1) — falling back to
 * round-robining every session (including the main one, both phases) if
 * the strategy has no other sessions configured.
 */
export function runSessionSplitBacktest(cfg: StrategyConfig, bars: Bar[], accountCount: number): SessionSplitResult {
  const round2 = (x: number) => Math.round(x * 100) / 100;
  const { byDay, days } = groupBarsByDay(cfg, bars);
  const sessionWindows = getSessionWindows(cfg);
  const M = sessionWindows.length;

  const mainWindow = sessionWindows.find((w) => w.open === cfg.session.open) ?? sessionWindows[0];
  const otherWindows = sessionWindows.filter((w) => w !== mainWindow);
  const roundRobinPool = otherWindows.length > 0 ? otherWindows : sessionWindows;
  const splitMainSession = accountCount >= 2;

  const accounts: SessionSplitAccount[] = [];
  let totalFeesPaid = 0;
  let totalCashPayouts = 0;
  let totalTrades = 0;
  let incompleteTrades = 0;

  // Every account is a pure function of (session window, phase filter) — two
  // accounts with the same pair produce byte-identical trades/economics
  // (no staggering here, unlike Portfolio). With one session configured,
  // every account past the first two shares the SAME pair, so without this
  // cache an N-account request redoes the full day-by-day simulation N
  // times instead of once — the actual cause of FUNCTION_INVOCATION_TIMEOUT
  // once N got large. Cache computes each unique pair exactly once.
  type CachedAccount = {
    trades: SimTrade[];
    incompleteTrades: number;
    dailyPnl: number[];
    econ: ReturnType<typeof walkAccountEconomics>;
  };
  const cache = new Map<string, CachedAccount>();

  function computeForWindow(w: { open: string; hardCutoff: string }, accountCfg: StrategyConfig, phaseFilter: "continuation" | "reversion" | null): CachedAccount {
    const key = `${w.open}|${w.hardCutoff}|${phaseFilter ?? "both"}`;
    const hit = cache.get(key);
    if (hit) return hit;

    const dailyPnl: number[] = [];
    const trades: SimTrade[] = [];
    let dayIncompleteTotal = 0;

    for (const day of days) {
      const dayBars = byDay.get(day)!.sort((a, b) => a.t.localeCompare(b.t));
      // Fresh per-day risk state — this account only ever plays one
      // session/day, so its own trade caps/loss caps apply within just
      // that window, same as a single-session strategy would.
      const state: DayRiskState = { tradesCount: 0, consecutiveLosses: 0, dayPnlDollars: 0 };
      const { trades: dayTrades, incompleteTrades: dayIncomplete } = simulateSession(
        dayBars,
        accountCfg,
        hhmmToMinutes(w.open),
        hhmmToMinutes(w.hardCutoff),
        state,
        w.open
      );
      trades.push(...dayTrades);
      dayIncompleteTotal += dayIncomplete;
      dailyPnl.push(dayTrades.reduce((s, t) => s + t.pnlDollars, 0));
    }

    const econ = walkAccountEconomics(accountCfg, dailyPnl, 0);
    const result: CachedAccount = { trades, incompleteTrades: dayIncompleteTotal, dailyPnl, econ };
    cache.set(key, result);
    return result;
  }

  for (let i = 0; i < accountCount; i++) {
    let w: { open: string; hardCutoff: string };
    let phaseFilter: "continuation" | "reversion" | null = null;
    let accountCfg = cfg;

    if (splitMainSession && i === 0) {
      w = mainWindow;
      phaseFilter = "continuation";
      accountCfg = { ...cfg, phases: { ...cfg.phases, tradeContinuation: true, tradeReversion: false } };
    } else if (splitMainSession && i === 1) {
      w = mainWindow;
      phaseFilter = "reversion";
      accountCfg = { ...cfg, phases: { ...cfg.phases, tradeContinuation: false, tradeReversion: true } };
    } else if (splitMainSession) {
      w = roundRobinPool[(i - 2) % roundRobinPool.length];
    } else {
      w = sessionWindows[i % M];
    }

    const { trades: accountTrades, incompleteTrades: accountIncomplete, econ } = computeForWindow(w, accountCfg, phaseFilter);
    incompleteTrades += accountIncomplete;
    const wins = accountTrades.filter((t) => t.win).length;
    const pooledNetPnl = accountTrades.reduce((s, t) => s + t.pnlDollars, 0);

    accounts.push({
      accountIndex: i,
      sessionOpen: w.open,
      phaseFilter,
      trades: accountTrades.length,
      wins,
      losses: accountTrades.length - wins,
      winRate: accountTrades.length ? round2((wins / accountTrades.length) * 100) : 0,
      pooledNetPnl: round2(pooledNetPnl),
      feesPaid: round2(econ.feesPaid),
      cashPayouts: round2(econ.cashPayouts),
      realWorldNetPnl: round2(econ.cashPayouts - econ.feesPaid),
      attemptsBought: econ.attemptsBought,
      timesFunded: econ.fundedCount,
    });

    totalFeesPaid += econ.feesPaid;
    totalCashPayouts += econ.cashPayouts;
    totalTrades += accountTrades.length;
  }

  return {
    accountCount,
    sessions: sessionWindows.map((w) => w.open),
    accounts,
    totalFeesPaid: round2(totalFeesPaid),
    totalCashPayouts: round2(totalCashPayouts),
    totalRealWorldNetPnl: round2(totalCashPayouts - totalFeesPaid),
    totalTrades,
    incompleteTrades,
  };
}
