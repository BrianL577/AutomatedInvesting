"use client";

import { useEffect, useRef, useState } from "react";
import { createClient } from "../../lib/supabase/client";
import { BOT_API_HEADERS } from "../../lib/botApiHeaders";

// Single-page operational view of the always-on bot: connection health,
// which broker/accounts it's actually trading, real live balances, which
// saved strategy is active, and a live-filterable tail of its own log
// file — built to replace the SSH-in-and-grep-bot_log.txt cycle that was
// the default way to answer "what's it doing right now" for an entire
// day of real troubleshooting.
const STORAGE_KEY = "jj-bot-api-url";
const REFRESH_MS = 10_000;

type AccountsInfo = { broker: string; env: string; accounts: { name: string; active: boolean }[] };
type BalanceInfo = { name: string; balance: number };
type LogTailResult = { path: string; line_count: number; lines: string[] };
type ActiveStrategy = { name: string; source: string; created_at: string } | null;

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function DebugPage() {
  const [apiUrl, setApiUrl] = useState("");
  const [urlInput, setUrlInput] = useState("");
  const [health, setHealth] = useState<"unknown" | "ok" | "error">("unknown");
  const [accountsInfo, setAccountsInfo] = useState<AccountsInfo | null>(null);
  const [balances, setBalances] = useState<BalanceInfo[] | null>(null);
  const [activeStrategy, setActiveStrategy] = useState<ActiveStrategy>(null);
  const [strategyError, setStrategyError] = useState("");
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logError, setLogError] = useState("");
  const [filter, setFilter] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const logBoxRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY) || "";
    setApiUrl(stored);
    setUrlInput(stored);
  }, []);

  function saveUrl() {
    window.localStorage.setItem(STORAGE_KEY, urlInput.trim());
    setApiUrl(urlInput.trim());
  }

  // Bot API-backed data: health, accounts, balances, log tail.
  useEffect(() => {
    if (!apiUrl) return;
    let cancelled = false;
    const base = apiUrl.replace(/\/$/, "");

    async function load() {
      try {
        const res = await fetch(`${base}/api/health`, { headers: BOT_API_HEADERS });
        if (cancelled) return;
        setHealth(res.ok ? "ok" : "error");
      } catch {
        if (!cancelled) setHealth("error");
      }

      try {
        const res = await fetch(`${base}/api/accounts`, { headers: BOT_API_HEADERS });
        const data = await res.json();
        if (!cancelled && res.ok) setAccountsInfo(data);
      } catch {
        /* health check above already surfaces connectivity failures */
      }

      try {
        const res = await fetch(`${base}/api/account-balances`, { headers: BOT_API_HEADERS });
        const data = await res.json();
        if (!cancelled && res.ok) setBalances(data.accounts || []);
      } catch {
        /* non-fatal — balances just won't show */
      }

      try {
        const params = new URLSearchParams({ lines: "150" });
        if (filter.trim()) params.set("pattern", filter.trim());
        const res = await fetch(`${base}/api/log-tail?${params.toString()}`, { headers: BOT_API_HEADERS });
        const data = await res.json();
        if (cancelled) return;
        if (!res.ok) {
          setLogError(data.detail || "Failed to load log tail.");
          setLogLines([]);
        } else {
          const result = data as LogTailResult;
          setLogError("");
          setLogLines(result.lines);
        }
      } catch (err: any) {
        if (!cancelled) {
          setLogError(err.message || "Could not reach the bot API for the log tail.");
          setLogLines([]);
        }
      }

      if (!cancelled) setLastUpdated(new Date());
    }

    load();
    const interval = autoRefresh ? setInterval(load, REFRESH_MS) : undefined;
    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl, filter, autoRefresh]);

  // Auto-scroll the log view to the newest line on every refresh.
  useEffect(() => {
    logBoxRef.current?.scrollTo({ top: logBoxRef.current.scrollHeight });
  }, [logLines]);

  // Active strategy: straight from Supabase (client-side, same RLS-scoped
  // access the Strategy Creator page already uses) — the bot API has no
  // endpoint for this since it's a dashboard-side concept, not a broker one.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const supabase = createClient();
        const { data, error } = await supabase
          .from("strategies")
          .select("config, source, created_at")
          .eq("is_active", true)
          .limit(1)
          .maybeSingle();
        if (cancelled) return;
        if (error) {
          setStrategyError(error.message);
          return;
        }
        setStrategyError("");
        setActiveStrategy(
          data ? { name: data.config?.name || "(unnamed)", source: data.source, created_at: data.created_at } : null
        );
      } catch (err: any) {
        if (!cancelled) setStrategyError(err.message || "Could not reach Supabase.");
      }
    }
    load();
    const interval = setInterval(load, REFRESH_MS * 3);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="container">
      <div className="header">
        <div>
          <h1>Debug Panel</h1>
          <p>
            Live status straight from the always-on bot — no PowerShell required. <a href="/">← Dashboard</a>
          </p>
        </div>
        {lastUpdated && (
          <span className="data-source-badge live">Updated {lastUpdated.toLocaleTimeString()}</span>
        )}
      </div>

      <div className="eval-sim-panel">
        <div className="eval-sim-header">
          <h2>Bot API URL</h2>
          <p>Same URL/localStorage key as the Test Trade and Live Balance panels on the main dashboard.</p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            type="text"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="http://your-bot-host:8000"
            style={{ flex: 1 }}
          />
          <button onClick={saveUrl}>Save</button>
        </div>
      </div>

      {!apiUrl ? (
        <p className="live-balance-empty">Set the Bot API URL above to see live status.</p>
      ) : (
        <>
          <div className="stat-grid">
            <div className="stat-card">
              <div className="label">Bot API</div>
              <div className={`value ${health === "ok" ? "positive" : health === "error" ? "negative" : ""}`}>
                {health === "ok" ? "● Reachable" : health === "error" ? "○ Unreachable" : "…"}
              </div>
            </div>
            <div className="stat-card">
              <div className="label">Broker / Env</div>
              <div className="value">{accountsInfo ? `${accountsInfo.broker} (${accountsInfo.env})` : "—"}</div>
            </div>
            <div className="stat-card">
              <div className="label">Active Strategy</div>
              <div className="value">{activeStrategy ? activeStrategy.name : "JJ Default"}</div>
            </div>
          </div>
          {strategyError && <div className="test-status test-status-error">Active strategy lookup: {strategyError}</div>}

          {accountsInfo && accountsInfo.accounts.length > 0 && (
            <div className="eval-sim-panel">
              <div className="eval-sim-header">
                <h2>Trading Accounts</h2>
              </div>
              <div className="live-balance-grid">
                {accountsInfo.accounts.map((a) => {
                  const bal = balances?.find((b) => b.name === a.name);
                  return (
                    <div className="stat-card" key={a.name}>
                      <div className="label">{a.name}</div>
                      <div className="value">{bal ? fmtMoney(bal.balance) : "—"}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="eval-sim-panel">
            <div className="eval-sim-header">
              <h2>Live Log</h2>
              <p>Tail of the bot's own log file, refreshing every {REFRESH_MS / 1000}s. Filter uses a regex, same idea as the Select-String filtering done by hand.</p>
            </div>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.75rem" }}>
              <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter, e.g. SIGNAL|Order placed|hub disconnected"
                style={{ flex: 1 }}
              />
              <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", whiteSpace: "nowrap" }}>
                <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
                Auto-refresh
              </label>
            </div>
            {logError && <div className="test-status test-status-error">{logError}</div>}
            <pre
              ref={logBoxRef}
              style={{
                maxHeight: "480px",
                overflowY: "auto",
                fontSize: "0.8rem",
                lineHeight: 1.4,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {logLines.length > 0 ? logLines.join("\n") : "No matching log lines yet."}
            </pre>
          </div>
        </>
      )}
    </div>
  );
}
