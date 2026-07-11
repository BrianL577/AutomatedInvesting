/**
 * Server-side strategy persistence (Supabase `strategies` table).
 *
 * Writes go through the service role key, which must only ever live in
 * server-side env (SUPABASE_SERVICE_ROLE_KEY on Vercel — never NEXT_PUBLIC_*).
 * Reads use the anon key via RLS. If Supabase isn't configured, the store
 * degrades gracefully: the JJ default strategy is always available and
 * backtests still run; saving custom strategies just requires setup.
 */
import { SavedStrategy, StrategyConfig, JJ_DEFAULT_STRATEGY } from "./strategySchema";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
const SERVICE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

export const storeConfigured = Boolean(SUPABASE_URL && ANON_KEY);
export const storeWritable = Boolean(SUPABASE_URL && SERVICE_KEY);

const DEFAULT_ENTRY: SavedStrategy = {
  id: "default-jj",
  config: JJ_DEFAULT_STRATEGY,
  source: "default",
  prompt: null,
  created_at: "2026-01-01T00:00:00Z",
};

export async function listStrategies(): Promise<SavedStrategy[]> {
  if (!storeConfigured) return [DEFAULT_ENTRY];
  try {
    const res = await fetch(
      `${SUPABASE_URL!.replace(/\/$/, "")}/rest/v1/strategies?select=id,config,source,prompt,created_at&order=created_at.desc`,
      {
        headers: { apikey: ANON_KEY!, Authorization: `Bearer ${ANON_KEY!}` },
        cache: "no-store",
      }
    );
    if (!res.ok) return [DEFAULT_ENTRY];
    const rows = (await res.json()) as SavedStrategy[];
    return [DEFAULT_ENTRY, ...rows];
  } catch {
    return [DEFAULT_ENTRY];
  }
}

export async function saveStrategy(
  config: StrategyConfig,
  source: "ai" | "manual",
  prompt?: string
): Promise<{ ok: true; id: string } | { ok: false; error: string }> {
  if (!storeWritable) {
    return {
      ok: false,
      error:
        "Strategy saving requires Supabase (set NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY on the server). You can still backtest without saving.",
    };
  }
  const res = await fetch(`${SUPABASE_URL!.replace(/\/$/, "")}/rest/v1/strategies`, {
    method: "POST",
    headers: {
      apikey: SERVICE_KEY!,
      Authorization: `Bearer ${SERVICE_KEY!}`,
      "Content-Type": "application/json",
      Prefer: "return=representation",
    },
    body: JSON.stringify({ config, source, prompt: prompt ?? null }),
  });
  if (!res.ok) {
    return { ok: false, error: `Supabase insert failed (${res.status}): ${(await res.text()).slice(0, 300)}` };
  }
  const [row] = (await res.json()) as { id: string }[];
  return { ok: true, id: row.id };
}
