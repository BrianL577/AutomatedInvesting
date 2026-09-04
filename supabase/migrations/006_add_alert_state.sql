-- Tiny dedupe table for the dashboard's outage-monitoring cron
-- (dashboard/app/api/health-check/route.ts) -- tracks the last time an
-- outage alert email was sent so a continued outage doesn't spam an email
-- on every cron tick, just once per cooldown window.
create table if not exists public.alert_state (
  id text primary key,
  last_alert_at timestamptz
);

alter table public.alert_state enable row level security;

-- Unlike public.trades (service-role-only writes), this table holds
-- nothing sensitive -- just a dedupe timestamp for the outage-monitoring
-- cron -- so it's opened up to the anon key too. That lets the dashboard's
-- health-check route reuse the SAME NEXT_PUBLIC_SUPABASE_ANON_KEY it
-- already has configured, rather than requiring the user to additionally
-- set up SUPABASE_SERVICE_ROLE_KEY as a Vercel env var just for this.
-- Worst case if someone else pokes this row: one extra alert email gets
-- sent early -- not a real security concern.
drop policy if exists "Public can read alert state" on public.alert_state;
create policy "Public can read alert state"
  on public.alert_state
  for select
  to anon
  using (true);

drop policy if exists "Public can upsert alert state" on public.alert_state;
create policy "Public can upsert alert state"
  on public.alert_state
  for insert
  to anon
  with check (true);

drop policy if exists "Public can update alert state" on public.alert_state;
create policy "Public can update alert state"
  on public.alert_state
  for update
  to anon
  using (true)
  with check (true);
