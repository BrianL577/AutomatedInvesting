/**
 * The strategy configuration schema — the contract between the AI generator,
 * the backtest engine, and the database.
 *
 * Strategies are DATA, not code: a strategy is a set of parameters over a
 * fixed library of rule primitives (session anchor, displacement detection,
 * break-of-structure, phase windows, fixed R:R brackets, daily caps). Claude
 * translates a user's natural-language description into this config; nothing
 * AI-generated is ever executed as code. Every config — AI-generated or
 * hand-written — is validated against this zod schema (with hard numeric
 * bounds) before it is saved or backtested.
 */
import { z } from "zod";

export const StrategyConfigSchema = z
  .object({
    name: z.string().min(1).max(80),
    description: z.string().max(2000).default(""),
    session: z
      .object({
        // Wall-clock ET times, "HH:MM" 24h
        open: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
        hardCutoff: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
        // Extra session windows traded the same way (own open-candle anchor,
        // own phase windows relative to that open). Daily trade caps, loss
        // caps, and consecutive-loss stops span ALL sessions in the day.
        // e.g. London open 03:00-04:30 ET alongside the NY 09:30 session.
        additionalSessions: z
          .array(
            z
              .object({
                open: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
                hardCutoff: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
              })
              .strict()
          )
          .max(3)
          .optional(),
      })
      .strict(),
    // Day-level filters. News/red-folder day handling: there is no reliable
    // offline economic calendar, so verified news dates must be supplied
    // explicitly (from ForexFactory/CME calendar etc.) — days listed in
    // excludeDates are skipped entirely, exactly like JJ's "don't trade
    // red-folder days without the pre-news anchor" guidance simplified to
    // its safe form (skip the day).
    filters: z
      .object({
        excludeDates: z.array(z.string().regex(/^\d{4}-\d{2}-\d{2}$/)).max(500).optional(),
      })
      .strict()
      .optional(),
    // Portfolio-of-accounts simulation (how prop traders actually scale:
    // several accounts run in parallel, often one trade per account per
    // day, staggered so they don't all bust/pass in lockstep).
    portfolio: z
      .object({
        accountCount: z.number().int().min(1).max(20),
        // Account i starts its eval i*staggerDays trading days later.
        // 0 = all accounts identical (they bust/pass together — the same
        // daily P&L stream hits every account, so stagger is what actually
        // diversifies outcomes).
        staggerDays: z.number().int().min(0).max(30),
        // Restrict each account to only the first trade of each day
        // (overrides risk.maxTradesPerDay for the portfolio simulation).
        oneTradePerDay: z.boolean(),
      })
      .strict()
      .optional(),
    phases: z
      .object({
        continuationEndMin: z.number().min(0).max(120),
        reversionEndMin: z.number().min(0).max(360),
        tradeContinuation: z.boolean(),
        tradeReversion: z.boolean(),
      })
      .strict(),
    entry: z
      .object({
        displacementSizeRatio: z.number().min(0.5).max(5),
        displacementPrevRatio: z.number().min(0).max(5),
        maxWickRatio: z.number().min(0).max(1),
        structureLookbackMin: z.number().min(2).max(240),
        swingStrength: z.number().int().min(1).max(10),
        breakBufferPoints: z.number().min(0).max(50),
        minExtensionPoints: z.number().min(0).max(500),
      })
      .strict(),
    risk: z
      .object({
        stopPoints: z.number().min(1).max(500),
        targetPoints: z.number().min(1).max(1000),
        maxTradesPerDay: z.number().int().min(1).max(20),
        stopAfterConsecutiveLosses: z.number().int().min(1).max(10),
        contractsPerTrade: z.number().int().min(1).max(10),
        dailyProfitCap: z.number().min(0).max(100000),
        dailyLossCap: z.number().min(0).max(100000),
      })
      .strict(),
    eval: z
      .object({
        accountSize: z.number().min(1000).max(1000000),
        profitTarget: z.number().min(100).max(100000),
        trailingMaxDrawdown: z.number().min(100).max(100000),
        // Real-world prop-firm economics — optional/backward-compatible so
        // older saved strategies (and AI-generated configs that omit them)
        // still validate; runBacktest() applies defaults when absent. These
        // default to what the user described for TopStep-style firms, but
        // exact current fees/thresholds vary by firm and change over time —
        // verify against the actual firm's current rules before trusting
        // the "real-world" dollar figures for a real decision.
        evalFeeDollars: z.number().min(0).max(10000).optional(),
        reactivationFeeDollars: z.number().min(0).max(10000).optional(),
        fundedProfitThreshold: z.number().min(0).max(1000000).optional(),
        payoutShareRatio: z.number().min(0).max(1).optional(),
        maxPayoutPerEvent: z.number().min(0).max(1000000).optional(),
      })
      .strict(),
  })
  .strict();

