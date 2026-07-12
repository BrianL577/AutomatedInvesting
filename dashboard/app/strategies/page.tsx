"use client";

import { useEffect, useState } from "react";
import type { StrategyConfig, SavedStrategy } from "../../lib/strategySchema";
import type { BacktestResult } from "../../lib/backtester";

type BacktestResponse = BacktestResult & { dataSource: "supabase" | "sample"; error?: string };

function fmtMoney(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

type ChatMessage = { role: "user" | "assistant"; content: string };

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<SavedStrategy[]>([]);
  const [selected, setSelected] = useState<SavedStrategy | null>(null);
  const [draft, setDraft] = useState<StrategyConfig | null>(null);
  const [draftPrompt, setDraftPrompt] = useState("");
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState<"" | "chatting" | "backtesting" | "saving">("");
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

  async function sendChat(text: string) {
    const trimmed = text.trim();
    if (trimmed.length < 2) return;
    const nextChat = [...chat, { role: "user" as const, content: trimmed }];
    setChat(nextChat);
    setChatInput("");
    setBusy("chatting");
    setMessage(null);
    try {
      const res = await fetch("/api/strategy-chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: nextChat, config: draft ?? selected?.config }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Chat failed");
      setChat([...nextChat, { role: "assistant", content: data.reply || "(no reply)" }]);
      if (data.config) {
        setDraft(data.config);
        setDraftPrompt(trimmed);
        setResult(null);
      }
      if (data.configError) setMessage({ kind: "error", text: data.configError });
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy("");
    }
  }

  function startOver() {
    setChat([]);
    setChatInput("");
    setMessage(null);
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
          <p
            title="Chat back and forth with Claude to design a strategy. It looks at how the current draft (or JJ's default) actually performed on each weekday historically before suggesting changes, and only aims to be profitable overall — not to hit any fixed win-rate number."
          >
            Talk it through with the AI — it can see how the strategy performs on different days historically and
            will suggest changes based on real patterns, not a fixed win-rate target.
          </p>
        </div>

        {chat.length === 0 ? (
          <div className="test-panel-row">
            <button
              className="btn btn-primary"
              onClick={() => sendChat("Look at the historical weekday patterns and generate the strategy you think will be most profitable. Explain your reasoning.")}
              disabled={busy !== ""}
              title="Skip the back-and-forth — AI analyzes historical patterns and proposes a strategy on its own."
            >
              {busy === "chatting" ? "Thinking…" : "Generate Strategy For Me"}
            </button>
          </div>
        ) : (
          <div className="chat-thread">
            {chat.map((m, i) => (
              <div key={i} className={`chat-msg chat-msg-${m.role}`}>
                <span className="chat-msg-role">{m.role === "user" ? "You" : "AI"}</span>
                <span className="chat-msg-text">{m.content}</span>
              </div>
            ))}
            {busy === "chatting" && <div className="chat-msg chat-msg-assistant"><span className="chat-msg-role">AI</span><span className="chat-msg-text">Thinking…</span></div>}
          </div>
        )}

        <div className="test-panel-row">
          <textarea
            className="test-input strategy-prompt"
            rows={2}
            maxLength={4000}
            placeholder={chat.length === 0
              ? 'e.g. "Trade only mean reversion between 30 and 90 minutes after the open, with a tight 15 point stop and 45 point target."'
              : "Reply to the AI…"}
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendChat(chatInput);
              }
            }}
          />
        </div>
        <div className="test-panel-row">
          <button className="btn btn-primary" onClick={() => sendChat(chatInput)} disabled={busy !== "" || chatInput.trim().length < 2}>
            {busy === "chatting" ? "Thinking…" : chat.length === 0 ? "Send" : "Send Reply"}
          </button>
          {chat.length > 0 && (
            <button className="btn" onClick={startOver} disabled={busy !== ""}>
              Start Over
            </button>
          )}
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
                <div className="rule" title="What time the trading window opens and the latest time it will start a new trade."><span>Session</span>{activeConfig.session.open}–{activeConfig.session.hardCutoff} ET</div>
                <div className="rule" title="Whether the strategy trades in the same direction as the opening move, and for how long after the open."><span>Continuation</span>{activeConfig.phases.tradeContinuation ? `first ${activeConfig.phases.continuationEndMin} min` : "off"}</div>
                <div className="rule" title="Whether the strategy trades back toward the opening price once it has moved far enough away, and until when."><span>Reversion</span>{activeConfig.phases.tradeReversion ? `until ${activeConfig.phases.reversionEndMin} min (≥${activeConfig.entry.minExtensionPoints} pts ext.)` : "off"}</div>
                <div className="rule" title="How many points price must move against you before it exits a loser (Stop) or a winner (Target)."><span>Stop / Target</span>{activeConfig.risk.stopPoints} / {activeConfig.risk.targetPoints} pts</div>
                <div className="rule" title="How many trades it will take per day, and when it stops early after consecutive losses."><span>Trade caps</span>{activeConfig.risk.maxTradesPerDay}/day, stop after {activeConfig.risk.stopAfterConsecutiveLosses} losses</div>
                <div className="rule" title="Stops trading for the day once profit or loss reaches these dollar amounts."><span>Daily $ caps</span>+${activeConfig.risk.dailyProfitCap} / −${activeConfig.risk.dailyLossCap}</div>
                <div className="rule" title="How large and clean a price candle must be before the strategy treats it as a real breakout worth trading."><span>Displacement</span>≥{activeConfig.entry.displacementSizeRatio}× avg TR, wick ≤ {Math.round(activeConfig.entry.maxWickRatio * 100)}%</div>
                <div className="rule" title="How far back it looks for recent price highs/lows, and how far price must clear them to count as a breakout."><span>Structure</span>{activeConfig.entry.structureLookbackMin} min lookback, +{activeConfig.entry.breakBufferPoints} pt buffer</div>
                <div className="rule" title="Settings for simulating a funded prop-firm account: starting size, profit target, and max allowed drawdown."><span>Eval sim</span>${activeConfig.eval.accountSize.toLocaleString()} acct, +${activeConfig.eval.profitTarget.toLocaleString()} target, ${activeConfig.eval.trailingMaxDrawdown.toLocaleString()} trailing DD</div>
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
                  <div className="stat-grid stat-grid-primary">
                    <div className="stat-card" title="Whether this strategy made or lost money overall, and by how much.">
                      <div className="label">Profitable?</div>
                      <div className={`value ${result.netPnl >= 0 ? "positive" : "negative"}`}>{result.netPnl >= 0 ? "Yes" : "No"} ({fmtMoney(result.netPnl)})</div>
                    </div>
                    <div className="stat-card" title="Net profit or loss as a percentage of the simulated account size — the main number to compare strategies by.">
                      <div className="label">Return %</div>
                      <div className={`value ${result.netPnlPct >= 0 ? "positive" : "negative"}`}>{result.netPnlPct.toFixed(2)}%</div>
                    </div>
                    <div className="stat-card" title="Percent of trades that hit the profit target instead of the stop. A lower win rate can still be very profitable if winners are bigger than losers.">
                      <div className="label">Win Rate</div>
                      <div className="value">{result.winRate.toFixed(1)}%</div>
                    </div>
                    <div className="stat-card" title="The worst peak-to-trough dip in account value during the test. Smaller is safer.">
                      <div className="label">Max Drawdown</div>
                      <div className="value negative">{fmtMoney(-result.maxDrawdown)}</div>
                    </div>
                    <div className="stat-card" title="How many trades this strategy took over the whole test period.">
                      <div className="label">Trades</div>
                      <div className="value">{result.totalTrades} ({result.wins}W / {result.losses}L)</div>
                    </div>
                  </div>

                  <details className="stat-grid-advanced">
                    <summary title="More detailed numbers behind the headline stats above.">More details</summary>
                    <div className="stat-grid">
                      <div className="stat-card" title="Total dollars made on winning trades only."><div className="label">Total Gained</div><div className="value positive">{fmtMoney(result.totalGained)}</div></div>
                      <div className="stat-card" title="Total dollars lost on losing trades only."><div className="label">Total Lost</div><div className="value negative">{fmtMoney(-result.totalLost)}</div></div>
                      <div className="stat-card" title="Percent of simulated prop-firm evaluation attempts (a common way funded trading accounts are tested) that this strategy would have passed."><div className="label">Eval Pass Rate</div><div className={`value ${result.evalPassRate >= 33 ? "positive" : "negative"}`}>{result.evalPassRate.toFixed(1)}%</div></div>
                      <div className="stat-card" title="Number of days the strategy traded, and how many of those days ended profitable."><div className="label">Days (profitable)</div><div className="value">{result.tradingDays} ({result.profitableDays})</div></div>
                      <div className="stat-card" title="The single best and single worst day of P&L in the test."><div className="label">Best / Worst Day</div><div className="value">{fmtMoney(result.bestDay)} / {fmtMoney(result.worstDay)}</div></div>
                      <div className="stat-card" title="How many days the strategy hit its daily profit cap vs. its daily loss cap and stopped trading for the day."><div className="label">Cap Hits (+/−)</div><div className="value">{result.daysHitProfitCap} / {result.daysHitLossCap}</div></div>
                      <div className="stat-card" title="Average number of days it took a simulated prop-firm eval attempt to resolve (pass or fail)."><div className="label">Avg Days to Eval Result</div><div className="value">{result.avgDaysToEvalResult}</div></div>
                    </div>
                  </details>

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
