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

function defaultEntry(isActive: boolean): SavedStrategy {
  return {
    id: "default-jj",
    config: JJ_DEFAULT_STRATEGY,
    source: "default",
    prompt: null,
    created_at: "2026-01-01T00:00:00Z",
    is_active: isActive,
  };
}

export async function listStrategiesForCurrentUser(): Promise<{
  strategies: SavedStrategy[];
  signedIn: boolean;
}> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) return { strategies: [defaultEntry(true)], signedIn: false };

  const { data, error } = await supabase
    .from("strategies")
    .select("id,config,source,prompt,created_at,is_active")
    .order("created_at", { ascending: false });

  if (error) return { strategies: [defaultEntry(true)], signedIn: true };
  const rows = data as SavedStrategy[];
  const anyActive = rows.some((s) => s.is_active);
  return { strategies: [defaultEntry(!anyActive), ...rows], signedIn: true };
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

/**
 * Marks one saved strategy as the active one — this is what jj_bot/config.py
 * fetches and the live bot actually trades, instead of always the built-in
 * JJ default. Pass null (or "default-jj") to clear the active flag, which
 * makes the bot fall back to the JJ default. At most one row per user can
 * be is_active=true (enforced by a partial unique index), so this clears
 * every other row for the user first.
 */
export async function setActiveStrategyForCurrentUser(
  id: string | null
): Promise<{ ok: true } | { ok: false; status: number; error: string }> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) return { ok: false, status: 401, error: "Sign in required." };

  const { error: clearError } = await supabase
    .from("strategies")
    .update({ is_active: false })
    .eq("user_id", user.id);
  if (clearError) return { ok: false, status: 500, error: clearError.message };

  if (!id || id === "default-jj") return { ok: true };

  const { error: setError } = await supabase
    .from("strategies")
    .update({ is_active: true })
    .eq("id", id)
    .eq("user_id", user.id);
  if (setError) return { ok: false, status: 500, error: setError.message };
  return { ok: true };
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
