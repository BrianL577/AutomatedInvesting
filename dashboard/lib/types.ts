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
  // jj_bot/trade_chart.py. Not a URL; the dashboard can't render it
  // directly yet since these files never leave the trading machine.
  chart_path?: string | null;
};
