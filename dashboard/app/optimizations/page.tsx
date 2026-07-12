"use client";

import { useEffect, useState } from "react";
import type { StrategyConfig } from "../../lib/strategySchema";
import type { SavedOptimization } from "../../lib/optimizationStore";

/** Parse a fetch response as JSON, surfacing plain-text errors (e.g. Vercel
 * timeout pages) as a readable message instead of "Unexpected token ... is
 * not valid JSON". */
async function readJson(res: Response): Promise<any> {
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { error: text.trim().slice(0, 200) || `Request failed with status ${res.status}` };
  }
}

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function OptimizationsPage() {
  const [runs, setRuns] = useState<SavedOptimization[]>([]);
  const [signedIn, setSignedIn] = useState(true);
  const [selected, setSelected] = useState<SavedOptimization | null>(null);
  const [busy, setBusy] = useState<"" | "loading" | "using" | "deleting">("");
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);

  async function refresh() {
    setBusy("loading");
    try {
      const res = await fetch("/api/optimizations");
      const data = await readJson(res);
      setRuns(data.optimizations ?? []);
      setSignedIn(data.signedIn ?? true);
    } catch {
      // best-effort
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function useConfig(config: StrategyConfig) {
    setBusy("using");
    setMessage(null);
    try {
      const res = await fetch("/api/strategies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config, source: "ai", prompt: "Loaded from a saved AI-Optimize run" }),
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || "Save failed");
      setMessage({ kind: "ok", text: `Saved "${config.name}" as a strategy — find it in Strategy Creator.` });
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy("");
    }
  }

  async function removeRun(id: string) {
    setBusy("deleting");
    setMessage(null);
    try {
      const res = await fetch(`/api/optimizations?id=${encodeURIComponent(id)}`, { method: "DELETE" });
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || "Delete failed");
      if (selected?.id === id) setSelected(null);
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
          <h1>Optimizations</h1>
          <p>
            Every AI-Optimize run you save lives here — revisit the leaderboard or load any variant as a strategy
            anytime, for free, with no new AI call. <a href="/strategies">← Back to Strategy Creator</a>
          </p>
        </div>
      </div>

      {!signedIn ? (
        <div className="empty-state">Sign in to save and view your AI-Optimize runs.</div>
      ) : (
        <div className="strategy-layout">
          <div className="strategy-list">
            <h2>Saved Runs</h2>
            {busy === "loading" && runs.length === 0 && <div className="empty-state">Loading…</div>}
            {busy !== "loading" && runs.length === 0 && (
              <div className="empty-state">
                No saved runs yet — run AI-Optimize on a strategy, then hit &quot;Save This Run&quot;.
              </div>
            )}
            {runs.map((r) => (
              <div
                key={r.id}
                role="button"
                tabIndex={0}
                className={`strategy-item ${selected?.id === r.id ? "active" : ""}`}
                onClick={() => setSelected(r)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") setSelected(r);
                }}
              >
                <span className="strategy-item-name">
                  {r.base_config_name} ({r.rounds} rounds, {r.history.length} variants)
                </span>
                <span className={`badge ${r.data_source === "supabase" ? "long" : "short"}`}>{r.data_source}</span>
                <button
                  className="strategy-item-delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeRun(r.id);
                  }}
                  title="Delete this saved run"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>

          <div className="strategy-detail">
            {selected ? (
              <>
                <div className="strategy-detail-header">
                  <h2>
                    {selected.base_config_name} — saved {new Date(selected.created_at).toLocaleString()}
                  </h2>
                </div>
                {message && (
                  <div className={`test-status test-status-${message.kind === "error" ? "error" : "success"}`}>
                    {message.text}
                  </div>
                )}
                <div className="bt-results">
                  <p className="bt-explainer">
                    {selected.rounds} round(s), {selected.history.length} variant(s) tried against{" "}
                    {selected.data_source === "supabase" ? "real historical data" : "synthetic sample data"}.{" "}
                    <strong>Profitable</strong> means the real-money bottom line (payouts minus fees) came out
                    positive.
                  </p>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>What was tried</th>
                          <th>Win Rate</th>
                          <th>Bottom Line</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...selected.history]
                          .sort((a, b) => b.fitness - a.fitness)
                          .map((c, i) => (
                            <tr key={i}>
                              <td>{i === 0 ? "🏆" : i + 1}</td>
                              <td>{c.rationale}</td>
                              <td>{c.result.winRate.toFixed(1)}%</td>
                              <td>
                                <span className={`badge ${c.result.realWorldNetPnl > 0 ? "win" : "loss"}`}>
                                  {fmtMoney(c.result.realWorldNetPnl)}
                                  {c.result.realWorldNetPnl > 0 ? " Profitable" : ""}
                                </span>
                              </td>
                              <td>
                                <button className="btn" onClick={() => useConfig(c.config)} disabled={busy !== ""}>
                                  {busy === "using" ? "…" : "Use This Config"}
                                </button>
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </>
            ) : (
              <div className="empty-state">Select a saved run to view its leaderboard.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
