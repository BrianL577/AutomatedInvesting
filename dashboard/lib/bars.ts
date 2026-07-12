/**
 * Historical 1-minute NQ bars for backtesting.
 *
 * Preferred source: a Supabase `bars` table (see supabase/schema.sql),
 * populated with real historical data via scripts/import_bars.py. Falls back
 * to a bundled synthetic sample (data/sample_bars.json) so the Strategy
 * Creator works out of the box — results on the sample are clearly labeled
 * as synthetic, not real market performance.
 *
 * Performance: PostgREST caps each request at 1,000 rows, so a year of
 * 1-minute data is 100+ pages. Fetching those sequentially on every API call
 * blew Vercel's 60s function limit (FUNCTION_INVOCATION_TIMEOUT). Instead we
 * read the exact row count once, fetch all pages with bounded concurrency,
 * and keep the result in module memory so warm invocations skip the download
 * entirely.
 */
import { promises as fs } from "fs";
import path from "path";
import type { Bar } from "./backtester";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

const MAX_BARS = 200_000;
// PostgREST caps every request at 1000 rows regardless of the requested limit.
const PAGE_SIZE = 1_000;
const CONCURRENCY = 12;
const CACHE_TTL_MS = 10 * 60 * 1000;

export type BarsResult = { bars: Bar[]; source: "supabase" | "sample" };

let cache: (BarsResult & { at: number }) | null = null;

function supabaseHeaders(extra?: Record<string, string>): Record<string, string> {
  return {
    apikey: SUPABASE_ANON_KEY!,
    Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
    ...extra,
  };
}

/** Total row count via a zero-row request with Prefer: count=exact. */
async function fetchBarCount(baseUrl: string): Promise<number | null> {
  const res = await fetch(`${baseUrl}/rest/v1/bars?select=t&limit=1`, {
    headers: supabaseHeaders({ Prefer: "count=exact", Range: "0-0" }),
    cache: "no-store",
  });
  if (!res.ok) return null;
  // content-range looks like "0-0/123456"
  const total = res.headers.get("content-range")?.split("/")[1];
  const n = total ? parseInt(total, 10) : NaN;
  return Number.isFinite(n) ? n : null;
}

async function fetchPage(baseUrl: string, offset: number): Promise<Bar[] | null> {
  const res = await fetch(
    `${baseUrl}/rest/v1/bars?select=t,o,h,l,c,v&order=t.asc&limit=${PAGE_SIZE}&offset=${offset}`,
    { headers: supabaseHeaders(), cache: "no-store" }
  );
  if (!res.ok) return null;
  return (await res.json()) as Bar[];
}

async function loadBarsFromSupabase(): Promise<Bar[] | null> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null;
  try {
    const baseUrl = SUPABASE_URL.replace(/\/$/, "");
    const count = await fetchBarCount(baseUrl);
    if (!count || count <= 0) return null;

    const total = Math.min(count, MAX_BARS);
    const offsets: number[] = [];
    for (let offset = 0; offset < total; offset += PAGE_SIZE) offsets.push(offset);

    const pages: Bar[][] = new Array(offsets.length);
    let failed = false;
    let next = 0;
    const worker = async () => {
      while (!failed) {
        const i = next++;
        if (i >= offsets.length) return;
        const page = await fetchPage(baseUrl, offsets[i]);
        if (page === null) {
          failed = true;
          return;
        }
        pages[i] = page;
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, offsets.length) }, worker));
    if (failed) return null;

    const all = pages.flat();
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
  if (cache && Date.now() - cache.at < CACHE_TTL_MS) {
    return { bars: cache.bars, source: cache.source };
  }
  const fromSupabase = await loadBarsFromSupabase();
  const result: BarsResult = fromSupabase
    ? { bars: fromSupabase, source: "supabase" }
    : { bars: await loadSampleBars(), source: "sample" };
  // Only cache real data; the sample fallback may just mean Supabase was
  // briefly unreachable, and we don't want to pin synthetic results for 10min.
  if (result.source === "supabase") {
    cache = { ...result, at: Date.now() };
  }
  return result;
}
