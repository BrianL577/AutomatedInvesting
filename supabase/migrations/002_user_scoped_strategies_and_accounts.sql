-- Run this ONLY if you already ran the original supabase/schema.sql before
-- user accounts existed (i.e. your `strategies` table has no `user_id`
-- column yet). If you're setting up Supabase for the first time, just run
-- the current supabase/schema.sql instead — it already includes this.
--
-- This migration: (1) makes strategies private per user, (2) adds the
-- tradovate_accounts table.

-- Any pre-auth strategy rows have no owner and can't be attributed to a
-- user — remove them (there's no "existing user's strategies" to preserve
-- from before auth existed).
delete from public.strategies;

alter table public.strategies
  add column if not exists user_id uuid references auth.users (id) on delete cascade;

alter table public.strategies
  alter column user_id set not null;

create index if not exists strategies_user_id_idx on public.strategies (user_id, created_at desc);

drop policy if exists "Public can read strategies" on public.strategies;

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
