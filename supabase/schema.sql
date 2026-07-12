-- Run this once in your Supabase project's SQL editor (Project -> SQL Editor -> New query).
--
-- This backs the live trade log: the Python bot (jj_bot/trade_logger.py)
-- writes here using the service role key, and the Vercel dashboard reads
-- here using the anon key (read-only, via the RLS policy below).

create table if not exists public.trades (
  id bigint generated always as identity primary key,
  timestamp timestamptz not null,
  exit_timestamp timestamptz not null,
  phase text not null,
  direction text not null,
  grade text not null,
  reason text not null,
  entry_price numeric not null,
  exit_price numeric not null,
  stop_price numeric not null,
  target_price numeric not null,
  win boolean not null,
  pnl_points numeric not null,
  pnl_dollars numeric not null,
  source text not null,
  account_name text,
  logged_at timestamptz not null default now()
);

create index if not exists trades_timestamp_idx on public.trades (timestamp desc);

alter table public.trades enable row level security;

-- Public read access (the dashboard uses the anon key, no login flow yet).
-- Tighten this later (e.g. require an authenticated session) once the
-- dashboard has auth — for now every trade is written by the bot with the
-- service role key, which bypasses RLS entirely, so this policy only
-- controls who can *read*.
create policy "Public can read trades"
  on public.trades
  for select
  to anon
  using (true);

-- No insert/update/delete policy for anon/authenticated roles is created on
-- purpose: only the service role key (used server-side by the Python bot)
-- can write, since it bypasses RLS. The dashboard's anon key can never
-- write trades directly.

-- ---------------------------------------------------------------------------
-- Strategy Creator: saved strategies (PRIVATE per user)
-- ---------------------------------------------------------------------------
-- Strategies are parameter sets (JSONB) over the fixed rule engine — never
-- code. Each strategy belongs to exactly one user (auth.uid()) and RLS
-- ensures a user can only ever see/edit/delete their own. The built-in JJ
-- default strategy is not a row in this table — it's shipped in app code
-- and shown to everyone alongside each user's private list.

create table if not exists public.strategies (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  config jsonb not null,
  source text not null check (source in ('ai', 'manual')),
  prompt text,
  created_at timestamptz not null default now()
);

create index if not exists strategies_user_id_idx on public.strategies (user_id, created_at desc);

alter table public.strategies enable row level security;

create policy "Users can read their own strategies"
  on public.strategies
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy "Users can insert their own strategies"
  on public.strategies
  for insert
  to authenticated
  with check (auth.uid() = user_id);

create policy "Users can delete their own strategies"
  on public.strategies
  for delete
  to authenticated
  using (auth.uid() = user_id);

-- No anon policy at all: signed-out visitors see only the built-in default.

-- ---------------------------------------------------------------------------
-- Strategy Creator: saved AI-Optimize runs (PRIVATE per user)
-- ---------------------------------------------------------------------------
-- Each AI-Optimize call costs real tokens (system prompt + leaderboard per
-- round). Without persistence, the resulting leaderboard vanished on
-- refresh, forcing a full re-run (and re-spend) just to look at it again.
-- Saving the whole run — base config, every variant tried, and the winner —
-- lets a user revisit or reuse past results for free.

create table if not exists public.optimizations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  base_config jsonb not null,
  base_config_name text not null,
  rounds integer not null,
  data_source text not null,
  history jsonb not null,
  best_config jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists optimizations_user_id_idx on public.optimizations (user_id, created_at desc);

alter table public.optimizations enable row level security;

create policy "Users can read their own optimizations"
  on public.optimizations
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy "Users can insert their own optimizations"
  on public.optimizations
  for insert
  to authenticated
  with check (auth.uid() = user_id);

create policy "Users can delete their own optimizations"
  on public.optimizations
  for delete
  to authenticated
  using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- Strategy Creator / live bot: saved Tradovate account names (PRIVATE per user)
-- ---------------------------------------------------------------------------
-- Lets each user enter and manage the Tradovate account name(s) they trade,
-- instead of hardcoding TRADOVATE_ACCOUNT_NAMES for one operator. Only the
-- account *name* (e.g. "DEMO12345") is stored here — never Tradovate
-- passwords/CID/SEC, which stay in the bot host's own env vars.

create table if not exists public.tradovate_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  account_name text not null,
  label text,
  created_at timestamptz not null default now(),
  unique (user_id, account_name)
);

create index if not exists tradovate_accounts_user_id_idx on public.tradovate_accounts (user_id, created_at desc);

alter table public.tradovate_accounts enable row level security;

create policy "Users can read their own accounts"
  on public.tradovate_accounts
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy "Users can insert their own accounts"
  on public.tradovate_accounts
  for insert
  to authenticated
  with check (auth.uid() = user_id);

create policy "Users can delete their own accounts"
  on public.tradovate_accounts
  for delete
  to authenticated
  using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- Strategy Creator: historical 1-minute NQ bars for backtesting
-- ---------------------------------------------------------------------------
-- Populate with real historical data via scripts/import_bars.py. Until this
-- table has rows, the dashboard backtests against a bundled synthetic sample
-- and labels the results accordingly.

create table if not exists public.bars (
  t timestamptz primary key,
  o numeric not null,
  h numeric not null,
  l numeric not null,
  c numeric not null,
  v numeric not null default 0
);

create index if not exists bars_t_idx on public.bars (t asc);

alter table public.bars enable row level security;

create policy "Public can read bars"
  on public.bars
  for select
  to anon
  using (true);
