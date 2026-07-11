/**
 * Per-user strategy persistence. Reads/writes go through the authenticated
 * server-side Supabase client (lib/supabase/server.ts), so Postgres RLS
 * (`auth.uid() = user_id`) is what actually enforces privacy — this module
 * never bypasses it with the service role key. A signed-out visitor gets
 * only the built-in JJ default; a signed-in user gets the default plus
 * their own saved strategies, never anyone else's.
 */
import { createClient } from "./supabase/server";
import { SavedStrategy, StrategyConfig, JJ_DEFAULT_STRATEGY } from "./strategySchema";

const DEFAULT_ENTRY: SavedStrategy = {
  id: "default-jj",
  config: JJ_DEFAULT_STRATEGY,
  source: "default",
  prompt: null,
  created_at: "2026-01-01T00:00:00Z",
};

export async function listStrategiesForCurrentUser(): Promise<{
  strategies: SavedStrategy[];
  signedIn: boolean;
}> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) return { strategies: [DEFAULT_ENTRY], signedIn: false };

  const { data, error } = await supabase
    .from("strategies")
    .select("id,config,source,prompt,created_at")
    .order("created_at", { ascending: false });

  if (error) return { strategies: [DEFAULT_ENTRY], signedIn: true };
  return { strategies: [DEFAULT_ENTRY, ...(data as SavedStrategy[])], signedIn: true };
}

export async function saveStrategyForCurrentUser(
  config: StrategyConfig,
  source: "ai" | "manual",
  prompt?: string
): Promise<{ ok: true; id: string } | { ok: false; status: number; error: string }> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return { ok: false, status: 401, error: "Sign in to save strategies." };
  }

  const { data, error } = await supabase
    .from("strategies")
    .insert({ user_id: user.id, config, source, prompt: prompt ?? null })
    .select("id")
    .single();

  if (error) return { ok: false, status: 500, error: error.message };
  return { ok: true, id: data.id };
}

export async function deleteStrategyForCurrentUser(
  id: string
): Promise<{ ok: true } | { ok: false; status: number; error: string }> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) return { ok: false, status: 401, error: "Sign in required." };

  // RLS also enforces ownership; this check just gives a clean 403 instead
  // of a silent no-op delete.
  const { error } = await supabase.from("strategies").delete().eq("id", id).eq("user_id", user.id);
  if (error) return { ok: false, status: 500, error: error.message };
  return { ok: true };
}
