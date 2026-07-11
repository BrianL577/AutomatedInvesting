/**
 * POST /api/backtest — runs a validated strategy config against historical
 * bars (Supabase `bars` table when populated, bundled synthetic sample
 * otherwise) and returns the full yield report.
 */
import { NextRequest, NextResponse } from "next/server";
import { runBacktest } from "../../../lib/backtester";
import { loadBars } from "../../../lib/bars";
import { StrategyConfigSchema } from "../../../lib/strategySchema";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(req: NextRequest) {
  let body: { config?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = StrategyConfigSchema.safeParse(body.config);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Strategy config failed validation", issues: parsed.error.issues.slice(0, 10) },
      { status: 400 }
    );
  }

  const { bars, source } = await loadBars();
  if (!bars.length) {
    return NextResponse.json({ error: "No historical bars available." }, { status: 503 });
  }

  const result = runBacktest(parsed.data, bars);
  // Cap the trade list in the response; the stats cover everything.
  const trades = result.trades.slice(-200);
  return NextResponse.json({ ...result, trades, dataSource: source });
}
