/**
 * POST /api/optimize — AI-guided parameter search over the fixed rule
 * library, not free-form pattern-mining of raw price data.
 *
 * Why not "give Claude the 484k bars and ask it to find patterns": LLMs are
 * poor at large-scale numerical pattern-mining directly — that's what the
 * deterministic backtester already does, cheaply and exactly. Instead: each
 * round, Claude proposes a batch of parameter tweaks (phases/entry/risk
 * only — never session/eval/filters/portfolio) given the previous round's
 * results; every proposal is validated, backtested against the real
 * historical dataset (free, instant, deterministic), and only the
 * aggregated result summary — not raw bars — goes back to Claude for the
 * next round. Hill-climbs toward better real-world economics, it does not
 * guarantee a profitable strategy exists within this rule library.
 */
import Anthropic from "@anthropic-ai/sdk";
import { NextRequest, NextResponse } from "next/server";
import { runBacktest, type BacktestResult } from "../../../lib/backtester";
import { loadBars } from "../../../lib/bars";
import {
  STRATEGY_VARIANT_BATCH_JSON_SCHEMA,
  StrategyConfigSchema,
  StrategyVariantSchema,
  type StrategyConfig,
  type StrategyVariant,
} from "../../../lib/strategySchema";

export const dynamic = "force-dynamic";
export const maxDuration = 300; // several rounds of model calls + backtests

const MAX_ROUNDS = 5;
const MAX_BATCH_SIZE = 6;

const SYSTEM_PROMPT = `You are tuning the numeric parameters of a fixed rule-based NQ futures intraday strategy — you do not invent new mechanics, only propose tweaks to existing ones (session/phase timing already fixed, entry displacement/structure thresholds, and risk stop/target/caps).

Each round you'll see the current best config and a leaderboard of every variant tried so far with its real-world results:
- evalStage: win rate & net P&L for trades while still working toward the eval profit target
- fundedStage: win rate & net P&L for trades after reaching funded — this is what actually matters long-term
- realWorldNetPnl: actual dollars (eval/reactivation fees vs. funded payouts) — the honest bottom line
- evalPassRate: probability of ever reaching funded at all

Propose meaningfully different variants each round, not tiny noise — if recent variants haven't improved realWorldNetPnl, try a different direction (e.g. tighter displacement filter for higher-quality signals vs. looser for more trades; different stop:target ratio; fewer max trades/day to reduce overtrading). Each variant is a partial diff on top of the CURRENT BEST config — only include fields you're deliberately changing. Give one honest sentence per variant explaining the hypothesis, referencing what the leaderboard actually shows.`;

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
    risk: { ...base.risk, ...diff.risk },
  };
}

function fitnessOf(result: BacktestResult): number {
  // Real-world net P&L is the honest bottom line (fees vs. funded payouts);
  // eval pass rate breaks ties between two candidates with similar economics
  // by preferring the one more likely to actually reach funded at all.
  return result.realWorldNetPnl * 1000 + result.evalPassRate;
}

function summarizeForPrompt(candidates: Candidate[]): string {
  return candidates
    .map((c, i) => {
      const r = c.result;
      return (
        `#${i + 1} (round ${c.round}) — ${c.rationale}\n` +
        `  diff: ${JSON.stringify(c.diff)}\n` +
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
        system: SYSTEM_PROMPT,
        output_config: {
          format: { type: "json_schema", schema: STRATEGY_VARIANT_BATCH_JSON_SCHEMA },
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
