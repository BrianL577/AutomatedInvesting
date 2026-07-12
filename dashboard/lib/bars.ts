/**
 * Historical 1-minute NQ bars for backtesting.
 *
 * Preferred source: a Supabase `bars` table (see supabase/schema.sql),
 * populated with real historical data via scripts/import_bars.py. Falls back
 * to a bundled synthetic sample (data/sample_bars.json) so the Strategy
 * Creator works out of the box — results on the sample are clearly labeled
 * as synthetic, not real market performance.
 */
import { promises as fs } from "fs";
import path from "path";
import type { Bar } from "./backtester";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

const MAX_BARS = 200_000;

export type BarsResult = { bars: Bar[]; source: "supabase" | "sample" };

async function loadBarsFromSupabase(): Promise<Bar[] | null> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null;
  try {
    const all: Bar[] = [];
    // Supabase's PostgREST API caps every request at 1000 rows regardless of
    // the requested `limit`, so pageSize must match that or pagination stops
    // after the first (silently truncated) page.
    const pageSize = 1_000;
    for (let offset = 0; offset < MAX_BARS; offset += pageSize) {
      const res = await fetch(
        `${SUPABASE_URL.replace(/\/$/, "")}/rest/v1/bars?select=t,o,h,l,c,v&order=t.asc&limit=${pageSize}&offset=${offset}`,
        {
          headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}` },
          cache: "no-store",
        }
      );
      if (!res.ok) return null;
      const page = (await res.json()) as Bar[];
      all.push(...page);
      if (page.length < pageSize) break;
    }
    return all.length > 0 ? all : null;
  } catch {
    return null;
  }
}

async function loadSampleBars(): Promise<Bar[]> {
  const filePath = path.join(process.cwd(), "data", "sample_bars.json");
  const raw = await fs.readFile(filePath, "utf-8");
  return JSON.parse(raw) as Bar[];
}

export async function loadBars(): Promise<BarsResult> {
  const fromSupabase = await loadBarsFromSupabase();
  if (fromSupabase) return { bars: fromSupabase, source: "supabase" };
  return { bars: await loadSampleBars(), source: "sample" };
}
