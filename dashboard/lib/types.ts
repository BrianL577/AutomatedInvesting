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
};
