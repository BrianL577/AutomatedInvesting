/**
 * Per-user Tradovate account names — the scalable replacement for a single
 * hardcoded TRADOVATE_ACCOUNT_NAMES env var. Only the account *name* is
 * stored (e.g. "DEMO12345"); Tradovate login credentials (username,
 * password, CID, SEC) still live in the bot host's own env vars, never in
 * this table. RLS (auth.uid() = user_id) enforces privacy.
 */
import { createClient } from "./supabase/server";

export type SavedAccount = {
  id: string;
  account_name: string;
  label: string | null;
  created_at: string;
};

export async function listAccountsForCurrentUser(): Promise<
  { ok: true; accounts: SavedAccount[] } | { ok: false; status: number; error: string }
> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { ok: false, status: 401, error: "Sign in required." };

  const { data, error } = await supabase
    .from("tradovate_accounts")
    .select("id,account_name,label,created_at")
    .order("created_at", { ascending: true });
  if (error) return { ok: false, status: 500, error: error.message };
  return { ok: true, accounts: data as SavedAccount[] };
}

export async function addAccountForCurrentUser(
  accountName: string,
  label?: string
): Promise<{ ok: true; account: SavedAccount } | { ok: false; status: number; error: string }> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { ok: false, status: 401, error: "Sign in required." };

  const { data, error } = await supabase
    .from("tradovate_accounts")
    .insert({ user_id: user.id, account_name: accountName, label: label ?? null })
    .select("id,account_name,label,created_at")
    .single();

  if (error) {
    if (error.code === "23505") {
      return { ok: false, status: 409, error: "You've already saved that account name." };
    }
    return { ok: false, status: 500, error: error.message };
  }
  return { ok: true, account: data as SavedAccount };
}

export async function deleteAccountForCurrentUser(
  id: string
): Promise<{ ok: true } | { ok: false; status: number; error: string }> {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { ok: false, status: 401, error: "Sign in required." };

  const { error } = await supabase.from("tradovate_accounts").delete().eq("id", id).eq("user_id", user.id);
  if (error) return { ok: false, status: 500, error: error.message };
  return { ok: true };
}