export type StrategyConfig = z.infer<typeof StrategyConfigSchema>;

export type SavedStrategy = {
  id: string;
  config: StrategyConfig;
  source: "default" | "ai" | "manual";
  prompt?: string | null;
  created_at: string;
};

/** JJ's strategy, exactly as encoded in the Python bot's config.yaml. */
export const JJ_DEFAULT_STRATEGY: StrategyConfig = {
  name: "JJ NY-Session Strategy (Default)",
  description:
    "High-timeframe mean reversion, low-timeframe continuation anchored to the 9:30 ET open. " +
    "Continuation trades in the opening candle's direction for the first 10 minutes, then mean " +
    "reversion back toward the opening price until 90 minutes in. Entries require a displacement " +
    "candle (large true range, small wicks) that breaks and closes through recent swing structure. " +
    "Fixed 25pt stop / 38pt target (1:1.5 R:R), max 4 trades/day, stop after 2 consecutive losses, " +
    "$1,520 daily profit cap / $1,000 daily loss cap.",
  session: { open: "09:30", hardCutoff: "11:00" },
  phases: {
    continuationEndMin: 10,
    reversionEndMin: 90,
    tradeContinuation: true,
    tradeReversion: true,
  },
  entry: {
    displacementSizeRatio: 1.15,
    displacementPrevRatio: 1.0,
    maxWickRatio: 0.3,
    structureLookbackMin: 30,
    swingStrength: 2,
    breakBufferPoints: 1.0,
    minExtensionPoints: 12,
  },
  risk: {
    stopPoints: 25,
    targetPoints: 38,
    maxTradesPerDay: 4,
    stopAfterConsecutiveLosses: 2,
    contractsPerTrade: 1,
    dailyProfitCap: 1520,
    dailyLossCap: 1000,
  },
  eval: {
    accountSize: 50000,
    profitTarget: 3000,
    trailingMaxDrawdown: 2000,
  },
};

/** NQ contract constants used to convert points to dollars. */
export const INSTRUMENT = { symbol: "NQ", tickSize: 0.25, tickValue: 5.0 };
export const DOLLARS_PER_POINT = INSTRUMENT.tickValue / INSTRUMENT.tickSize; // $20/pt on NQ

/**
 * JSON Schema handed to Claude's structured-output format. Mirrors the zod
 * schema's shape; numeric bounds are enforced by zod after parsing (the
 * structured-outputs grammar doesn't support min/max).
 */
