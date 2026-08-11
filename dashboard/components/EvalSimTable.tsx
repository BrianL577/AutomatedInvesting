"use client";

import { useEffect, useState } from "react";
import type { AccountSim } from "../lib/trades";
import { EVAL_SIM } from "../lib/evalSimConfig";
import { BOT_API_HEADERS } from "../lib/botApiHeaders";

// Stage Balance shown here is REAL, live, straight from TopStep's own
// Account/search API (same source as LiveBalancePanel) whenever it's
// reachable — not summed from locally-logged trades, which can drift (a
// missed/duplicate-logged trade, an excluded connectivity-test trade that
// still moved real money, etc.). Falls back to the locally-simulated
// number, clearly labeled, if the bot API isn't configured/reachable.
//
// Bust Line, Evals Purchased, and everything else on this table stays
// simulated — TopStep has no API for trailing-drawdown floor, eval-target
// escalation, or attempt/payout history (confirmed: their public API only
// covers Account/Market Data/Orders/Positions/Trades, nothing eval-specific).
// Only the balance itself can be sourced as ground truth.
type BalanceInfo = { name: string; balance: number };

const STORAGE_KEY = "jj-bot-api-url";
const REFRESH_MS = 30_000;

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function EvalSimTable({ accountSims }: { accountSims: AccountSim[] }) {
  const [apiUrl, setApiUrl] = useState("");
  const [liveBalances, setLiveBalances] = useState<Map<string, number> | null>(null);
  const [liveError, setLiveError] = useState("");

  useEffect(() => {
    setApiUrl(window.localStorage.getItem(STORAGE_KEY) || "");
  }, []);

  useEffect(() => {
    if (!apiUrl) return;
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch(`${apiUrl.replace(/\/$/, "")}/api/account-balances`, {
          headers: BOT_API_HEADERS,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to load live balances");
        if (cancelled) return;
        const accounts = (data.accounts || []) as BalanceInfo[];
        setLiveBalances(new Map(accounts.map((a) => [a.name, a.balance])));
        setLiveError("");
      } catch (err: any) {
        if (cancelled) return;
        setLiveError(err.message || "Could not reach the bot API.");
      }
    }

    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [apiUrl]);

  if (accountSims.length === 0) {
    return <div className="empty-state">No account trades yet — the simulation fills in once trades are logged.</div>;
  }

  return (
    <div className="table-wrap">
      {apiUrl && liveError && <div className="test-status test-status-error">{liveError}</div>}
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
          {accountSims.map((sim) => {
            const liveRaw = liveBalances?.get(sim.account);
            const isLive = liveRaw !== undefined;
            const balance = isLive ? liveRaw! - EVAL_SIM.ACCOUNT_SIZE : sim.balance;
            return (
              <tr key={sim.account}>
                <td>{sim.account}</td>
                <td>
                  <span className={`badge ${sim.stage === "funded" ? "win" : "test"}`}>
                    {sim.stage === "funded" ? "Funded" : "Eval"}
                  </span>
                </td>
                <td className={balance >= 0 ? "positive" : "negative"}>
                  {fmtMoney(balance)}
                  {sim.stage === "eval" && (
                    <span className="text-dim"> / ${sim.effectiveProfitTarget.toLocaleString()}</span>
                  )}
                  {isLive ? (
                    <span className="text-dim" title="Real balance from TopStep's API, not a local simulation">
                      {" "}
                      ● live
                    </span>
                  ) : (
                    <span className="text-dim" title="Simulated from locally-logged trades — set the Bot API URL above for the real number">
                      {" "}
                      (sim)
                    </span>
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
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
