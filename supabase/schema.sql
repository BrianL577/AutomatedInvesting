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
-- Strategy Creator: saved strategies
-- ---------------------------------------------------------------------------
-- Strategies are parameter sets (JSONB) over the fixed rule engine — never
-- code. They are written only through the dashboard's server-side API route
-- (service role key) after zod validation, and read publicly via anon.

create table if not exists public.strategies (
  id uuid primary key default gen_random_uuid(),
  config jsonb not null,
  source text not null check (source in ('ai', 'manual')),
  prompt text,
  created_at timestamptz not null default now()
);

alter table public.strategies enable row level security;

create policy "Public can read strategies"
  on public.strategies
  for select
  to anon
  using (true);

-- Writes: service role only (bypasses RLS; no anon insert policy on purpose).

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
