"use client";

import { useEffect, useState } from "react";

type AccountInfo = { name: string; id: number; active: boolean };

const STORAGE_KEY = "jj-bot-api-url";

export default function TestTradePanel() {
  const [apiUrl, setApiUrl] = useState("");
  const [savedApiUrl, setSavedApiUrl] = useState("");
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [selectedAccount, setSelectedAccount] = useState("");
  const [direction, setDirection] = useState<"Buy" | "Sell">("Buy");
  const [status, setStatus] = useState<"idle" | "loading" | "error" | "success">("idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY) || "";
    setApiUrl(stored);
    setSavedApiUrl(stored);
  }, []);

  function saveApiUrl() {
    window.localStorage.setItem(STORAGE_KEY, apiUrl.trim());
    setSavedApiUrl(apiUrl.trim());
    setAccounts([]);
    setStatus("idle");
    setMessage("");
  }

  async function loadAccounts() {
    if (!savedApiUrl) return;
    setStatus("loading");
    setMessage("Connecting...");
    try {
      const res = await fetch(`${savedApiUrl.replace(/\/$/, "")}/api/accounts`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to load accounts");
      setAccounts(data.accounts || []);
      if (data.accounts?.length) setSelectedAccount(data.accounts[0].name);
      setStatus("success");
      setMessage(`Connected. Found ${data.accounts?.length ?? 0} account(s) on ${data.env}.`);
    } catch (err: any) {
      setStatus("error");
      setMessage(err.message || "Could not reach the bot API.");
    }
  }

  async function sendTestTrade() {
    if (!savedApiUrl || !selectedAccount) return;
    setStatus("loading");
    setMessage("Submitting test trade...");
    try {
      const res = await fetch(`${savedApiUrl.replace(/\/$/, "")}/api/test-trade`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_name: selectedAccount, direction }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Test trade failed");
      setStatus("success");
      setMessage(
        `Test trade submitted on ${data.tested_account} (${data.contract_symbol}). Check your Tradovate demo account to confirm the fill. Refresh this page to see it logged below.`
      );
    } catch (err: any) {
      setStatus("error");
      setMessage(err.message || "Test trade failed.");
    }
  }

  return (
    <div className="test-panel">
      <div className="test-panel-header">
        <h2>Connection &amp; Automation Test</h2>
        <p>
          Point this at your running bot API (<code>python scripts/run_api_server.py</code>), confirm it
          sees your account(s), then fire a small test order to prove the automation actually reaches
          your paper account before trusting live trading.
        </p>
      </div>

      <div className="test-panel-row">
        <input
          className="test-input"
          placeholder="Bot API URL, e.g. https://your-bot-host:8787"
          value={apiUrl}
          onChange={(e) => setApiUrl(e.target.value)}
        />
        <button className="btn" onClick={saveApiUrl}>
          Save
        </button>
        <button className="btn" onClick={loadAccounts} disabled={!savedApiUrl}>
          Load Accounts
        </button>
      </div>

      {accounts.length > 0 && (
        <div className="test-panel-row">
          <select className="test-select" value={selectedAccount} onChange={(e) => setSelectedAccount(e.target.value)}>
            {accounts.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name} {a.active ? "" : "(inactive)"}
              </option>
            ))}
          </select>
          <select className="test-select" value={direction} onChange={(e) => setDirection(e.target.value as "Buy" | "Sell")}>
            <option value="Buy">Buy (long)</option>
            <option value="Sell">Sell (short)</option>
          </select>
          <button className="btn btn-primary" onClick={sendTestTrade} disabled={status === "loading"}>
            Send Test Trade
          </button>
        </div>
      )}

      {message && <div className={`test-status test-status-${status}`}>{message}</div>}
    </div>
  );
}
