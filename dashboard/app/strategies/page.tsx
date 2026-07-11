"use client";

import { useEffect, useState } from "react";
import type { StrategyConfig, SavedStrategy } from "../../lib/strategySchema";
import type { BacktestResult } from "../../lib/backtester";

type BacktestResponse = BacktestResult & { dataSource: "supabase" | "sample"; error?: string };

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<SavedStrategy[]>([]);
  const [selected, setSelected] = useState<SavedStrategy | null>(null);
  const [draft, setDraft] = useState<StrategyConfig | null>(null);
  const [draftPrompt, setDraftPrompt] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState<"" | "generating" | "backtesting" | "saving">("");
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [resultFor, setResultFor] = useState("");

  async function refresh() {
    const res = await fetch("/api/strategies");
    const data = await res.json();
    setStrategies(data.strategies ?? []);
    if (!selected && data.strategies?.length) setSelected(data.strategies[0]);
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeConfig: StrategyConfig | null = draft ?? selected?.config ?? null;
  const activeName = draft ? `${draft.name} (unsaved)` : selected?.config.name ?? "";

  async function generate() {
    setBusy("generating");
    setMessage(null);
    try {
      const res = await fetch("/api/generate-strategy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Generation failed");
      setDraft(data.config);
      setDraftPrompt(prompt);
      setResult(null);
      setMessage({ kind: "ok", text: "Strategy generated. Review the rules below, then backtest or save it." });
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy("");
    }
  }

  async function backtest() {
    if (!activeConfig) return;
    setBusy("backtesting");
    setMessage(null);
    try {
      const res = await fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: activeConfig }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Backtest failed");
      setResult(data);
      setResultFor(activeName);
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy("");
    }
  }

  async function removeStrategy(id: string) {
    setBusy("saving");
    setMessage(null);
    try {
      const res = await fetch(`/api/strategies?id=${encodeURIComponent(id)}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Delete failed");
      if (selected?.id === id) setSelected(null);
      await refresh();
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy("");
    }
  }

  async function save() {
    if (!draft) return;
    setBusy("saving");
    setMessage(null);
    try {
      const res = await fetch("/api/strategies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: draft, source: "ai", prompt: draftPrompt }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Save failed");
      setMessage({ kind: "ok", text: "Strategy saved." });
      setDraft(null);
      await refresh();
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="container">
      <div className="header">
        <div>
          <h1>Strategy Creator</h1>
          <p>
            Describe a NASDAQ futures strategy in plain English, let AI turn it into testable rules, and
            simulate its yields against historical data. <a href="/">← Back to dashboard</a>
          </p>
        </div>
      </div>

      <div className="test-panel">
        <div className="test-panel-header">
          <h2>Create a strategy with AI</h2>
          <p>
            Your description is translated by Claude into parameters for the same rule engine that runs JJ&apos;s
            default strategy (session anchor, displacement candles, break of structure, fixed R:R brackets, daily
            caps). AI output is data, not code — every config is validated before it runs.
          </p>
        </div>
        <div className="test-panel-row">
          <textarea
            className="test-input strategy-prompt"
            rows={3}
            maxLength={4000}
            placeholder='e.g. "Trade only mean reversion between 30 and 90 minutes after the open, with a tight 15 point stop and 45 point target, max 2 trades a day, stop after 1 loss."'
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>
        <div className="test-panel-row">
          <button className="btn btn-primary" onClick={generate} disabled={busy !== "" || prompt.trim().length < 10}>
            {busy === "generating" ? "Generating…" : "Generate Strategy"}
          </button>
          {draft && (
            <>
              <button className="btn" onClick={save} disabled={busy !== ""}>
                {busy === "saving" ? "Saving…" : "Save Strategy"}
              </button>
              <button className="btn" onClick={() => { setDraft(null); setMessage(null); }} disabled={busy !== ""}>
                Discard Draft
              </button>
            </>
          )}
        </div>
        {message && <div className={`test-status test-status-${message.kind === "error" ? "error" : "success"}`}>{message.text}</div>}
      </div>

      <div className="strategy-layout">
        <div className="strategy-list">
          <h2>Strategies</h2>
          {strategies.map((s) => (
            <div
              key={s.id}
              role="button"
              tabIndex={0}
              className={`strategy-item ${!draft && selected?.id === s.id ? "active" : ""}`}
              onClick={() => {
                setSelected(s);
                setDraft(null);
                setResult(null);
                setMessage(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  setSelected(s);
                  setDraft(null);
                  setResult(null);
                  setMessage(null);
                }
              }}
            >
              <span className="strategy-item-name">{s.config.name}</span>
              <span className={`badge ${s.source === "default" ? "test" : s.source === "ai" ? "long" : "short"}`}>
                {s.source}
              </span>
              {s.source !== "default" && (
                <button
                  className="strategy-item-delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeStrategy(s.id);
                  }}
                  title="Delete strategy"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
          {draft && (
            <div className="strategy-item active">
              <span className="strategy-item-name">{draft.name}</span>
              <span className="badge win">draft</span>
            </div>
          )}
        </div>

        <div className="strategy-detail">
          {activeConfig ? (
            <>
              <div className="strategy-detail-header">
                <h2>{activeName}</h2>
                <button className="btn btn-primary" onClick={backtest} disabled={busy !== ""}>
                  {busy === "backtesting" ? "Simulating…" : "Run Backtest"}
                </button>
              </div>
              <p className="strategy-desc">{activeConfig.description}</p>
              <div className="rule-grid">
                <div className="rule"><span>Session</span>{activeConfig.session.open}–{activeConfig.session.hardCutoff} ET</div>
                <div className="rule"><span>Continuation</span>{activeConfig.phases.tradeContinuation ? `first ${activeConfig.phases.continuationEndMin} min` : "off"}</div>
                <div className="rule"><span>Reversion</span>{activeConfig.phases.tradeReversion ? `until ${activeConfig.phases.reversionEndMin} min (≥${activeConfig.entry.minExtensionPoints} pts ext.)` : "off"}</div>
                <div className="rule"><span>Stop / Target</span>{activeConfig.risk.stopPoints} / {activeConfig.risk.targetPoints} pts</div>
                <div className="rule"><span>Trade caps</span>{activeConfig.risk.maxTradesPerDay}/day, stop after {activeConfig.risk.stopAfterConsecutiveLosses} losses</div>
                <div className="rule"><span>Daily $ caps</span>+${activeConfig.risk.dailyProfitCap} / −${activeConfig.risk.dailyLossCap}</div>
                <div className="rule"><span>Displacement</span>≥{activeConfig.entry.displacementSizeRatio}× avg TR, wick ≤ {Math.round(activeConfig.entry.maxWickRatio * 100)}%</div>
                <div className="rule"><span>Structure</span>{activeConfig.entry.structureLookbackMin} min lookback, +{activeConfig.entry.breakBufferPoints} pt buffer</div>
                <div className="rule"><span>Eval sim</span>${activeConfig.eval.accountSize.toLocaleString()} acct, +${activeConfig.eval.profitTarget.toLocaleString()} target, ${activeConfig.eval.trailingMaxDrawdown.toLocaleString()} trailing DD</div>
              </div>

              {result && (
                <div className="bt-results">
                  <div className="bt-results-header">
                    <h3>Simulation — {resultFor}</h3>
                    <span className={`data-source-badge ${result.dataSource === "supabase" ? "live" : "static"}`}>
                      {result.dataSource === "supabase"
                        ? "● Real historical data (Supabase)"
                        : "○ Synthetic sample data — import real NQ bars for meaningful results"}
                    </span>
                  </div>
                  <div className="stat-grid">
                    <div className="stat-card"><div className="label">Success Rate</div><div className={`value ${result.winRate >= 50 ? "positive" : "negative"}`}>{result.winRate.toFixed(1)}%</div></div>
                    <div className="stat-card"><div className="label">Net P&amp;L</div><div className={`value ${result.netPnl >= 0 ? "positive" : "negative"}`}>{fmtMoney(result.netPnl)}</div></div>
                    <div className="stat-card"><div className="label">Return %</div><div className={`value ${result.netPnlPct >= 0 ? "positive" : "negative"}`}>{result.netPnlPct.toFixed(2)}%</div></div>
                    <div className="stat-card"><div className="label">Total Gained</div><div className="value positive">{fmtMoney(result.totalGained)}</div></div>
                    <div className="stat-card"><div className="label">Total Lost</div><div className="value negative">{fmtMoney(-result.totalLost)}</div></div>
                    <div className="stat-card"><div className="label">Trades (W/L)</div><div className="value">{result.totalTrades} ({result.wins}/{result.losses})</div></div>
                    <div className="stat-card"><div className="label">Eval Pass Rate</div><div className={`value ${result.evalPassRate >= 33 ? "positive" : "negative"}`}>{result.evalPassRate.toFixed(1)}%</div></div>
                    <div className="stat-card"><div className="label">Max Drawdown</div><div className="value negative">{fmtMoney(-result.maxDrawdown)}</div></div>
                    <div className="stat-card"><div className="label">Days (profitable)</div><div className="value">{result.tradingDays} ({result.profitableDays})</div></div>
                    <div className="stat-card"><div className="label">Best / Worst Day</div><div className="value">{fmtMoney(result.bestDay)} / {fmtMoney(result.worstDay)}</div></div>
                    <div className="stat-card"><div className="label">Cap Hits (+/−)</div><div className="value">{result.daysHitProfitCap} / {result.daysHitLossCap}</div></div>
                    <div className="stat-card"><div className="label">Avg Days to Eval Result</div><div className="value">{result.avgDaysToEvalResult}</div></div>
                  </div>

                  {result.trades.length > 0 && (
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Entry (ET)</th><th>Phase</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Result</th><th>P&amp;L ($)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...result.trades].reverse().slice(0, 50).map((t, i) => (
                            <tr key={i}>
                              <td>{new Date(t.entryTime).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" })}</td>
                              <td>{t.phase}</td>
                              <td><span className={`badge ${t.direction}`}>{t.direction}</span></td>
                              <td>{t.entry.toFixed(2)}</td>
                              <td>{t.exit.toFixed(2)}</td>
                              <td><span className={`badge ${t.win ? "win" : "loss"}`}>{t.win ? "Win" : "Loss"}</span></td>
                              <td className={t.pnlDollars >= 0 ? "positive" : "negative"}>{fmtMoney(t.pnlDollars)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="empty-state">Select a strategy or generate one with AI.</div>
          )}
        </div>
      </div>
    </div>
  );
}
