import { NextRequest, NextResponse } from "next/server";
import { StrategyConfigSchema } from "../../../lib/strategySchema";
import { listStrategies, saveStrategy } from "../../../lib/strategyStore";

export const dynamic = "force-dynamic";

export async function GET() {
  const strategies = await listStrategies();
  return NextResponse.json({ strategies });
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { config, source, prompt } = (body ?? {}) as {
    config?: unknown;
    source?: string;
    prompt?: string;
  };

  const parsed = StrategyConfigSchema.safeParse(config);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Strategy config failed validation", issues: parsed.error.issues.slice(0, 10) },
      { status: 400 }
    );
  }
  if (typeof prompt === "string" && prompt.length > 5000) {
    return NextResponse.json({ error: "Prompt too long" }, { status: 400 });
  }

  const result = await saveStrategy(parsed.data, source === "ai" ? "ai" : "manual", prompt);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: 503 });
  return NextResponse.json({ id: result.id });
}
