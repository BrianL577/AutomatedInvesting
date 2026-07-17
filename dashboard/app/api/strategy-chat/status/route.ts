/**
 * GET /api/strategy-chat/status?jobId=... — poll the result of a job
 * started by POST /api/strategy-chat. Each poll is a quick, well-under-10s
 * fetch to the bot API host and back — all the slow work happens there, not
 * in this function. See ../route.ts for why this two-step shape exists.
 */
import { NextRequest, NextResponse } from "next/server";
import { StrategyConfigSchema, survivabilityViolation } from "../../../../lib/strategySchema";

export const dynamic = "force-dynamic";
export const maxDuration = 10;

type AnthropicContentBlock = { type: string; text?: string };
type AnthropicMessage = { stop_reason?: string; content?: AnthropicContentBlock[] };

export async function GET(req: NextRequest) {
  const botApiUrl = process.env.BOT_API_URL;
  if (!botApiUrl) {
    return NextResponse.json(
      { error: "BOT_API_URL is not set on the server — see dashboard/.env.example." },
      { status: 503 }
    );
  }

  const jobId = req.nextUrl.searchParams.get("jobId");
  if (!jobId) {
    return NextResponse.json({ error: "jobId query param is required" }, { status: 400 });
  }

  let jobRes: Response;
  try {
    jobRes = await fetch(`${botApiUrl.replace(/\/$/, "")}/api/ai-job/${encodeURIComponent(jobId)}`);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : "unknown error";
    return NextResponse.json({ error: `Could not reach the bot API host: ${message}` }, { status: 502 });
  }

  if (jobRes.status === 404) {
    return NextResponse.json({ error: "Unknown or expired job. Try sending the message again." }, { status: 404 });
  }

  const jobText = await jobRes.text();
  let job: { status?: string; result?: AnthropicMessage; error?: string; status_code?: number };
  try {
    job = JSON.parse(jobText);
  } catch {
    return NextResponse.json(
      { error: `Bot API host returned a non-JSON response: ${jobText.slice(0, 200)}` },
      { status: 502 }
    );
  }

  if (!jobRes.ok) {
    return NextResponse.json({ error: job.error || "Failed to fetch job status." }, { status: 502 });
  }

  if (job.status === "pending") {
    return NextResponse.json({ status: "pending" });
  }

  if (job.status === "error") {
    if (job.status_code === 429) {
      return NextResponse.json({ error: "AI is rate-limited right now — try again shortly." }, { status: 429 });
    }
    return NextResponse.json({ error: `AI request failed: ${job.error || "unknown error"}` }, { status: 502 });
  }

  // job.status === "done"
  const response = job.result;
  if (!response) {
    return NextResponse.json({ error: "Job finished with no result." }, { status: 502 });
  }

  if (response.stop_reason === "refusal") {
    return NextResponse.json(
      { error: "The model declined to respond to this message. Try rephrasing." },
      { status: 422 }
    );
  }

  const text = response.content?.find((b) => b.type === "text");
  if (!text || typeof text.text !== "string") {
    return NextResponse.json({ error: "Model returned no reply." }, { status: 502 });
  }

  let parsed: { reply?: unknown; config?: unknown };
  try {
    parsed = JSON.parse(text.text);
  } catch {
    return NextResponse.json({ error: "Model returned malformed output." }, { status: 502 });
  }

  const reply = typeof parsed.reply === "string" ? parsed.reply : "";
  if (!reply) {
    return NextResponse.json({ error: "Model returned an empty reply." }, { status: 502 });
  }

  let config = null;
  let finalReply = reply;
  if (parsed.config !== null && parsed.config !== undefined) {
    const check = StrategyConfigSchema.safeParse(parsed.config);
    if (check.success && (check.data.phases.tradeContinuation || check.data.phases.tradeReversion)) {
      const violation = survivabilityViolation(check.data);
      if (violation) {
        // Strip the config rather than fail the whole turn — the
        // conversation continues and the trader can ask for an adjusted
        // (safer) version.
        finalReply += `\n\n⚠️ I generated a config, but withheld it: ${violation}`;
      } else {
        config = check.data;
      }
    }
    // An invalid config silently degrades to reply-only — the conversation
    // continues and the trader can ask for it again.
  }

  return NextResponse.json({ status: "done", reply: finalReply, config });
}
