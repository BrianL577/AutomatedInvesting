/**
 * POST /api/optimize — AI-guided parameter search over the fixed rule
 * library, not free-form pattern-mining of raw price data.
 *
 * Why not "give Claude the 484k bars and ask it to find patterns": LLMs are
 * poor at large-scale numerical pattern-mining directly — that's what the
 * deterministic backtester already does, cheaply and exactly. Instead: each
 * round, Claude proposes a batch of parameter tweaks (phases/entry only —
 * risk is locked to the base config's exact values, never
 * session/eval/filters/portfolio either) given the previous round's
 * results; every proposal is validated, backtested against the real
 * historical dataset (free, instant, deterministic), and only the
 * aggregated result summary — not raw bars — goes back to Claude for the
 * next round. Hill-climbs toward better real-world economics, it does not
 * guarantee a profitable strategy exists within this rule library.
 *
 * Risk sizing (stop/target/contracts/trade-caps/$ caps) is the user's own
 * locked trading rule, not a search dimension: every variant inherits the
 * base config's risk block byte-for-byte. The optimizer can only change
 * WHICH trades get taken (entry selectivity, phase windows), never what a
 * trade costs or pays.
 */
import Anthropic from "@anthropic-ai/sdk";
import { NextRequest, NextResponse } from "next/server";
import { runBacktest, type BacktestResult } from "../../../lib/backtester";
import { loadBars } from "../../../lib/bars";
import {
  STRATEGY_VARIANT_BATCH_JSON_SCHEMA,
  StrategyConfigSchema,
  StrategyVariantSchema,
  survivabilityViolation,
  type StrategyConfig,
  type StrategyVariant,
} from "../../../lib/strategySchema";

export const dynamic = "force-dynamic";
export const maxDuration = 300; // several rounds of model calls + backtests

const MAX_ROUNDS = 5;
const MAX_BATCH_SIZE = 6;

const SYSTEM_PROMPT = `You are tuning which trades get taken by a fixed rule-based NQ futures intraday strategy — you do not invent new mechanics, only propose tweaks to session/phase timing and entry displacement/structure thresholds.

RISK IS LOCKED, NOT A SEARCH DIMENSION: the trader has a fixed, non-negotiable position-sizing rule (exact stop points, target points, contracts per trade, trade caps, and $ caps) that every variant inherits unchanged from the base config. You cannot propose risk changes — there is no risk field in your output schema. Your only levers are: which sessions/phases trade (phases.*) and how selective entries are (entry.*). This means win rate is your ONLY lever toward profitability — you cannot change R:R to compensate for a low win rate, so focus entirely on making the entry signal more (or deliberately less, to test both directions) selective.

TARGET: a configuration counts as "profitable" when realWorldNetPnl is positive — actual dollars after eval/reactivation fees and funded payouts. That's the primary objective.

Each round you'll see the current best config and a leaderboard of every variant tried so far with its real-world results:
- winRate: overall win rate across all trades — since R:R is fixed, this is the entire game
- evalStage: win rate & net P&L for trades while still working toward the eval profit target
- fundedStage: win rate & net P&L for trades after reaching funded — this is what actually matters long-term
- realWorldNetPnl: actual dollars (fees vs. funded payouts) — the honest bottom line and the fitness metric
- evalPassRate: probability of ever reaching funded at all

Search heuristics: tighten displacement/structure thresholds for fewer, higher-conviction entries when win rate is below breakeven; loosen them (or widen phase windows, add trade frequency) when a stricter filter already proved a win-rate lift but starved trade volume too much. Eval and funded stages can reward different selectivity — watch evalStage vs fundedStage results separately and say which stage a variant is aimed at.

Propose meaningfully different variants each round, not tiny noise — if recent variants haven't improved realWorldNetPnl, try a different direction (tighter vs. looser displacement/structure filters, different phase windows). Each variant is a partial diff on top of the CURRENT BEST config, touching only phases and/or entry — only include fields you're deliberately changing. Give one honest sentence per variant explaining the hypothesis, referencing what the leaderboard actually shows.`;

type Candidate = {
  round: number;
  rationale: string;
  diff: Omit<StrategyVariant, "rationale">;
  config: StrategyConfig;
  result: BacktestResult;
  fitness: number;
};

function mergeVariant(base: StrategyConfig, diff: Omit<StrategyVariant, "rationale">): StrategyConfig {
  return {
    ...base,
    phases: { ...base.phases, ...diff.phases },
    entry: { ...base.entry, ...diff.entry },
    // risk is intentionally never merged from the diff — it isn't a search
    // dimension (see StrategyVariantSchema), every variant keeps the base
    // config's risk block exactly.
  };
}

function fitnessOf(result: BacktestResult): number {
  // "Profitable" simply means positive real-world net P&L (fees vs. funded
  // payouts) — so that IS the fitness metric, with eval pass rate breaking
  // ties between candidates with similar economics.
  return result.realWorldNetPnl * 1000 + result.evalPassRate;
}

