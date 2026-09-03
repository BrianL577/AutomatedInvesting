-- Captures WHY a trade chart failed to upload to Supabase Storage (bucket
-- creation failed, upload failed, local file missing) directly on the trade
-- row, so it's diagnosable via SQL alone -- no access to whatever machine is
-- actually running the bot required. Null whenever chart_url is set, or when
-- there was never a chart to upload in the first place.
alter table public.trades add column if not exists chart_upload_error text;
