/**
 * POST /api/strategy-chat — kicks off a multi-turn strategy design
 * conversation turn and returns immediately with a jobId; the actual
 * Claude call happens on the always-on bot API host (see
 * jj_bot/api_server.py's /api/ai-job), not inline in this function.
 *
 * Why: Vercel Hobby hard-caps serverless functions at 10s regardless of any
 * maxDuration set here, but Claude calls with extended thinking on a
 * conversational strategy question routinely take longer than that —
 * confirmed live via FUNCTION_INVOCATION_TIMEOUT. Poll
 * GET /api/strategy-chat/status?jobId=... (see status/route.ts) for the
 * result instead of waiting on it inline.
 *
 * Unlike /api/generate-strategy (one-shot description -> config), this is a
 * back-and-forth: Claude sees a compact weekly digest of the real
 * historical data (first + last trading day of each week, ~100 entries —
 * see lib/weeklySummary.ts) and can discuss what patterns it suggests,
 * push back on ideas, and iterate with the user. When the conversation
 * converges on something concrete, the reply includes a full validated
 * config the UI offers to load as a draft.
 *
 * Same security model as generation: the model produces DATA (parameters
 * over the fixed rule library), never code, and every config is
 * re-validated with zod before it reaches the client (in status/route.ts,
 * once the job result comes back).
 */
import { NextRequest, NextResponse } from "next/server";
import { loadBars } from "../../../lib/bars";
import { buildWeeklyEdgeSummary } from "../../../lib/weeklySummary";
import { JJ_DEFAULT_STRATEGY, STRATEGY_JSON_SCHEMA } from "../../../lib/strategySchema";

export const dynamic = "force-dynamic";
export const maxDuration = 10;

const MAX_MESSAGES = 24;
const MAX_MESSAGE_CHARS = 4000;

const CHAT_JSON_SCHEMA = {
  type: "object",
  properties: {
    reply: {
      type: "string",
      description: "Your conversational reply to the trader — plain English, no JSON in it.",
    },
    config: {
      anyOf: [
        STRATEGY_JSON_SCHEMA,
        { type: "null" },
      ],
      description:
        "A complete strategy config ONLY once the conversation has converged on something concrete the trader wants to test. null while still discussing.",
    },
  },
  required: ["reply", "config"],
  additionalProperties: false,
} as const;

const SYSTEM_PROMPT = `You are a strategy design partner for intraday NASDAQ-100 futures (NQ), collaborating with a trader over multiple messages.

You have a weekly digest of their real historical 1-minute data: the first and last trading day of each week, each with the full-day move, range, and the 09:30-11:00 ET session move. Use it to ground the discussion — point at actual dates and numbers when you claim a pattern, and be statistically honest: ~100 data points is small, day-of-week effects are usually weak, and a pattern that holds 55% of the time is noise until proven otherwise. Never invent data that isn't in the digest.

The strategy engine is a fixed rule library — you tune parameters, you do not invent new mechanics:
- Session anchor: the 1-minute candle at session.open (ET) defines "fair price" and opening direction. additionalSessions (max 3) can add more windows.
- Continuation phase (first phases.continuationEndMin minutes): entries in the opening candle's direction.
- Reversion phase (until phases.reversionEndMin): entries back toward the open once price extended >= entry.minExtensionPoints away.
- Break of structure is the ONLY mandatory entry trigger: close beyond the nearest swing high/low (entry.swingStrength-bar pivots within entry.structureLookbackMin minutes, falling back to the session's extreme so far if no pivot has confirmed) by more than entry.breakBufferPoints. Two secondary confluences only upgrade the setup grade, never gate entry: a displacement candle (true range >= entry.displacementSizeRatio x ~10-bar avg TR and >= entry.displacementPrevRatio x previous bar, wick fraction <= entry.maxWickRatio) and HTF bias (direction of a synthetic candle built from the last entry.htfBarMinutes 1-min bars). Grade: A+ = both confluences align, A = one aligns, B+ = BOS alone — all grades are tradeable.
- Fixed bracket exits: risk.stopPoints / risk.targetPoints. Daily discipline: risk.maxTradesPerDay, risk.stopAfterConsecutiveLosses, +risk.dailyProfitCap / -risk.dailyLossCap dollar caps (NQ = $20/point/contract), no entries after session.hardCutoff.
- Prop-firm eval sim: eval.accountSize / profitTarget / trailingMaxDrawdown. "Profitable" for this trader means the real-world simulation nets positive dollars after eval fees and funded payouts — not any specific win-rate number.
- If the trader wants something the engine can't express (VWAP, order flow, live news filters), say so plainly and offer the closest approximation.

Prop-firm principles (from JJ's own interview — treat these as the house philosophy when advising):
- Optimize the EVAL and FUNDED stages for different things. Eval: maximize the chance of hitting the profit target before the trailing max drawdown. Funded: maximize expected payout value (chance of payout x payout size). A config that's great for one can be mediocre for the other; when a trade-off exists, say which stage it favors.
- Match risk:reward to the account's own ratio. A $3,000 target / $2,000 trailing drawdown eval is a 1:1.5 game — so a ~1:1.5 R:R (e.g. $1,000 risk / $1,500 target) passes more evals than either scalpy 1:1 or lottery-style 1:5+, because of how the trailing drawdown moves. High R:R (1:5+) needs improbable streaks under the consistency rule; suggest R:R near the eval's own ratio unless the trader has a reason not to.
- HARD CONSTRAINT, non-negotiable: risk.stopPoints x risk.contractsPerTrade x $20/point must never exceed eval.trailingMaxDrawdown — a single losing trade must never be able to bust the account outright, regardless of win rate. Configs violating this are withheld from the trader entirely. Check this arithmetic before attaching any config.
- Static everything: fixed dollar stop, fixed dollar target, no runners, no partials, no breakeven moves. The prop-firm math rewards static risk; runners are punished by consistency rules and end-of-day trailing drawdown.
- More sessions = more attempts. Every session open (8:30 news, 9:30 NY, 2pm NY PM, 8pm Asian) sets a fresh "fair price": one continuation off the opening move, then reversions back toward the open. Use additionalSessions to add windows when the trader wants more trade frequency.
- The economics is a slot machine you can price: expected value = pass rate x payout rate x payout size vs. eval fees. It only needs a slight edge per trade repeated many times — not a high win rate, not home runs.

Conversation behavior:
- Ask clarifying questions when the trader's idea is vague; propose concrete parameter choices when it's specific.
- Set config to null while still discussing. Only include a full config once you and the trader have converged on something concrete to test — and say in your reply that you've attached it. Its description field must summarize the exact rules in plain English, including approximations.
- Remind the trader that any idea should be validated with Run Backtest — patterns in the digest are hypotheses, not proof.`;

