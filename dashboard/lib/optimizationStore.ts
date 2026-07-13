/**
 * Per-user AI-Optimize run persistence. Same privacy model as
 * strategyStore.ts: reads/writes go through the authenticated server-side
 * Supabase client, so Postgres RLS is what actually enforces privacy.
 *
 * Why this exists: every /api/optimize call spends real tokens (system
 * prompt + growing leaderboard, per round). Without persistence, the
 * resulting leaderboard vanished on refresh — revisiting past results meant
 * paying to re-run the search. Saving the whole run once lets it be
 * reloaded for free indefinitely.
 */
import { createClient } from "./supabase/server";
import type { StrategyConfig } from "./strategySchema";
import type { BacktestResult } from "./backtester";

export type SavedOptimizationCandidate = {
  round: number;
  rationale: string;
  diff: unknown;
  fitness: number;
  result: BacktestResult;
  config: StrategyConfig;
};

export type SavedOptimization = {
  id: string;
  base_config: StrategyConfig;
  base_config_name: string;
  rounds: number;
  data_source: "supabase" | "sample";
  history: SavedOptimizationCandidate[];
  best_config: StrategyConfig;
  created_at: string;
};

export async function listOptimizationsForCurrentUser(): Promise<{
  optimizations: SavedOptimization[];
  signedIn: boolean;
}> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) return { optimizations: [], signedIn: false };

  const { data, error } = await supabase
    .from("optimizations")
    .select("id,base_config,base_config_name,rounds,data_source,history,best_config,created_at")
    .order("created_at", { ascending: false });

  if (error) return { optimizations: [], signedIn: true };
  return { optimizations: data as SavedOptimization[], signedIn: true };
}

export async function saveOptimizationForCurrentUser(params: {
  baseConfig: StrategyConfig;
  rounds: number;
  dataSource: "supabase" | "sample";
  history: SavedOptimizationCandidate[];
  bestConfig: StrategyConfig;
}): Promise<{ ok: true; id: string } | { ok: false; status: number; error: string }> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    return { ok: false, status: 401, error: "Sign in to save optimization runs." };
  }

  const { data, error } = await supabase
    .from("optimizations")
    .insert({
      user_id: user.id,
      base_config: params.baseConfig,
      base_config_name: params.baseConfig.name,
      rounds: params.rounds,
      data_source: params.dataSource,
      history: params.history,
      best_config: params.bestConfig,
    })
    .select("id")
    .single();

  if (error) return { ok: false, status: 500, error: error.message };
  return { ok: true, id: data.id };
}

export async function deleteOptimizationForCurrentUser(
  id: string
): Promise<{ ok: true } | { ok: false; status: number; error: string }> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) return { ok: false, status: 401, error: "Sign in required." };

  const { error } = await supabase.from("optimizations").delete().eq("id", id).eq("user_id", user.id);
  if (error) return { ok: false, status: 500, error: error.message };
  return { ok: true };
}
