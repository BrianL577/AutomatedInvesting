import { loadTrades, computeStats, RATE_LIMITS } from "../lib/trades";

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

  return (
    <div className="container">
      <div className="header">
        <div>
          <h1>JJ Strategy — Paper Trading Dashboard</h1>
          <p>NY-session displacement / break-of-structure strategy, running against a TopStep/Tradovate paper (demo) account.</p>
        </div>
      </div>

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
        <div className="stat-card">
          <div className="label">Total Gained</div>
          <div className="value positive">{fmtMoney(stats.totalGained)}</div>
        </div>
        <div className="stat-card">
          <div className="label">Total Lost</div>
          <div className="value negative">{fmtMoney(-stats.totalLost)}</div>
        </div>
        <div className="stat-card">
          <div className="label">Net P&amp;L</div>
          <div className={`value ${stats.netPnl >= 0 ? "positive" : "negative"}`}>{fmtMoney(stats.netPnl)}</div>
        </div>
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
              {[...trades].reverse().map((t) => (
                <tr key={t.id}>
                  <td>{fmtTime(t.timestamp)}</td>
                  <td>{t.phase}</td>
                  <td>
                    <span className={`badge ${t.direction}`}>{t.direction}</span>
                  </td>
                  <td>{t.grade}</td>
                  <td>{t.entry_price.toFixed(2)}</td>
                  <td>{t.exit_price.toFixed(2)}</td>
                  <td>
                    <span className={`badge ${t.win ? "win" : "loss"}`}>{t.win ? "Win" : "Loss"}</span>
                  </td>
                  <td className={t.pnl_points >= 0 ? "positive" : "negative"}>{t.pnl_points.toFixed(2)}</td>
                  <td className={t.pnl_dollars >= 0 ? "positive" : "negative"}>{fmtMoney(t.pnl_dollars)}</td>
                  <td className="reason-cell" title={t.reason}>
                    {t.reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