type ChatMessage = { role: "user" | "assistant"; content: string };

function sanitizeMessages(raw: unknown): ChatMessage[] | null {
  if (!Array.isArray(raw) || raw.length === 0 || raw.length > MAX_MESSAGES) return null;
  const out: ChatMessage[] = [];
  for (const m of raw) {
    if (typeof m !== "object" || m === null) return null;
    const role = (m as Record<string, unknown>).role;
    const content = (m as Record<string, unknown>).content;
    if (role !== "user" && role !== "assistant") return null;
    if (typeof content !== "string" || !content.trim() || content.length > MAX_MESSAGE_CHARS) return null;
    out.push({ role, content: content.trim() });
  }
  if (out[out.length - 1].role !== "user") return null;
  return out;
}

export async function POST(req: NextRequest) {
  // Unlike /api/generate-strategy, this route no longer calls Anthropic
  // directly — it dispatches to the bot API host (see module docstring),
  // which holds its own ANTHROPIC_API_KEY. Nothing to check for here.
  const botApiUrl = process.env.BOT_API_URL;
  if (!botApiUrl) {
    return NextResponse.json(
      {
        error:
          "AI chat requires BOT_API_URL (your always-on bot API host, e.g. Railway) to be set on the server — see dashboard/.env.example.",
      },
      { status: 503 }
    );
  }

  let body: { messages?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const messages = sanitizeMessages(body.messages);
  if (!messages) {
    return NextResponse.json(
      { error: "messages must be 1-24 non-empty user/assistant turns ending with a user message." },
      { status: 400 }
    );
  }

  const { bars, source } = await loadBars();
  const digest = bars.length ? buildWeeklyEdgeSummary(bars) : "(no historical data imported yet)";

  const anthropicBody = {
    model: "claude-opus-4-8",
    max_tokens: 4096,
    thinking: { type: "adaptive" },
    // Identical on every turn of a conversation (digest/reference config
    // only change when historical data is reimported) — mark cacheable so
    // repeat turns within the cache TTL bill ~10% of input cost for this
    // block instead of full price on every message.
    system: [
      {
        type: "text",
        text:
          SYSTEM_PROMPT +
          `\n\nData source: ${source === "supabase" ? "real imported historical bars" : "SYNTHETIC sample data — warn the trader that patterns here are meaningless until real data is imported"}.` +
          `\n\nWeekly digest (first + last trading day of each week, oldest first):\n${digest}` +
          `\n\nReference config (JJ's default strategy):\n${JSON.stringify(JJ_DEFAULT_STRATEGY)}`,
        cache_control: { type: "ephemeral" },
      },
    ],
    output_config: {
      format: { type: "json_schema", schema: CHAT_JSON_SCHEMA },
      // No longer needs to bias for speed against a 60s ceiling — the actual
      // call runs on the bot API host now, which isn't time-boxed. Kept
      // moderate rather than maxed out purely to keep cost/latency sane.
      effort: "medium",
    },
    messages,
  };

  try {
    const dispatchRes = await fetch(`${botApiUrl.replace(/\/$/, "")}/api/ai-job`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: anthropicBody }),
    });
    const dispatchText = await dispatchRes.text();
    let dispatchData: { job_id?: string; detail?: string };
    try {
      dispatchData = JSON.parse(dispatchText);
    } catch {
      return NextResponse.json(
        { error: `Bot API host returned a non-JSON response: ${dispatchText.slice(0, 200)}` },
        { status: 502 }
      );
    }
    if (!dispatchRes.ok || !dispatchData.job_id) {
      return NextResponse.json(
        { error: dispatchData.detail || "Failed to start the AI job on the bot API host." },
        { status: 502 }
      );
    }
    return NextResponse.json({ jobId: dispatchData.job_id, dataSource: source });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: `Could not reach the bot API host: ${message}` }, { status: 502 });
  }
}
