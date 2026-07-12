/**
 * POST /api/strategy-chat — a multi-turn conversation with Claude to design a
 * strategy together, instead of a single-shot prompt-to-config generation.
 *
 * Each turn, Claude sees: the rule engine it can configure, the current draft
 * (if any), and weekday pattern stats (win rate / net P&L per Mon-Fri) from
 * backtesting either the current draft or JJ's default strategy against the
 * loaded historical bars — so it can reason about real patterns instead of
 * guessing. Claude replies with plain text, and may call the
 * `propose_strategy` tool when it has a concrete config to suggest. A
 * strategy only needs to be net profitable, not hit any fixed win-rate bar.
 */
import Anthropic from "@anthropic-ai/sdk";
import { NextRequest, NextResponse } from "next/server";
import { runBacktest } from "../../../lib/backtester";
import { loadBars } from "../../../lib/bars";
import { analyzeWeekdayPatterns } from "../../../lib/dayPatterns";
import {
  JJ_DEFAULT_STRATEGY,
  STRATEGY_JSON_SCHEMA,
  StrategyConfigSchema,
  type StrategyConfig,
} from "../../../lib/strategySchema";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const SYSTEM_PROMPT = `You are designing an intraday NASDAQ-100 futures (NQ) strategy together with a trader, as a conversation — ask questions, explain trade-offs, and only propose a config once you have a concrete idea worth testing.

The engine's fixed rule library (you can only tune its parameters, not invent new mechanics):
- A session anchor: the 1-minute candle at session.open (ET) defines the "fair price" and the opening direction.
- Continuation phase: from the open until phases.continuationEndMin minutes after it, entries in the opening candle's direction are allowed (if tradeContinuation).
- Reversion phase: after the continuation window until phases.reversionEndMin, entries back toward the open price are allowed (if tradeReversion), but only once price has extended at least entry.minExtensionPoints away from the open.
- Every entry additionally requires a "displacement" candle — true range at least entry.displacementSizeRatio x the ~10-bar average true range AND at least entry.displacementPrevRatio x the previous bar's true range, with wick fraction <= entry.maxWickRatio — that closes beyond the nearest swing high/low (pivots with entry.swingStrength bars each side within entry.structureLookbackMin minutes) by more than entry.breakBufferPoints.
- Exits are a fixed bracket: risk.stopPoints stop, risk.targetPoints target.
- Daily discipline: at most risk.maxTradesPerDay trades, stop after risk.stopAfterConsecutiveLosses consecutive losses, stop for the day once P&L reaches +risk.dailyProfitCap or -risk.dailyLossCap dollars (NQ = $20/point/contract), and no entries after session.hardCutoff ET.
- Prop-firm eval simulation uses eval.accountSize / eval.profitTarget / eval.trailingMaxDrawdown.

You will be given weekday pattern stats (trade count, win rate, net P&L per Mon-Fri) from backtesting the current reference config against real historical bars. Use these to talk concretely about patterns (e.g. "Fridays are your best day, Mondays are dragging you down") instead of vague generalities.

Important: there is no fixed win-rate bar to clear (not 60%, not 50%) — the only bar is being net profitable overall. A strategy with a 35% win rate and a good risk:reward can be far more profitable than one with a 65% win rate and poor risk:reward. Judge configs by net P&L and return %, not win rate alone.

Guidelines when you do propose a config via the propose_strategy tool:
- Use sensible defaults for anything you're not deliberately changing (reference: stop 25 / target 38, max 4 trades/day, 09:30 open, 11:00 cutoff, $1520/$1000 daily caps, 50k eval with +3000 target and 2000 trailing drawdown).
- Keep values realistic for NQ 1-minute data: stops 5-100 points, targets 5-200 points, ratios near 1.
- The description field must summarize, in plain English, the exact rules this config encodes, including any approximations for things the user asked for that the engine can't literally express (e.g. VWAP, order flow, news filters).
- Times are 24h ET "HH:MM". Never disable both tradeContinuation and tradeReversion.
- Keep your text reply short (2-4 sentences) — you're in a chat, not writing a report.`;

