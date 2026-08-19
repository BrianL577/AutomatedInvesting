-- Adds chart_path: the local filesystem path to the candlestick screenshot
-- render_trade_chart() saves for each closed trade (see
-- jj_bot/trade_chart.py, jj_bot/trade_logger.py). Nullable — older trade
-- rows, and any trade where chart rendering failed, simply have no chart.
alter table public.trades add column if not exists chart_path text;