function summarizeForPrompt(candidates: Candidate[]): string {
  return candidates
    .map((c, i) => {
      const r = c.result;
      return (
        `#${i + 1} (round ${c.round}) — ${c.rationale}\n` +
        `  diff: ${JSON.stringify(c.diff)}\n` +
        `  overall winRate: ${r.winRate.toFixed(1)}%${r.realWorldNetPnl > 0 ? " (PROFITABLE — positive real-world net)" : ""}\n` +
        `  evalStage: ${r.evalStage.winRate.toFixed(1)}% win, $${r.evalStage.netPnl.toFixed(0)} net (${r.evalStage.trades} trades)\n` +
        `  fundedStage: ${r.fundedStage.winRate.toFixed(1)}% win, $${r.fundedStage.netPnl.toFixed(0)} net (${r.fundedStage.trades} trades)\n` +
        `  realWorldNetPnl: $${r.realWorldNetPnl.toFixed(0)}, evalPassRate: ${r.evalPassRate.toFixed(1)}%`
      );
    })
    .join("\n");
}

export async function POST(req: NextRequest) {
  if (!process.env.ANTHROPIC_API_KEY) {
    return NextResponse.json(
      { error: "Optimization requires ANTHROPIC_API_KEY to be set on the server (Vercel env vars)." },
      { status: 503 }
    );
  }

  let body: { baseConfig?: unknown; rounds?: unknown; batchSize?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const baseParsed = StrategyConfigSchema.safeParse(body.baseConfig);
  if (!baseParsed.success) {
    return NextResponse.json(
      { error: "baseConfig failed validation", issues: baseParsed.error.issues.slice(0, 10) },
      { status: 400 }
    );
  }
  const rounds = Math.min(MAX_ROUNDS, Math.max(1, Number(body.rounds) || 3));
  const batchSize = Math.min(MAX_BATCH_SIZE, Math.max(1, Number(body.batchSize) || 4));

  const { bars, source } = await loadBars();
  if (!bars.length) {
    return NextResponse.json({ error: "No historical bars available to optimize against." }, { status: 503 });
  }

  const client = new Anthropic();

  const baselineResult = runBacktest(baseParsed.data, bars);
  let best: Candidate = {
    round: 0,
    rationale: "Baseline (starting config, unmodified)",
    diff: {},
    config: baseParsed.data,
    result: baselineResult,
    fitness: fitnessOf(baselineResult),
  };
  const history: Candidate[] = [best];

  try {
    for (let round = 1; round <= rounds; round++) {
      const leaderboard = [...history].sort((a, b) => b.fitness - a.fitness).slice(0, 8);

      const response = await client.messages.create({
        model: "claude-opus-4-8",
        max_tokens: 4096,
        thinking: { type: "adaptive" },
        // Static across every round of this run (and across separate runs)
        // — mark cacheable so repeat calls within the cache TTL bill ~10%
        // of input cost for this block instead of full price each round.
        system: [{ type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
        // effort (not thinking.budget_tokens, which claude-opus-4-8
        // rejects) bounds adaptive thinking time — "low" keeps each round
        // fast, since up to MAX_ROUNDS=5 calls share one 300s function
        // budget and one slow round can starve the rest.
        output_config: {
          format: { type: "json_schema", schema: STRATEGY_VARIANT_BATCH_JSON_SCHEMA },
          effort: "low",
        },
        messages: [
          {
            role: "user",
            content:
              `Current best config (fitness leader):\n${JSON.stringify({ phases: best.config.phases, entry: best.config.entry, risk: best.config.risk })}\n\n` +
              `Leaderboard so far (best first):\n${summarizeForPrompt(leaderboard)}\n\n` +
              `Propose ${batchSize} new variants (diffs on top of the current best config) for round ${round} of ${rounds}.`,
          },
        ],
      });

      if (response.stop_reason === "refusal") continue;
      const text = response.content.find((b) => b.type === "text");
      if (!text || text.type !== "text") continue;

      let variants: unknown[];
      try {
        variants = JSON.parse(text.text).variants ?? [];
      } catch {
        continue;
      }

      for (const raw of variants.slice(0, batchSize)) {
        const parsed = StrategyVariantSchema.safeParse(raw);
        if (!parsed.success) continue;
        const { rationale, ...diff } = parsed.data;
        const candidateConfig = mergeVariant(best.config, diff);
        const configCheck = StrategyConfigSchema.safeParse(candidateConfig);
        if (!configCheck.success) continue;
        if (!configCheck.data.phases.tradeContinuation && !configCheck.data.phases.tradeReversion) continue;
        // Hard survivability floor (shared with generate-strategy and
        // strategy-chat): a single losing trade must never exceed the
        // eval's own trailing drawdown limit, or the very first loss
        // guarantee-busts the account regardless of the strategy's
        // long-run edge. Reject outright — no exceptions, even if the
        // model proposes one — rather than let a backtest-profitable but
        // unsurvivable config ever reach the leaderboard.
        if (survivabilityViolation(configCheck.data)) continue;

        const result = runBacktest(configCheck.data, bars);
        const candidate: Candidate = {
          round,
          rationale,
          diff,
          config: configCheck.data,
          result,
          fitness: fitnessOf(result),
        };
        history.push(candidate);
        if (candidate.fitness > best.fitness) best = candidate;
      }
    }
  } catch (err: unknown) {
    // Return whatever was found before the failure rather than losing all progress.
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({
      bestConfig: best.config,
      bestResult: best.result,
      history: history.map((c) => ({ round: c.round, rationale: c.rationale, diff: c.diff, fitness: c.fitness, result: c.result, config: c.config })),
      dataSource: source,
      warning: `Stopped early after an error: ${message}`,
    });
  }

  return NextResponse.json({
    bestConfig: best.config,
    bestResult: best.result,
    history: history.map((c) => ({ round: c.round, rationale: c.rationale, diff: c.diff, fitness: c.fitness, result: c.result, config: c.config })),
    dataSource: source,
  });
}
