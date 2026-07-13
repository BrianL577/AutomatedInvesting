/**
 * POST /api/generate-strategy — turns a natural-language strategy description
 * into a validated StrategyConfig using Claude with structured outputs.
 *
 * Security model: the model produces DATA (parameters over a fixed rule
 * library), never code. The response is constrained server-side by a JSON
 * schema grammar, then re-validated with zod (which enforces the numeric
 * bounds the grammar can't). ANTHROPIC_API_KEY is server-only.
 */
import Anthropic from "@anthropic-ai/sdk";
import { NextRequest, NextResponse } from "next/server";
import {
  JJ_DEFAULT_STRATEGY,
  STRATEGY_JSON_SCHEMA,
  StrategyConfigSchema,
  survivabilityViolation,
} from "../../../lib/strategySchema";

export const dynamic = "force-dynamic";
export const maxDuration = 60; // Vercel function timeout headroom for the model call

const SYSTEM_PROMPT = `You translate a trader's natural-language description of an intraday NASDAQ-100 futures (NQ) strategy into a strict JSON configuration for a rule-based backtesting engine.

The engine's fixed rule library (you can only tune its parameters, not invent new mechanics):
- A session anchor: the 1-minute candle at session.open (ET) defines the "fair price" and the opening direction.
- Continuation phase: from the open until phases.continuationEndMin minutes after it, entries in the opening candle's direction are allowed (if tradeContinuation).
- Reversion phase: after the continuation window until phases.reversionEndMin, entries back toward the open price are allowed (if tradeReversion), but only once price has extended at least entry.minExtensionPoints away from the open.
- Every entry additionally requires a "displacement" candle — true range at least entry.displacementSizeRatio × the ~10-bar average true range AND at least entry.displacementPrevRatio × the previous bar's true range, with wick fraction ≤ entry.maxWickRatio — that closes beyond the nearest swing high/low (pivots with entry.swingStrength bars each side within entry.structureLookbackMin minutes) by more than entry.breakBufferPoints.
- Exits are a fixed bracket: risk.stopPoints stop, risk.targetPoints target.
- Daily discipline: at most risk.maxTradesPerDay trades, stop after risk.stopAfterConsecutiveLosses consecutive losses, stop for the day once P&L reaches +risk.dailyProfitCap or -risk.dailyLossCap dollars (NQ = $20/point/contract), and no entries after session.hardCutoff ET.
- Prop-firm eval simulation uses eval.accountSize / eval.profitTarget / eval.trailingMaxDrawdown.

Guidelines:
- Map the user's intent onto these parameters as faithfully as possible. If they describe a mechanic the engine cannot express (e.g. VWAP, order flow, news filters), approximate it with the closest available parameters and say so in the description field.
- Use sensible defaults for anything unspecified (reference: stop 25 / target 38 on 2 contracts [$1,000/$1,520 on NQ], 1 trade/day, 09:30 open, 11:00 cutoff, $1520/$1000 daily caps, 50k eval with +3000 target and 2000 trailing drawdown).
- Keep values realistic for NQ 1-minute data: stops 5-100 points, targets 5-200 points, ratios near 1.
- HARD CONSTRAINT: risk.stopPoints x risk.contractsPerTrade x $20/point must not exceed eval.trailingMaxDrawdown — a single losing trade must never be able to bust the account outright. Configs violating this are rejected outright, so check this arithmetic before finalizing stopPoints/contractsPerTrade.
- The description field must summarize, in plain English, the exact rules this config encodes — including any approximations you made.
- Times are 24h ET "HH:MM". Never disable both tradeContinuation and tradeReversion.`;

export async function POST(req: NextRequest) {
  if (!process.env.ANTHROPIC_API_KEY) {
    return NextResponse.json(
      { error: "AI generation requires ANTHROPIC_API_KEY to be set on the server (Vercel env vars)." },
      { status: 503 }
    );
  }

  let body: { prompt?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
  if (prompt.length < 10) {
    return NextResponse.json({ error: "Describe your strategy in at least a sentence." }, { status: 400 });
  }
  if (prompt.length > 4000) {
    return NextResponse.json({ error: "Strategy description too long (max 4000 chars)." }, { status: 400 });
  }

  const client = new Anthropic();

  try {
    const response = await client.messages.create({
      model: "claude-opus-4-8",
      max_tokens: 4096,
      // Bounded, not "adaptive" — adaptive can think for long enough to
      // exceed Vercel's 60s function timeout on some requests. A fixed
      // budget keeps response time predictable.
      thinking: { type: "enabled", budget_tokens: 3000 },
      // Static across every call — mark cacheable so repeat requests within
      // the cache TTL bill ~10% of input cost for this block instead of
      // full price. Same exact prompt, no change in what the model sees.
      system: [{ type: "text", text: SYSTEM_PROMPT, cache_control: { type: "ephemeral" } }],
      output_config: {
        format: { type: "json_schema", schema: STRATEGY_JSON_SCHEMA },
      },
      messages: [
        {
          role: "user",
          content:
            `Reference config (JJ's default strategy):\n${JSON.stringify(JJ_DEFAULT_STRATEGY)}\n\n` +
            `Trader's strategy description:\n${prompt}`,
        },
      ],
    });

    if (response.stop_reason === "refusal") {
      return NextResponse.json(
        { error: "The model declined to generate a config for this prompt. Try rephrasing." },
        { status: 422 }
      );
    }

    const text = response.content.find((b) => b.type === "text");
    if (!text || text.type !== "text") {
      return NextResponse.json({ error: "Model returned no config." }, { status: 502 });
    }

    const parsed = StrategyConfigSchema.safeParse(JSON.parse(text.text));
    if (!parsed.success) {
      return NextResponse.json(
        {
          error: "Generated config failed validation — try a more concrete description.",
          issues: parsed.error.issues.slice(0, 10),
        },
        { status: 422 }
      );
    }
    if (!parsed.data.phases.tradeContinuation && !parsed.data.phases.tradeReversion) {
      return NextResponse.json(
        { error: "Generated config disables all entry phases — try a more concrete description." },
        { status: 422 }
      );
    }
    const violation = survivabilityViolation(parsed.data);
    if (violation) {
      return NextResponse.json(
        { error: `Generated config rejected: ${violation} Try a more conservative description.` },
        { status: 422 }
      );
    }

    return NextResponse.json({ config: parsed.data });
  } catch (err: unknown) {
    if (err instanceof Anthropic.RateLimitError) {
      return NextResponse.json({ error: "AI is rate-limited right now — try again shortly." }, { status: 429 });
    }
    if (err instanceof Anthropic.APIError) {
      return NextResponse.json({ error: `AI request failed: ${err.message}` }, { status: 502 });
    }
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: `Generation failed: ${message}` }, { status: 500 });
  }
}
