import { loadTrades } from "../../lib/trades";
import Reveal from "../../components/Reveal";
import TradeTable from "../../components/TradeTable";
import type { Trade } from "../../lib/types";

export const dynamic = "force-dynamic";

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

type AccountSummary = {
  account: string;
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  netDollars: number;
};

function summarizeByAccount(trades: Trade[]): AccountSummary[] {
  const byAccount = new Map<string, Trade[]>();
  for (const t of trades) {
    const key = t.account_name || "(unknown)";
    if (!byAccount.has(key)) byAccount.set(key, []);
    byAccount.get(key)!.push(t);
  }
  return [...byAccount.entries()]
    .map(([account, accountTrades]) => {
      const wins = accountTrades.filter((t) => t.win).length;
      const losses = accountTrades.length - wins;
      const netDollars = accountTrades.reduce((sum, t) => sum + t.pnl_dollars, 0);
      return {
        account,
        trades: accountTrades.length,
        wins,
        losses,
        winRate: accountTrades.length ? (wins / accountTrades.length) * 100 : 0,
        netDollars,
      };
    })
    .sort((a, b) => a.account.localeCompare(b.account));
}

// Virtual-practice trades (scripts/run_virtual_practice.py — abstract
// accounts, no real order behind them) live on this dedicated page so they
// never dilute the real trade log on "/", the same reason they're excluded
// from computeStats()/simulateAccounts() there (see dashboard/lib/trades.ts).
export default async function VirtualPracticePage() {
  const allTrades = await loadTrades();
  const trades = allTrades.filter((t) => t.source === "virtual_practice");
  const summaries = summarizeByAccount(trades);

  const totalTrades = trades.length;
  const totalWins = trades.filter((t) => t.win).length;
  const totalLosses = totalTrades - totalWins;
  const winRate = totalTrades ? (totalWins / totalTrades) * 100 : 0;
  const netDollars = trades.reduce((sum, t) => sum + t.pnl_dollars, 0);

  return (
    <div className="container">
      <div className="header">
        <div>
          <h1>Virtual Practice Accounts</h1>
          <p>
            Abstract &quot;Virtual-01&quot;..&quot;Virtual-N&quot; accounts, each taking its own distinct setup from
            the NY-session strategy against live TopstepX market data — no real order is ever placed. One trade
            per account per day, same rule as real trading. Not counted toward the real P&amp;L/win-rate stats or
            the Eval Simulator on the main dashboard.
          </p>
        </div>
      </div>

      <Reveal>
        <div className="stat-grid">
          <div className="stat-card">
            <div className="label">Total Virtual Trades</div>
            <div className="value">{totalTrades}</div>
          </div>
          <div className="stat-card">
            <div className="label">Win Rate</div>
            <div className={`value ${winRate >= 50 ? "positive" : "negative"}`}>{winRate.toFixed(1)}%</div>
          </div>
          <div className="stat-card">
            <div className="label">Wins / Losses</div>
            <div className="value">
              <span className="positive">{totalWins}</span> / <span className="negative">{totalLosses}</span>
            </div>
          </div>
          <div className="stat-card">
            <div className="label">Net (virtual $)</div>
            <div className={`value ${netDollars >= 0 ? "positive" : "negative"}`}>{fmtMoney(netDollars)}</div>
          </div>
        </div>
      </Reveal>

      <Reveal delayMs={60}>
        <div className="table-wrap">
          {summaries.length === 0 ? (
            <div className="empty-state">
              No virtual practice trades yet. Run <code>python scripts/run_virtual_practice.py</code> during a live
              NY session to populate this page.
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Trades</th>
                  <th>Wins</th>
                  <th>Losses</th>
                  <th>Win Rate</th>
                  <th>Net ($)</th>
                </tr>
              </thead>
              <tbody>
                {summaries.map((s) => (
                  <tr key={s.account}>
                    <td>{s.account}</td>
                    <td>{s.trades}</td>
                    <td className="positive">{s.wins}</td>
                    <td className="negative">{s.losses}</td>
                    <td className={s.winRate >= 50 ? "positive" : "negative"}>{s.winRate.toFixed(1)}%</td>
                    <td className={s.netDollars >= 0 ? "positive" : "negative"}>{fmtMoney(s.netDollars)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Reveal>

      <Reveal delayMs={100}>
        {trades.length === 0 ? (
          <div className="empty-state">No virtual practice trades logged yet.</div>
        ) : (
          <TradeTable trades={trades} />
        )}
      </Reveal>
    </div>
  );
}
