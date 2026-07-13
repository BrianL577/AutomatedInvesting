/**
 * Historical 1-minute NQ bars for backtesting.
 *
 * Preferred source: a Supabase `bars` table (see supabase/schema.sql),
 * populated with real historical data via scripts/import_bars.py. Falls back
 * to a bundled synthetic sample (data/sample_bars.json) so the Strategy
 * Creator works out of the box — results on the sample are clearly labeled
 * as synthetic, not real market performance.
 *
 * Performance: PostgREST caps each request at 1,000 rows, so multiple years
 * of 1-minute data is hundreds of pages. Fetching those sequentially on
 * every API call blew Vercel's 60s function limit
 * (FUNCTION_INVOCATION_TIMEOUT). We read the exact row count once, then
 * fetch pages and keep the result in module memory so warm invocations skip
 * the download entirely.
 *
 * When the dataset is small (<= MAX_BARS), we fetch ascending with OFFSET
 * pagination and bounded concurrency — cheap at small offsets.
 *
 * When the dataset EXCEEDS MAX_BARS (multi-million-row tables), we instead
 * fetch the MOST RECENT MAX_BARS rows using KEYSET (cursor) pagination —
 * "t < <oldest timestamp seen so far>", not OFFSET. This matters a lot at
 * scale: Postgres OFFSET has to scan through every skipped row before
 * returning a page, so a deep offset (e.g. 499,000) against a multi-million
 * row table is slow and can exceed Postgres's statement_timeout under
 * concurrent load — keyset pagination is a fast indexed range scan
 * regardless of how deep into the table it goes. Both a growing sync should
 * always surface fresh data (never stall on the oldest slice once the cap
 * is crossed), and a single failed page must not silently nuke the entire
 * load back to synthetic sample data — every failure here is logged so a
 * real problem is never invisible.
 */
import { promises as fs } from "fs";
import path from "path";
import type { Bar } from "./backtester";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

// ~2-3 years of near-continuous 1-minute NQ data (CME trades ~23h/day,
// 5 days/week), kept safely under Vercel's 60s function budget even with
// sequential keyset pagination (see module doc comment).
const MAX_BARS = 200_000;
// PostgREST caps every request at 1000 rows regardless of the requested limit.
const PAGE_SIZE = 1_000;
const CONCURRENCY = 12;
const CACHE_TTL_MS = 10 * 60 * 1000;
const MAX_RETRIES = 2;

export type BarsResult = { bars: Bar[]; source: "supabase" | "sample" };

let cache: (BarsResult & { at: number }) | null = null;

function supabaseHeaders(extra?: Record<string, string>): Record<string, string> {
  return {
    apikey: SUPABASE_ANON_KEY!,
    Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
    ...extra,
  };
}

/** GET with a couple of retries on transient failures (network blip,
 * momentary timeout) — a single flaky request must not nuke an entire
 * multi-hundred-page load back to synthetic sample data. Logs every
 * failure (including the final one) so a real problem is always visible in
 * Vercel's function logs instead of silently degrading. */
async function fetchWithRetry(url: string, context: string): Promise<Response | null> {
  let lastError = "";
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const res = await fetch(url, { headers: supabaseHeaders(), cache: "no-store" });
      if (res.ok) return res;
      lastError = `HTTP ${res.status}: ${await res.text().catch(() => "")}`;
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
    }
    console.error(`[bars] ${context} attempt ${attempt + 1}/${MAX_RETRIES + 1} failed: ${lastError}`);
  }
  console.error(`[bars] ${context} gave up after ${MAX_RETRIES + 1} attempts: ${lastError}`);
  return null;
}

