export type Trade = {
  id: number | string;
  timestamp: string;
  exit_timestamp: string;
  phase: "continuation" | "reversion" | "test" | "waiting_for_open" | "done_for_day";
  direction: "long" | "short";
  grade: string;
  reason: string;
  entry_price: number;
  exit_price: number;
  stop_price: number;
  target_price: number;
  win: boolean;
  pnl_points: number;
  pnl_dollars: number;
  source: string;
  account_name?: string | null;
  logged_at: string;
  // Local filesystem path (on the machine running the bot) to the
  // candlestick screenshot saved for this trade — see
  // jj_bot/trade_chart.py. Not a URL; kept only as a debugging breadcrumb
  // since these files never leave the trading machine. Use chart_url below
  // to actually display the chart.
  chart_path?: string | null;
  // Publicly-fetchable URL (Supabase Storage "trade-charts" bucket) for the
  // same chart — this IS renderable by the dashboard. Null for trades from
  // before this was wired up, or when the Storage upload failed (chart
  // rendering/upload failures never block logging the trade itself).
  chart_url?: string | null;
  // Why chart_url is null when a chart WAS expected -- see
  // jj_bot/trade_logger.py's _upload_chart. Debugging aid only, not shown
  // in the normal UI.
  chart_upload_error?: string | null;
};
