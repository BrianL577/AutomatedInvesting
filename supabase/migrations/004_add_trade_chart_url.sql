-- Adds chart_url: a publicly-fetchable URL (Supabase Storage, "trade-charts"
-- bucket) for the candlestick screenshot render_trade_chart() saves for each
-- closed trade -- unlike chart_path (the LOCAL filesystem path on the
-- trading machine, which the dashboard can never fetch), this is what lets
-- the dashboard actually display the chart image. Nullable -- older trade
-- rows, and any trade where chart rendering or the Storage upload failed,
-- simply have no chart to show.
alter table public.trades add column if not exists chart_url text;