/** Total row count via a zero-row request with Prefer: count=exact. */
async function fetchBarCount(baseUrl: string): Promise<number | null> {
  const res = await fetchWithRetry(`${baseUrl}/rest/v1/bars?select=t&limit=1`, "fetchBarCount");
  if (!res) return null;
  // content-range looks like "0-0/123456"
  const total = res.headers.get("content-range")?.split("/")[1];
  const n = total ? parseInt(total, 10) : NaN;
  if (!Number.isFinite(n)) {
    console.error(`[bars] fetchBarCount: could not parse content-range header (got "${res.headers.get("content-range")}")`);
    return null;
  }
  return n;
}

/** Ascending OFFSET pagination — only used when the whole table fits under
 * MAX_BARS, where offsets stay small and cheap. */
async function fetchAllAscending(baseUrl: string, total: number): Promise<Bar[] | null> {
  const offsets: number[] = [];
  for (let offset = 0; offset < total; offset += PAGE_SIZE) offsets.push(offset);

  const pages: Bar[][] = new Array(offsets.length);
  let failed = false;
  let next = 0;
  const worker = async () => {
    while (!failed) {
      const i = next++;
      if (i >= offsets.length) return;
      const res = await fetchWithRetry(
        `${baseUrl}/rest/v1/bars?select=t,o,h,l,c,v&order=t.asc&limit=${PAGE_SIZE}&offset=${offsets[i]}`,
        `fetchAllAscending page ${i + 1}/${offsets.length}`
      );
      if (res === null) {
        failed = true;
        return;
      }
      pages[i] = (await res.json()) as Bar[];
    }
  };
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, offsets.length) }, worker));
  if (failed) return null;
  return pages.flat();
}

/** Keyset (cursor) pagination for the MOST RECENT `maxBars` rows — fast
 * indexed range scans ("t < cursor ORDER BY t DESC LIMIT N"), no matter how
 * deep into a multi-million-row table it reaches, unlike OFFSET. Necessarily
 * sequential (each page's cursor depends on the previous page's last row),
 * but each page is cheap, so this stays well within the function time
 * budget even for MAX_BARS pages. */
async function fetchMostRecent(baseUrl: string, maxBars: number): Promise<Bar[] | null> {
  const pagesDesc: Bar[][] = [];
  let cursor: string | null = null;
  let fetched = 0;
  let pageNum = 0;
  while (fetched < maxBars) {
    pageNum++;
    const limit = Math.min(PAGE_SIZE, maxBars - fetched);
    const filter = cursor ? `&t=lt.${encodeURIComponent(cursor)}` : "";
    const res = await fetchWithRetry(
      `${baseUrl}/rest/v1/bars?select=t,o,h,l,c,v&order=t.desc&limit=${limit}${filter}`,
      `fetchMostRecent page ${pageNum}`
    );
    if (res === null) return null;
    const page = (await res.json()) as Bar[];
    if (page.length === 0) break; // reached the start of the table
    pagesDesc.push(page);
    fetched += page.length;
    cursor = page[page.length - 1].t; // oldest timestamp in this page = next cursor
  }
  const all = pagesDesc.flat();
  all.reverse(); // newest-first pages -> chronological ascending
  return all;
}

async function loadBarsFromSupabase(): Promise<Bar[] | null> {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    console.error("[bars] NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY not set — using sample data.");
    return null;
  }
  try {
    const baseUrl = SUPABASE_URL.replace(/\/$/, "");
    const count = await fetchBarCount(baseUrl);
    if (!count || count <= 0) {
      console.error(`[bars] fetchBarCount returned ${count} — falling back to sample data.`);
      return null;
    }

    const all = count > MAX_BARS ? await fetchMostRecent(baseUrl, MAX_BARS) : await fetchAllAscending(baseUrl, count);
    if (all === null) {
      console.error(`[bars] failed to load bars from Supabase (table has ${count} rows) — falling back to sample data.`);
      return null;
    }
    if (all.length === 0) {
      console.error("[bars] Supabase returned 0 bars despite a positive count — falling back to sample data.");
      return null;
    }
    return all;
  } catch (err) {
    console.error(`[bars] unexpected error loading bars from Supabase: ${err instanceof Error ? err.stack : err} — falling back to sample data.`);
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
