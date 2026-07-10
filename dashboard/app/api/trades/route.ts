import { NextResponse } from "next/server";
import { loadTrades } from "../../../lib/trades";

export const dynamic = "force-dynamic";

export type { Trade } from "../../../lib/types";

export async function GET() {
  const trades = await loadTrades();
  return NextResponse.json({ trades });
}
