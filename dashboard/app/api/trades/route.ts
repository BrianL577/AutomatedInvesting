import { NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";

export const dynamic = "force-dynamic";

export type Trade = {
  id: number;
  timestamp: string;
  exit_timestamp: string;
  phase: "continuation" | "reversion";
  direction: "long" | "short";
  grade: string;
  reason: string;
  entry_price: number;
  exit_price: number;
  stop_price: number;
  target_price: number;
  win: boolean;
  pnl_points: number;
  pnl_dollars: number;
  source: string;
  logged_at: string;
};

export async function GET() {
  const filePath = path.join(process.cwd(), "data", "trades.json");
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    const trades: Trade[] = JSON.parse(raw);
    return NextResponse.json({ trades });
  } catch (err) {
    return NextResponse.json({ trades: [], error: "No trade log found yet." });
  }
}
