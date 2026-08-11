// Pure constants only — no `fs` or other server-only imports, so this is
// safe to import from a client component (unlike lib/trades.ts, which pulls
// in Node's `fs` for its file-based trade-loading fallback and breaks the
// client bundle if imported from "use client" code).
import { JJ_DEFAULT_STRATEGY } from "./strategySchema";

const EVAL_CFG = JJ_DEFAULT_STRATEGY.eval;

export const EVAL_SIM = {
  // The eval's starting balance — real live balance minus this is exact,
  // real "profit so far", straight from TopStep's own Account/search API
  // (no simulation/guessing involved, unlike floor/effectiveProfitTarget,
  // which TopStep has no API for at all).
  ACCOUNT_SIZE: EVAL_CFG.accountSize,
  EVAL_TARGET: EVAL_CFG.profitTarget,
  TRAILING_DRAWDOWN: EVAL_CFG.trailingMaxDrawdown,
  // Flat per-attempt cost: Topstep's real $85/mo subscription (PROMO PRICE,
  // may change — update here if it does) + $85 reset fee are two separate
  // charges (see backtester.ts's walkAccountEconomics for the full
  // monthly-billing simulation) — this is a deliberate simplification for
  // the at-a-glance homepage view, using the reset fee as the flat number
  // since it's charged on every bust regardless of billing cycle timing.
  EVAL_COST: EVAL_CFG.evalFeeDollars ?? 85,
  PAYOUT_SHARE: EVAL_CFG.payoutShareRatio ?? 0.9,
  // PROMO VALUE — Topstep's page currently shows a promotional payout cap
  // ($4,000). This can change — check Topstep's current pricing page and
  // update JJ_DEFAULT_STRATEGY.eval.maxPayoutPerEvent (strategySchema.ts)
  // if it does; everything else here derives from that single source.
  MAX_PAYOUT: EVAL_CFG.maxPayoutPerEvent ?? 4000,
  MAX_PAYOUT_BALANCE_SHARE: 0.5,
  MIN_WINNING_DAYS_FOR_PAYOUT: EVAL_CFG.minWinningDaysForPayout ?? 5,
  MIN_WINNING_DAY_PROFIT: EVAL_CFG.minWinningDayProfit ?? 150,
};
