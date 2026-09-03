import { loadTrades, computeStats, simulateAccounts, usingSupabase, RATE_LIMITS, EVAL_SIM } from "../lib/trades";
import TestTradePanel from "../components/TestTradePanel";
import MarketChart from "../components/MarketChart";
import GainLossChart from "../components/GainLossChart";
import LiveBalancePanel from "../components/LiveBalancePanel";
import EvalSimTable from "../components/EvalSimTable";
import TradeTable from "../components/TradeTable";
import Reveal from "../components/Reveal";

export const dynamic = "force-dynamic";

export default async function Page() {
  const trades = await loadTrades();
  const stats = computeStats(trades);
  const accountSims = simulateAccounts(trades);
  const totalGain = accountSims.reduce((s, a) => s + a.cashPayouts, 0);
  const totalLoss = accountSims.reduce((s, a) => s + a.feesPaid, 0);

  return (
    <div className="container">
      <div className="header">
        <div>
          <h1>JJ Strategy — Paper Trading Dashboard</h1>
          <p>
            NY-session displacement / break-of-structure strategy, running against a TopStep/Tradovate paper
            (demo) account. <a href="/strategies">Strategy Creator →</a> · <a href="/debug">Debug Panel →</a>
          </p>
        </div>
        <span className={`data-source-badge ${usingSupabase ? "live" : "static"}`}>
          {usingSupabase ? "● Live (Supabase)" : "○ Static demo file"}
        </span>
      </div>

      <Reveal>
        <TestTradePanel />
      </Reveal>

      <Reveal delayMs={60}>
        <LiveBalancePanel />
      </Reveal>

      <Reveal delayMs={60}>
        <MarketChart />
      </Reveal>

      <Reveal delayMs={60}>
        <div className="rate-limit-banner">
          <span>Daily rate limiter:</span>
          <span className={`pill profit-cap ${stats.hitProfitCap ? "hit" : ""}`}>
            Max gain cap ${RATE_LIMITS.PROFIT_CAP.toLocaleString()} {stats.hitProfitCap ? "— hit" : ""}
          </span>
          <span className={`pill loss-cap ${stats.hitLossCap ? "hit" : ""}`}>
            Max loss cap ${RATE_LIMITS.LOSS_CAP.toLocaleString()} {stats.hitLossCap ? "— hit" : ""}
          </span>
          <span>Trading stops for the day once either cap is touched.</span>
        </div>
      </Reveal>

      <Reveal delayMs={100}>
        <div className="stat-grid">
          <div className="stat-card">
            <div className="label">Success Rate</div>
            <div className={`value ${stats.successRate >= 50 ? "positive" : "negative"}`}>
              {stats.successRate.toFixed(1)}%
            </div>
          </div>
          <div className="stat-card">
            <div className="label">Total Trades</div>
            <div className="value">{stats.totalTrades}</div>
          </div>
          <div className="stat-card">
            <div className="label">Wins / Losses</div>
            <div className="value">
              <span className="positive">{stats.wins}</span> / <span className="negative">{stats.losses}</span>
            </div>
          </div>
        </div>
      </Reveal>

      <Reveal delayMs={60}>
        <div className="eval-sim-panel">
          <div className="eval-sim-header">
            <h2>Eval &amp; Funded Account Simulator</h2>
            <p>
              Same economics model as the Strategy Creator&apos;s backtests: each account starts in{" "}
              <strong>Eval</strong>, needs +${EVAL_SIM.EVAL_TARGET.toLocaleString()} to pass — but a big single day
              raises that target (target = max(${EVAL_SIM.EVAL_TARGET.toLocaleString()}, best day ÷ 0.5)). Busting is
              a <strong>trailing</strong> ${EVAL_SIM.TRAILING_DRAWDOWN.toLocaleString()} drawdown from your peak
              balance, capped at breakeven — not a flat floor. Passing moves you to <strong>Funded</strong>; payouts
              need 5 winning days of ${EVAL_SIM.MIN_WINNING_DAY_PROFIT}+ (or a faster consistency path), capped at $
              {EVAL_SIM.MAX_PAYOUT.toLocaleString()} at {(EVAL_SIM.PAYOUT_SHARE * 100).toFixed(0)}% your share — and
              the drawdown buffer resets to $0 right after each payout. Busting a funded account costs a fresh eval
              (${EVAL_SIM.EVAL_COST}, flat — covers the real per-attempt + accrued monthly fees).
            </p>
          </div>
          <EvalSimTable accountSims={accountSims} />
        </div>
      </Reveal>

      <Reveal delayMs={60}>
        <GainLossChart gain={totalGain} loss={totalLoss} />
      </Reveal>

      <Reveal delayMs={100}>
        {trades.length === 0 ? (
          <div className="empty-state">
            No trades logged yet. Run the backtester or live paper-trading bot to populate
            <code> dashboard/data/trades.json</code>.
          </div>
        ) : (
          <TradeTable trades={trades} sourceBadges />
        )}
      </Reveal>
    </div>
  );
}