export const STRATEGY_JSON_SCHEMA = {
  type: "object",
  properties: {
    name: { type: "string", description: "Short human-readable strategy name" },
    description: { type: "string", description: "One-paragraph summary of the rules in plain English" },
    session: {
      type: "object",
      properties: {
        open: { type: "string", description: 'Session anchor time in ET, 24h "HH:MM", e.g. "09:30"' },
        hardCutoff: { type: "string", description: 'No new entries after this ET time, 24h "HH:MM"' },
        additionalSessions: {
          type: "array",
          maxItems: 3,
          description:
            "Optional extra session windows traded the same way with their own open-candle anchor (e.g. London open 03:00-04:30 ET). Daily trade/loss caps span all sessions. Omit unless the user asks for extra sessions.",
          items: {
            type: "object",
            properties: {
              open: { type: "string", description: 'ET "HH:MM"' },
              hardCutoff: { type: "string", description: 'ET "HH:MM"' },
            },
            required: ["open", "hardCutoff"],
            additionalProperties: false,
          },
        },
      },
      required: ["open", "hardCutoff"],
      additionalProperties: false,
    },
    filters: {
      type: "object",
      description:
        "Optional day-level filters. Only include if the user supplies specific dates — never invent news dates.",
      properties: {
        excludeDates: {
          type: "array",
          maxItems: 500,
          description: 'Dates to skip entirely (red-folder news days etc.), "YYYY-MM-DD". Only use dates the user explicitly provided.',
          items: { type: "string" },
        },
      },
      additionalProperties: false,
    },
    portfolio: {
      type: "object",
      description:
        "Optional portfolio-of-accounts simulation: N accounts run in parallel with staggered eval start dates, optionally one trade per account per day. Include only if the user describes running multiple accounts.",
      properties: {
        accountCount: { type: "integer", description: "Number of accounts run in parallel (1-20)" },
        staggerDays: { type: "integer", description: "Account i starts its eval i*staggerDays trading days later (0-30). 0 means all accounts move in lockstep." },
        oneTradePerDay: { type: "boolean", description: "Restrict each account to only the first trade of each day" },
      },
      required: ["accountCount", "staggerDays", "oneTradePerDay"],
      additionalProperties: false,
    },
    phases: {
      type: "object",
      properties: {
        continuationEndMin: { type: "number", description: "Minutes after open during which continuation trades are allowed (0 disables the window)" },
        reversionEndMin: { type: "number", description: "Minutes after open until which mean-reversion trades are allowed" },
        tradeContinuation: { type: "boolean", description: "Whether to take continuation trades in the opening candle's direction" },
        tradeReversion: { type: "boolean", description: "Whether to take mean-reversion trades back toward the session open price" },
      },
      required: ["continuationEndMin", "reversionEndMin", "tradeContinuation", "tradeReversion"],
      additionalProperties: false,
    },
    entry: {
      type: "object",
      properties: {
        displacementSizeRatio: { type: "number", description: "Candle true-range must be >= this multiple of the recent average true range (typical 1.0-1.5)" },
        displacementPrevRatio: { type: "number", description: "Candle true-range must be >= this multiple of the previous candle's true range (typical 1.0)" },
        maxWickRatio: { type: "number", description: "Max fraction of the candle that may be wick for it to count as displacement (0-1, typical 0.3)" },
        structureLookbackMin: { type: "number", description: "Minutes of history scanned for swing highs/lows (structure)" },
        swingStrength: { type: "integer", description: "Bars required on each side to confirm a swing pivot (typical 2)" },
        breakBufferPoints: { type: "number", description: "Close must clear the structure level by this many points to count as a break" },
        minExtensionPoints: { type: "number", description: "Minimum points price must extend away from the open before a reversion trade is valid" },
      },
      required: [
        "displacementSizeRatio",
        "displacementPrevRatio",
        "maxWickRatio",
        "structureLookbackMin",
        "swingStrength",
        "breakBufferPoints",
        "minExtensionPoints",
      ],
      additionalProperties: false,
    },
    risk: {
      type: "object",
      properties: {
        stopPoints: { type: "number", description: "Stop-loss distance in NQ points" },
        targetPoints: { type: "number", description: "Take-profit distance in NQ points" },
        maxTradesPerDay: { type: "integer", description: "Maximum trades per day" },
        stopAfterConsecutiveLosses: { type: "integer", description: "Stop trading for the day after this many consecutive losses" },
        contractsPerTrade: { type: "integer", description: "Contracts per trade" },
        dailyProfitCap: { type: "number", description: "Stop trading once day P&L reaches this many dollars (0 = no cap)" },
        dailyLossCap: { type: "number", description: "Stop trading once day P&L falls to minus this many dollars (0 = no cap)" },
      },
      required: [
        "stopPoints",
        "targetPoints",
        "maxTradesPerDay",
        "stopAfterConsecutiveLosses",
        "contractsPerTrade",
        "dailyProfitCap",
        "dailyLossCap",
      ],
      additionalProperties: false,
    },
    eval: {
      type: "object",
      properties: {
        accountSize: { type: "number", description: "Prop-firm eval account size in dollars (e.g. 50000)" },
        profitTarget: { type: "number", description: "Eval profit target in dollars (e.g. 3000)" },
        trailingMaxDrawdown: { type: "number", description: "Eval trailing max drawdown in dollars (e.g. 2000)" },
      },
      required: ["accountSize", "profitTarget", "trailingMaxDrawdown"],
      additionalProperties: false,
    },
  },
  required: ["name", "description", "session", "phases", "entry", "risk", "eval"],
  additionalProperties: false,
} as const;
