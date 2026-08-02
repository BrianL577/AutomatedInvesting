"use client";

import { useEffect, useState } from "react";

// Real-time account balance straight from the broker (TopstepX) — the
// ground truth for "how much money is actually in the account right now",
// independent of anything reconstructed from the locally-logged trade
// history (which can drift, e.g. a connectivity test trade whose outcome
// wasn't captured). Reuses the same Bot API URL the Test Trade panel
// stores in localStorage, since it's the same always-on host.
type BalanceInfo = { name: string; balance: number };

const STORAGE_KEY = "jj-bot-api-url";
const REFRESH_MS = 30_000;

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function LiveBalancePanel() {
  const [apiUrl, setApiUrl] = useState("");
  const [balances, setBalances] = useState<BalanceInfo[] | null>(null);
  const [broker, setBroker] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error" | "success">("idle");
  const [message, setMessage] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    setApiUrl(window.localStorage.getItem(STORAGE_KEY) || "");
  }, []);

  useEffect(() => {
    if (!apiUrl) return;
    let cancelled = false;

    async function load() {
      setStatus("loading");
      try {
        const res = await fetch(`${apiUrl.replace(/\/$/, "")}/api/account-balances`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to load balances");
        if (cancelled) return;
        setBroker(data.broker || "");
        setBalances(data.accounts || []);
        setLastUpdated(new Date());
        setStatus("success");
        setMessage("");
      } catch (err: any) {
        if (cancelled) return;
        setStatus("error");
        setMessage(err.message || "Could not reach the bot API.");
      }
    }

    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [apiUrl]);

  if (!apiUrl) {
    return (
      <div className="live-balance-panel">
        <div className="live-balance-header">
          <h2>Live TopStep Balance</h2>
        </div>
        <p className="live-balance-empty">
          Set the Bot API URL in the Test Trade panel above to show your real account balance here, straight from
          TopStep — the ground truth, independent of anything this dashboard reconstructs from logged trades.
        </p>
      </div>
    );
  }

  return (
    <div className="live-balance-panel">
      <div className="live-balance-header">
        <h2>Live TopStep Balance</h2>
        {lastUpdated && (
          <span className="live-balance-updated">
            Updated {lastUpdated.toLocaleTimeString()} · refreshes every 30s
          </span>
        )}
      </div>
      {status === "error" && <div className="test-status test-status-error">{message}</div>}
      {broker && broker !== "topstepx" && (
        <p className="live-balance-empty">
          Broker is &quot;{broker}&quot; — live balance only applies to TopstepX (real money, no sandbox). Paper/demo
          account balances aren&apos;t the point here.
        </p>
      )}
      {balances && balances.length > 0 && (
        <div className="live-balance-grid">
          {balances.map((b) => (
            <div className="stat-card" key={b.name}>
              <div className="label">{b.name}</div>
              <div className="value">{fmtMoney(b.balance)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
