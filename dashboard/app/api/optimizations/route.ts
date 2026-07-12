import { NextRequest, NextResponse } from "next/server";
import { StrategyConfigSchema } from "../../../lib/strategySchema";
import {
  deleteOptimizationForCurrentUser,
  listOptimizationsForCurrentUser,
  saveOptimizationForCurrentUser,
} from "../../../lib/optimizationStore";

export const dynamic = "force-dynamic";

export async function GET() {
  const { optimizations, signedIn } = await listOptimizationsForCurrentUser();
  return NextResponse.json({ optimizations, signedIn });
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { baseConfig, rounds, dataSource, history, bestConfig } = (body ?? {}) as {
    baseConfig?: unknown;
    rounds?: unknown;
    dataSource?: unknown;
    history?: unknown;
    bestConfig?: unknown;
  };

  const baseParsed = StrategyConfigSchema.safeParse(baseConfig);
  if (!baseParsed.success) {
    return NextResponse.json({ error: "baseConfig failed validation" }, { status: 400 });
  }
  const bestParsed = StrategyConfigSchema.safeParse(bestConfig);
  if (!bestParsed.success) {
    return NextResponse.json({ error: "bestConfig failed validation" }, { status: 400 });
  }
  if (!Array.isArray(history) || history.length === 0) {
    return NextResponse.json({ error: "history must be a non-empty array" }, { status: 400 });
  }
  if (dataSource !== "supabase" && dataSource !== "sample") {
    return NextResponse.json({ error: "dataSource must be 'supabase' or 'sample'" }, { status: 400 });
  }
  const roundsNum = Number(rounds);
  if (!Number.isInteger(roundsNum) || roundsNum < 1 || roundsNum > 5) {
    return NextResponse.json({ error: "rounds must be an integer 1-5" }, { status: 400 });
  }

  const result = await saveOptimizationForCurrentUser({
    baseConfig: baseParsed.data,
    rounds: roundsNum,
    dataSource,
    history: history as never,
    bestConfig: bestParsed.data,
  });
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
  return NextResponse.json({ id: result.id });
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "Missing id" }, { status: 400 });

  const result = await deleteOptimizationForCurrentUser(id);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
  return NextResponse.json({ ok: true });
}