const PROPOSE_TOOL: Anthropic.Tool = {
  name: "propose_strategy",
  description: "Propose a concrete strategy config for the trader to review, backtest, and save.",
  input_schema: STRATEGY_JSON_SCHEMA as unknown as Anthropic.Tool.InputSchema,
};

type ChatMessage = { role: "user" | "assistant"; content: string };

export async function POST(req: NextRequest) {
  if (!process.env.ANTHROPIC_API_KEY) {
    return NextResponse.json(
      { error: "AI chat requires ANTHROPIC_API_KEY to be set on the server (Vercel env vars)." },
      { status: 503 }
    );
  }

  let body: { messages?: unknown; config?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const messages = Array.isArray(body.messages) ? (body.messages as ChatMessage[]) : [];
  const validMessages = messages.filter(
    (m) => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string"
  );
  if (validMessages.length === 0) {
    return NextResponse.json({ error: "At least one message is required." }, { status: 400 });
  }
  if (validMessages.length > 40) {
    return NextResponse.json({ error: "Conversation too long — start a new one." }, { status: 400 });
  }

  const referenceParsed = StrategyConfigSchema.safeParse(body.config);
  const referenceConfig: StrategyConfig = referenceParsed.success ? referenceParsed.data : JJ_DEFAULT_STRATEGY;

  let patternContext = "No historical bars available yet, so no pattern stats — reason qualitatively.";
  try {
    const { bars, source } = await loadBars();
    if (bars.length) {
      const result = runBacktest(referenceConfig, bars);
      const weekdayStats = analyzeWeekdayPatterns(result.trades);
      patternContext =
        `Reference config backtested: "${referenceConfig.name}" against ${source === "supabase" ? "real historical" : "synthetic sample"} data. ` +
        `Overall: ${result.totalTrades} trades, ${result.winRate.toFixed(1)}% win rate, net P&L ${result.netPnl >= 0 ? "+" : ""}$${result.netPnl.toFixed(0)}.\n` +
        `Per weekday: ${weekdayStats
          .map((w) => `${w.weekday}: ${w.trades} trades, ${w.winRate.toFixed(0)}% win, net $${w.netPnl.toFixed(0)} (avg $${w.avgPnl.toFixed(0)}/trade)`)
          .join("; ")}.`;
    }
  } catch {
    // Pattern context is best-effort; proceed without it on failure.
  }

  const client = new Anthropic();

  try {
    const response = await client.messages.create({
      model: "claude-opus-4-8",
      max_tokens: 2048,
      system: `${SYSTEM_PROMPT}\n\n${patternContext}`,
      tools: [PROPOSE_TOOL],
      messages: validMessages.map((m) => ({ role: m.role, content: m.content })),
    });

    if (response.stop_reason === "refusal") {
      return NextResponse.json(
        { error: "The model declined to respond to this conversation. Try rephrasing." },
        { status: 422 }
      );
    }

    const textBlock = response.content.find((b) => b.type === "text");
    const reply = textBlock && textBlock.type === "text" ? textBlock.text : "";

    const toolUse = response.content.find((b) => b.type === "tool_use" && b.name === "propose_strategy");
    let config: StrategyConfig | undefined;
    let configError: string | undefined;
    if (toolUse && toolUse.type === "tool_use") {
      const parsed = StrategyConfigSchema.safeParse(toolUse.input);
      if (parsed.success && (parsed.data.phases.tradeContinuation || parsed.data.phases.tradeReversion)) {
        config = parsed.data;
      } else {
        configError = "The proposed config failed validation, so it wasn't attached — ask the AI to try again.";
      }
    }

    return NextResponse.json({ reply, config, configError });
  } catch (err: unknown) {
    if (err instanceof Anthropic.RateLimitError) {
      return NextResponse.json({ error: "AI is rate-limited right now — try again shortly." }, { status: 429 });
    }
    if (err instanceof Anthropic.APIError) {
      return NextResponse.json({ error: `AI request failed: ${err.message}` }, { status: 502 });
    }
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: `Chat failed: ${message}` }, { status: 500 });
  }
}
