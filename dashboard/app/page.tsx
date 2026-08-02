import { loadTrades, computeStats, simulateAccounts, usingSupabase, RATE_LIMITS, EVAL_SIM } from "../lib/trades";
import TestTradePanel from "../components/TestTradePanel";
import MarketChart from "../components/MarketChart";

export const dynamic = "force-dynamic";

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "America/New_York",
  });
}

export default async function Page() {
  const trades = await loadTrades();
  const stats = computeStats(trades);
  const accountSims = simulateAccounts(trades);

  return (
    <div className="container">
      <div className="header">
        <div>
          <h1>JJ Strategy — Paper Trading Dashboard</h1>
          <p>
            NY-session displacement / break-of-structure strategy, running against a TopStep/Tradovate paper
            (demo) account. <a href="/strategies">Strategy Creator →</a>
          </p>
        </div>
        <span className={`data-source-badge ${usingSupabase ? "live" : "static"}`}>
          {usingSupabase ? "● Live (Supabase)" : "○ Static demo file"}
        </span>
      </div>

      <TestTradePanel />

      <MarketChart />

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
        {accountSims.length === 0 ? (
          <div className="empty-state">No account trades yet — the simulation fills in once trades are logged.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Stage</th>
                  <th>Stage Balance</th>
                  <th>Bust Line</th>
                  <th>Evals Purchased</th>
                  <th>Times Funded</th>
                  <th>Funded Losses</th>
                  <th>Payouts</th>
                  <th>Fees Paid</th>
                  <th>Bottom Line</th>
                </tr>
              </thead>
              <tbody>
                {accountSims.map((sim) => (
                  <tr key={sim.account}>
                    <td>{sim.account}</td>
                    <td>
                      <span className={`badge ${sim.stage === "funded" ? "win" : "test"}`}>
                        {sim.stage === "funded" ? "Funded" : "Eval"}
                      </span>
                    </td>
                    <td className={sim.balance >= 0 ? "positive" : "negative"}>
                      {fmtMoney(sim.balance)}
                      {sim.stage === "eval" && (
                        <span className="text-dim"> / ${sim.effectiveProfitTarget.toLocaleString()}</span>
                      )}
                    </td>
                    <td className="negative">{fmtMoney(sim.floor)}</td>
                    <td>{sim.evalsPurchased}</td>
                    <td>{sim.fundedPasses}</td>
                    <td>{sim.fundedLosses}</td>
                    <td>
                      {sim.payoutsReceived} <span className="text-dim">({fmtMoney(sim.cashPayouts)})</span>
                    </td>
                    <td className="negative">{fmtMoney(-sim.feesPaid)}</td>
                    <td className={sim.netResult >= 0 ? "positive" : "negative"}>{fmtMoney(sim.netResult)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="table-wrap">
        {trades.length === 0 ? (
          <div className="empty-state">
            No trades logged yet. Run the backtester or live paper-trading bot to populate
            <code> dashboard/data/trades.json</code>.
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Entry Time (ET)</th>
                <th>Account</th>
                <th>Phase</th>
                <th>Direction</th>
                <th>Grade</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Result</th>
                <th>P&amp;L (pts)</th>
                <th>P&amp;L ($)</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {[...trades].reverse().map((t) => {
                const isTest = t.source === "connection_test" || t.phase === "test";
                return (
                  <tr key={t.id}>
                    <td>{fmtTime(t.timestamp)}</td>
                    <td>{t.account_name || "—"}</td>
                    <td>{t.phase}</td>
                    <td>
                      <span className={`badge ${t.direction}`}>{t.direction}</span>
                    </td>
                    <td>{t.grade}</td>
                    <td>{t.entry_price.toFixed(2)}</td>
                    <td>{t.exit_price.toFixed(2)}</td>
                    <td>
                      {isTest ? (
                        <span className="badge test">Test</span>
                      ) : (
                        <span className={`badge ${t.win ? "win" : "loss"}`}>{t.win ? "Win" : "Loss"}</span>
                      )}
                    </td>
                    <td className={t.pnl_points >= 0 ? "positive" : "negative"}>{t.pnl_points.toFixed(2)}</td>
                    <td className={t.pnl_dollars >= 0 ? "positive" : "negative"}>{fmtMoney(t.pnl_dollars)}</td>
                    <td className="reason-cell" title={t.reason}>
                      {t.reason}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
