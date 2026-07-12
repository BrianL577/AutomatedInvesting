"use client";

import { useEffect, useState } from "react";
import { survivabilityViolation, type StrategyConfig, type SavedStrategy } from "../../lib/strategySchema";
import type { BacktestResult } from "../../lib/backtester";

type BacktestResponse = BacktestResult & { dataSource: "supabase" | "sample"; error?: string };

type OptimizeCandidate = {
  round: number;
  rationale: string;
  diff: unknown;
  fitness: number;
  result: BacktestResult;
  config: StrategyConfig;
};

type OptimizeResponse = {
  bestConfig: StrategyConfig;
  bestResult: BacktestResult;
  history: OptimizeCandidate[];
  dataSource: "supabase" | "sample";
  warning?: string;
  error?: string;
};

type ChatMessage = { role: "user" | "assistant"; content: string; config?: StrategyConfig | null };

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

/** ⓘ icon with a themed tooltip bubble shown on hover. */
function Hint({ text }: { text: string }) {
  return (
    <span className="hint-wrap">
      <span className="hint-icon" tabIndex={0}>ⓘ</span>
      <span className="hint-pop" role="tooltip">{text}</span>
    </span>
  );
}

/** One stat tile with a plain-English hover explanation. */
function StatCard({
  label,
  hint,
  value,
  tone,
}: {
  label: string;
  hint: string;
  value: string;
  tone?: "positive" | "negative" | "";
}) {
  return (
    <div className="stat-card stat-card-hint">
      <div className="label">
        {label} <span className="hint-icon">ⓘ</span>
      </div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      <span className="hint-pop" role="tooltip">{hint}</span>
    </div>
  );
}

/** Labeled number input for the manual strategy editor. */
function NumField({
  label,
  hint,
  value,
  onChange,
  step,
  min,
  max,
}: {
  label: string;
  hint: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
}) {
  return (
    <label className="edit-field">
      <span className="edit-field-label">
        {label} <Hint text={hint} />
      </span>
      <input
        type="number"
        className="test-input"
        value={value}
        step={step ?? 1}
        min={min}
        max={max}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

/** Labeled "HH:MM" time input for the manual strategy editor. */
function TimeField({ label, hint, value, onChange }: { label: string; hint: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="edit-field">
      <span className="edit-field-label">
        {label} <Hint text={hint} />
      </span>
      <input
        type="text"
        className="test-input"
        placeholder="HH:MM"
        pattern="^([01]\d|2[0-3]):[0-5]\d$"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/** Labeled checkbox for the manual strategy editor. */
function BoolField({ label, hint, value, onChange }: { label: string; hint: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="edit-field edit-field-bool">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      <span className="edit-field-label">
        {label} <Hint text={hint} />
      </span>
    </label>
  );
}

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<SavedStrategy[]>([]);
  const [selected, setSelected] = useState<SavedStrategy | null>(null);
  const [draft, setDraft] = useState<StrategyConfig | null>(null);
  const [draftPrompt, setDraftPrompt] = useState("");
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [busy, setBusy] = useState<"" | "chatting" | "backtesting" | "saving" | "optimizing">("");
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [resultFor, setResultFor] = useState("");
  const [optimizeRounds, setOptimizeRounds] = useState(3);
  const [optimizeResult, setOptimizeResult] = useState<OptimizeResponse | null>(null);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<StrategyConfig | null>(null);

  async function refresh() {
    const res = await fetch("/api/strategies");
    const data = await readJson(res);
    setStrategies(data.strategies ?? []);
    if (!selected && data.strategies?.length) setSelected(data.strategies[0]);
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeConfig: StrategyConfig | null = draft ?? selected?.config ?? null;
  const activeName = draft ? `${draft.name} (unsaved)` : selected?.config.name ?? "";

  async function sendChat() {
    const text = chatInput.trim();
    if (!text || busy !== "") return;
    // Keep the last ~20 turns so long conversations don't hit the API cap.
    const nextChat: ChatMessage[] = [...chat, { role: "user" as const, content: text }].slice(-20);
    setChat(nextChat);
    setChatInput("");
    setBusy("chatting");
    setMessage(null);
    try {
      const res = await fetch("/api/strategy-chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: nextChat.map(({ role, content }) => ({ role, content })) }),
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || "Chat failed");
      const withReply: ChatMessage[] = [...nextChat, { role: "assistant", content: data.reply, config: data.config ?? null }];
      setChat(withReply);
      // If Claude attached a concrete config, immediately test it against the
      // historical data and post the numbers back into the conversation —
      // "what happens if I do X" gets a real answer, not a hypothesis.
      if (data.config) {
        setBusy("backtesting");
        try {
          const btRes = await fetch("/api/backtest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ config: data.config }),
          });
          const bt = await readJson(btRes);
          if (btRes.ok) {
            const summary =
              `Backtest of "${data.config.name}" against ${bt.dataSource === "supabase" ? "real historical" : "synthetic sample"} data:\n` +
              `• Real-money bottom line: ${fmtMoney(bt.realWorldNetPnl)} (${fmtMoney(bt.realWorldCashPayouts)} in payouts − ${fmtMoney(bt.realWorldFeesPaid)} in fees)\n` +
              `• ${bt.chronologicalAttempts} account(s) bought, ${bt.timesFunded} reached funded\n` +
              `• Win rate ${bt.winRate.toFixed(1)}% over ${bt.totalTrades} trades\n` +
              `Load it as a draft below to see the full breakdown, or keep refining it here.`;
            setChat([...withReply, { role: "assistant", content: summary, config: null }]);
          }
        } catch {
          // Auto-backtest is best-effort; the config button still works.
        }
      }
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
      setChat(chat); // roll back the optimistic user turn so it can be retried
      setChatInput(text);
    } finally {
      setBusy("");
    }
  }

  function loadChatConfig(config: StrategyConfig) {
    setDraft(config);
    setDraftPrompt("Designed in AI chat");
    setResult(null);
    setMessage({ kind: "ok", text: "Loaded as a draft — run a backtest to see how it actually performs, then save it if you like it." });
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
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || "Backtest failed");
      setResult(data);
      setResultFor(activeName);
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy("");
    }
  }

  async function optimize() {
    if (!activeConfig) return;
    setBusy("optimizing");
    setMessage(null);
    setOptimizeResult(null);
    try {
      const res = await fetch("/api/optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseConfig: activeConfig, rounds: optimizeRounds, batchSize: 4 }),
      });
      const data = await readJson(res);
      if (!res.ok) throw new Error(data.error || "Optimization failed");
      setOptimizeResult(data);
      setMessage({
        kind: "ok",
        text: `Tried ${data.history.length} variant(s) across ${optimizeRounds} round(s). Review the leaderboard below, then "Use This Config" on the best one to load it as a draft.`,
      });
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy("");
    }
  }

  function useOptimizedConfig(config: StrategyConfig) {
    setDraft(config);
    setDraftPrompt(`Optimized from "${activeName}"`);
    setResult(null);
    setOptimizeResult(null);
    setMessage({ kind: "ok", text: "Loaded as a new draft. Review, then Save Strategy if you want to keep it." });
  }

  function startEditing() {
    if (!activeConfig) return;
    setEditForm(JSON.parse(JSON.stringify(activeConfig)));
    setEditing(true);
    setMessage(null);
  }

  function cancelEditing() {
    setEditing(false);
    setEditForm(null);
  }

  function applyEdits() {
    if (!editForm) return;
    setDraft(editForm);
    setDraftPrompt(draft ? draftPrompt : `Manually edited from "${activeName}"`);
    setEditing(false);
    setEditForm(null);
    setResult(null);
    setMessage({ kind: "ok", text: "Applied as a draft — run a backtest to see how it performs, then Save Strategy if you want to keep it." });
  }

  /** Update one field of the in-progress edit form, e.g. setEditField("risk", "stopPoints", 30). */
  function setEditField<G extends "session" | "phases" | "entry" | "risk" | "eval">(
    group: G,
    field: keyof StrategyConfig[G],
    value: StrategyConfig[G][keyof StrategyConfig[G]]
  ) {
    setEditForm((prev) => (prev ? { ...prev, [group]: { ...prev[group], [field]: value } } : prev));
  }

  async function removeStrategy(id: string) {
    setBusy("saving");
    setMessage(null);
    try {
      const res = await fetch(`/api/strategies?id=${encodeURIComponent(id)}`, { method: "DELETE" });
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
      const data = await readJson(res);
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
          <h2>Design a strategy with AI</h2>
          <p>
            Have a conversation: ask what patterns show up in your real historical data (it sees a weekly digest —
            the first and last trading day of every week), bounce ideas around, and when you&apos;ve agreed on
            something concrete it attaches a ready-to-test strategy. AI output is data, not code — every config is
            validated before it runs, and nothing counts until it survives a real backtest.
          </p>
        </div>
        {chat.length > 0 && (
          <div className="chat-box">
            {chat.map((m, i) => (
              <div key={i} className={`chat-msg ${m.role}`}>
                <div className="chat-msg-role">{m.role === "user" ? "You" : "Claude"}</div>
                <div className="chat-msg-text">{m.content}</div>
                {m.config && (
                  <button className="btn btn-primary chat-config-btn" onClick={() => loadChatConfig(m.config!)}>
                    Load &quot;{m.config.name}&quot; as draft
                  </button>
                )}
              </div>
            ))}
            {busy === "chatting" && <div className="chat-msg assistant"><div className="chat-msg-role">Claude</div><div className="chat-msg-text">Thinking…</div></div>}
          </div>
        )}
        <div className="test-panel-row">
          <textarea
            className="test-input strategy-prompt"
            rows={2}
            maxLength={4000}
            placeholder={
              chat.length === 0
                ? 'e.g. "Looking at my data, do Mondays open differently than Fridays close? What would you try?"'
                : "Reply…"
            }
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendChat();
              }
            }}
          />
          <button className="btn btn-primary" onClick={sendChat} disabled={busy !== "" || chatInput.trim().length === 0}>
            {busy === "chatting" ? "…" : "Send"}
          </button>
        </div>
        <div className="test-panel-row">
          {chat.length > 0 && (
            <button className="btn" onClick={() => { setChat([]); setMessage(null); }} disabled={busy !== ""}>
              New Conversation
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
                <div className="test-panel-row" style={{ margin: 0 }}>
                  {!editing && (
                    <>
                      <button className="btn btn-primary" onClick={backtest} disabled={busy !== ""}>
                        {busy === "backtesting" ? "Simulating…" : "Run Backtest"}
                      </button>
                      <select
                        className="test-input"
                        style={{ width: 140 }}
                        value={optimizeRounds}
                        onChange={(e) => setOptimizeRounds(Number(e.target.value))}
                        disabled={busy !== ""}
                      >
                        <option value={2}>2 rounds</option>
                        <option value={3}>3 rounds</option>
                        <option value={5}>5 rounds</option>
                      </select>
                      <button className="btn" onClick={optimize} disabled={busy !== ""}>
                        {busy === "optimizing" ? "Optimizing…" : "AI-Optimize"}
                      </button>
                      <button className="btn" onClick={startEditing} disabled={busy !== ""}>
                        Edit
                      </button>
                    </>
                  )}
                  {editing && (
                    <>
                      <button className="btn btn-primary" onClick={applyEdits}>
                        Apply as Draft
                      </button>
                      <button className="btn" onClick={cancelEditing}>
                        Cancel
                      </button>
                    </>
                  )}
                </div>
              </div>

              {!editing && (
                <>
                  <p className="strategy-desc">{activeConfig.description}</p>
                  <div className="rule-grid">
                    <div className="rule"><span>Session <Hint text="The time window when trading happens (ET). The candle at the open sets the day's 'fair price'; no new trades after the cutoff." /></span>{activeConfig.session.open}–{activeConfig.session.hardCutoff} ET</div>
                    <div className="rule"><span>Continuation <Hint text="Whether the strategy trades in the same direction as the opening move, and for how many minutes after the open." /></span>{activeConfig.phases.tradeContinuation ? `first ${activeConfig.phases.continuationEndMin} min` : "off"}</div>
                    <div className="rule"><span>Reversion <Hint text="Whether the strategy bets on price coming back toward the opening price after it has stretched far enough away, and until when." /></span>{activeConfig.phases.tradeReversion ? `until ${activeConfig.phases.reversionEndMin} min (≥${activeConfig.entry.minExtensionPoints} pts ext.)` : "off"}</div>
                    <div className="rule"><span>Stop / Target <Hint text="How many NQ points a trade loses before it's cut (stop) or gains before it's cashed in (target). On NQ, 1 point = $20 per contract." /></span>{activeConfig.risk.stopPoints} / {activeConfig.risk.targetPoints} pts</div>
                    <div className="rule"><span>Trade caps <Hint text="The most trades allowed per day, and an early quit rule after too many losses in a row." /></span>{activeConfig.risk.maxTradesPerDay}/day, stop after {activeConfig.risk.stopAfterConsecutiveLosses} losses</div>
                    <div className="rule"><span>Daily $ caps <Hint text="Stop trading for the day once profit reaches the + number or loss reaches the − number." /></span>+${activeConfig.risk.dailyProfitCap} / −${activeConfig.risk.dailyLossCap}</div>
                    <div className="rule"><span>Displacement <Hint text="How big and clean a price candle must be before the strategy treats it as a real move worth entering on (big body, small wicks)." /></span>≥{activeConfig.entry.displacementSizeRatio}× avg TR, wick ≤ {Math.round(activeConfig.entry.maxWickRatio * 100)}%</div>
                    <div className="rule"><span>Structure <Hint text="How far back it scans for recent highs/lows, and how far price must break past them to count as a genuine breakout." /></span>{activeConfig.entry.structureLookbackMin} min lookback, +{activeConfig.entry.breakBufferPoints} pt buffer</div>
                    <div className="rule"><span>Eval sim <Hint text="The simulated prop-firm account: its size, the profit needed to pass the eval, and the trailing drawdown that busts it." /></span>${activeConfig.eval.accountSize.toLocaleString()} acct, +${activeConfig.eval.profitTarget.toLocaleString()} target, ${activeConfig.eval.trailingMaxDrawdown.toLocaleString()} trailing DD</div>
                  </div>
                </>
              )}

              {editing && editForm && (
                <div className="edit-form">
                  {survivabilityViolation(editForm) && (
                    <div className="test-status test-status-error">⚠️ {survivabilityViolation(editForm)}</div>
                  )}
                  <label className="edit-field" style={{ marginBottom: 12 }}>
                    <span className="edit-field-label">Name</span>
                    <input
                      type="text"
                      className="test-input"
                      value={editForm.name}
                      onChange={(e) => setEditForm((prev) => (prev ? { ...prev, name: e.target.value } : prev))}
                    />
                  </label>

                  <p className="edit-section-label">Session</p>
                  <div className="edit-grid">
                    <TimeField label="Open" hint="ET time the session-anchor candle fires." value={editForm.session.open} onChange={(v) => setEditField("session", "open", v)} />
                    <TimeField label="Hard cutoff" hint="No new entries after this ET time." value={editForm.session.hardCutoff} onChange={(v) => setEditField("session", "hardCutoff", v)} />
                  </div>

                  <p className="edit-section-label">Phases</p>
                  <div className="edit-grid">
                    <BoolField label="Trade continuation" hint="Enter in the opening candle's direction." value={editForm.phases.tradeContinuation} onChange={(v) => setEditField("phases", "tradeContinuation", v)} />
                    <NumField label="Continuation end (min)" hint="Minutes after open the continuation window stays valid." value={editForm.phases.continuationEndMin} onChange={(v) => setEditField("phases", "continuationEndMin", v)} min={0} max={120} />
                    <BoolField label="Trade reversion" hint="Enter back toward the open price after it has extended far enough." value={editForm.phases.tradeReversion} onChange={(v) => setEditField("phases", "tradeReversion", v)} />
                    <NumField label="Reversion end (min)" hint="Minutes after open the reversion window stays valid." value={editForm.phases.reversionEndMin} onChange={(v) => setEditField("phases", "reversionEndMin", v)} min={0} max={360} />
                  </div>

                  <p className="edit-section-label">Entry filters</p>
                  <div className="edit-grid">
                    <NumField label="Displacement size ratio" hint="Candle true range must be at least this x the ~10-bar average." value={editForm.entry.displacementSizeRatio} onChange={(v) => setEditField("entry", "displacementSizeRatio", v)} step={0.05} min={0.5} max={5} />
                    <NumField label="Displacement prev ratio" hint="Candle true range must be at least this x the previous bar's." value={editForm.entry.displacementPrevRatio} onChange={(v) => setEditField("entry", "displacementPrevRatio", v)} step={0.05} min={0} max={5} />
                    <NumField label="Max wick ratio" hint="Total wick / range must be under this to count as a clean displacement (0-1)." value={editForm.entry.maxWickRatio} onChange={(v) => setEditField("entry", "maxWickRatio", v)} step={0.05} min={0} max={1} />
                    <NumField label="Structure lookback (min)" hint="How far back it scans for swing highs/lows." value={editForm.entry.structureLookbackMin} onChange={(v) => setEditField("entry", "structureLookbackMin", v)} min={2} max={240} />
                    <NumField label="Swing strength" hint="Bars required on each side to confirm a pivot." value={editForm.entry.swingStrength} onChange={(v) => setEditField("entry", "swingStrength", v)} min={1} max={10} />
                    <NumField label="Break buffer (pts)" hint="Price must clear structure by this many points to count as a real break." value={editForm.entry.breakBufferPoints} onChange={(v) => setEditField("entry", "breakBufferPoints", v)} step={0.25} min={0} max={50} />
                    <NumField label="Min extension (pts)" hint="Minimum move away from the open before a reversion trade is valid." value={editForm.entry.minExtensionPoints} onChange={(v) => setEditField("entry", "minExtensionPoints", v)} min={0} max={500} />
                  </div>

                  <p className="edit-section-label">Risk</p>
                  <div className="edit-grid">
                    <NumField label="Stop (pts)" hint="Points of adverse move before the trade is cut." value={editForm.risk.stopPoints} onChange={(v) => setEditField("risk", "stopPoints", v)} min={1} max={500} />
                    <NumField label="Target (pts)" hint="Points of favorable move before the trade is cashed in." value={editForm.risk.targetPoints} onChange={(v) => setEditField("risk", "targetPoints", v)} min={1} max={1000} />
                    <NumField label="Contracts per trade" hint="How many contracts each trade uses — multiplies the dollar stop/target directly." value={editForm.risk.contractsPerTrade} onChange={(v) => setEditField("risk", "contractsPerTrade", v)} min={1} max={10} />
                    <NumField label="Max trades/day" hint="Hard cap on entries per day." value={editForm.risk.maxTradesPerDay} onChange={(v) => setEditField("risk", "maxTradesPerDay", v)} min={1} max={20} />
                    <NumField label="Stop after N losses" hint="Quit for the day after this many consecutive losses." value={editForm.risk.stopAfterConsecutiveLosses} onChange={(v) => setEditField("risk", "stopAfterConsecutiveLosses", v)} min={1} max={10} />
                    <NumField label="Daily profit cap ($)" hint="Stop trading for the day once profit reaches this." value={editForm.risk.dailyProfitCap} onChange={(v) => setEditField("risk", "dailyProfitCap", v)} min={0} max={100000} />
                    <NumField label="Daily loss cap ($)" hint="Stop trading for the day once loss reaches this." value={editForm.risk.dailyLossCap} onChange={(v) => setEditField("risk", "dailyLossCap", v)} min={0} max={100000} />
                  </div>

                  <p className="edit-section-label">Eval simulation</p>
                  <div className="edit-grid">
                    <NumField label="Account size ($)" hint="Simulated prop-firm account starting balance." value={editForm.eval.accountSize} onChange={(v) => setEditField("eval", "accountSize", v)} step={1000} min={1000} max={1000000} />
                    <NumField label="Profit target ($)" hint="Profit needed to pass the eval." value={editForm.eval.profitTarget} onChange={(v) => setEditField("eval", "profitTarget", v)} step={100} min={100} max={100000} />
                    <NumField label="Trailing max drawdown ($)" hint="Balance drop from peak that busts the account — a single trade's max loss must stay under this." value={editForm.eval.trailingMaxDrawdown} onChange={(v) => setEditField("eval", "trailingMaxDrawdown", v)} step={100} min={100} max={100000} />
                  </div>
                </div>
              )}

              {result && !editing && (
                <div className="bt-results">
                  <div className="bt-results-header">
                    <h3>Simulation — {resultFor}</h3>
                    <span className={`data-source-badge ${result.dataSource === "supabase" ? "live" : "static"}`}>
                      {result.dataSource === "supabase"
                        ? "● Real historical data (Supabase)"
                        : "○ Synthetic sample data — import real NQ bars for meaningful results"}
                    </span>
                  </div>
                  <div className="bt-results-header">
                    <h3>While in Eval <Hint text="Trades made while the simulated account was still trying to hit the eval profit target." /></h3>
                  </div>
                  <div className="stat-grid">
                    <StatCard
                      label="Win Rate"
                      hint="Of the trades taken during the eval stage, the percentage that hit the profit target instead of the stop-loss."
                      value={`${result.evalStage.winRate.toFixed(1)}%`}
                      tone={result.evalStage.winRate >= 50 ? "positive" : "negative"}
                    />
                    <StatCard
                      label="Net P&L"
                      hint="Dollars won minus dollars lost across all eval-stage trades. Virtual account money, not real cash."
                      value={fmtMoney(result.evalStage.netPnl)}
                      tone={result.evalStage.netPnl >= 0 ? "positive" : "negative"}
                    />
                    <StatCard
                      label="Trades (W/L)"
                      hint="Total eval-stage trades, and how many were wins vs losses."
                      value={`${result.evalStage.trades} (${result.evalStage.wins}/${result.evalStage.losses})`}
                    />
                  </div>

                  <div className="bt-results-header" style={{ marginTop: 20 }}>
                    <h3>Once Funded <Hint text="Trades made after the account reached funded status — this performance is what actually generates real payouts." /></h3>
                  </div>
                  <div className="stat-grid">
                    <StatCard
                      label="Win Rate"
                      hint="Of the trades taken after reaching funded, the percentage that won."
                      value={`${result.fundedStage.winRate.toFixed(1)}%`}
                      tone={result.fundedStage.winRate >= 50 ? "positive" : "negative"}
                    />
                    <StatCard
                      label="Net P&L"
                      hint="Dollars won minus lost across funded-stage trades. Still account money — real cash only moves via payouts below."
                      value={fmtMoney(result.fundedStage.netPnl)}
                      tone={result.fundedStage.netPnl >= 0 ? "positive" : "negative"}
                    />
                    <StatCard
                      label="Trades (W/L)"
                      hint="Total funded-stage trades, and how many were wins vs losses."
                      value={`${result.fundedStage.trades} (${result.fundedStage.wins}/${result.fundedStage.losses})`}
                    />
                  </div>

                  <div className="bt-results-header" style={{ marginTop: 20 }}>
                    <h3>Real Money <Hint text="Actual money in and out of your pocket: $50 per eval/reactivation, payouts at 50% share ($2,000 cap per event, 50% single-day consistency rule). Verify these against the firm's current rules." /></h3>
                  </div>
                  <div className="stat-grid">
                    <StatCard
                      label="Bottom Line"
                      hint="Payouts received minus fees paid — the actual cash result of running this strategy over the whole period. Positive = profitable."
                      value={fmtMoney(result.realWorldNetPnl)}
                      tone={result.realWorldNetPnl >= 0 ? "positive" : "negative"}
                    />
                    <StatCard
                      label="Fees Paid"
                      hint="Every $50 spent buying an eval or reactivating after busting an account."
                      value={fmtMoney(-result.realWorldFeesPaid)}
                      tone="negative"
                    />
                    <StatCard
                      label="Payouts Received"
                      hint="Real cash withdrawn from funded accounts. Each payout is 50% of the funded profit at the moment it triggers, capped at $2,000 — so it's usually less than $2,000 (e.g. a payout triggering at $3,600 profit pays $1,800). It also only counts when no single day made over half the profit."
                      value={fmtMoney(result.realWorldCashPayouts)}
                      tone="positive"
                    />
                    <StatCard
                      label="Accounts Bought"
                      hint="How many evals were purchased in total — every bust means buying another."
                      value={`${result.chronologicalAttempts}`}
                    />
                    <StatCard
                      label="Reached Funded"
                      hint="How many of those attempts made it through the eval to a funded account."
                      value={`${result.timesFunded}`}
                    />
                  </div>

                  <details className="bt-pooled-details">
                    <summary>Pooled stats (all {result.totalTrades} trades on one never-resetting account — not real economics, kept for reference)</summary>
                    <div className="stat-grid" style={{ marginTop: 12 }}>
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
                      {result.incompleteTrades > 0 && (
                        <div className="stat-card"><div className="label">Excluded (unresolved)</div><div className="value">{result.incompleteTrades}</div></div>
                      )}
                    </div>
                    {result.incompleteTrades > 0 && (
                      <p style={{ marginTop: 8, opacity: 0.7, fontSize: "0.85em" }}>
                        {result.incompleteTrades} trade(s) never hit their stop or target before the available bar data ran out and were excluded from every stat above — the bracket is never flattened early or faked with a made-up exit price.
                      </p>
                    )}
                  </details>

                  <details className="bt-pooled-details">
                    <summary>Breakdown by phase &amp; session — spot which entries drag win rate down</summary>
                    <div style={{ marginTop: 12 }}>
                      <p style={{ opacity: 0.7, fontSize: "0.85em", marginBottom: 8 }}>By phase</p>
                      <div className="stat-grid">
                        {result.byPhase.map((g) => (
                          <div className="stat-card" key={g.key}>
                            <div className="label">{g.key}</div>
                            <div className={`value ${g.winRate >= 50 ? "positive" : "negative"}`}>{g.winRate.toFixed(1)}%</div>
                            <div style={{ fontSize: "0.8em", opacity: 0.7, marginTop: 4 }}>
                              {g.trades} trades ({g.wins}/{g.losses}) · {fmtMoney(g.netPnl)}
                            </div>
                          </div>
                        ))}
                      </div>
                      <p style={{ opacity: 0.7, fontSize: "0.85em", margin: "16px 0 8px" }}>By session open</p>
                      <div className="stat-grid">
                        {result.bySession.map((g) => (
                          <div className="stat-card" key={g.key}>
                            <div className="label">{g.key} ET</div>
                            <div className={`value ${g.winRate >= 50 ? "positive" : "negative"}`}>{g.winRate.toFixed(1)}%</div>
                            <div style={{ fontSize: "0.8em", opacity: 0.7, marginTop: 4 }}>
                              {g.trades} trades ({g.wins}/{g.losses}) · {fmtMoney(g.netPnl)}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </details>

                  {result.portfolio && (
                    <>
                      <div className="bt-results-header" style={{ marginTop: 20 }}>
                        <h3>Portfolio — {result.portfolio.accountCount} Accounts</h3>
                        <span className="data-source-badge static">
                          {result.portfolio.oneTradePerDay ? "One trade per account per day" : "Full daily trading per account"}, starts staggered {result.portfolio.staggerDays} day(s) apart
                        </span>
                      </div>
                      <div className="stat-grid">
                        <div className="stat-card"><div className="label">Total Fees Paid</div><div className="value negative">{fmtMoney(-result.portfolio.feesPaid)}</div></div>
                        <div className="stat-card"><div className="label">Total Cash Payouts</div><div className="value positive">{fmtMoney(result.portfolio.cashPayouts)}</div></div>
                        <div className="stat-card"><div className="label">Portfolio Net (payouts − fees)</div><div className={`value ${result.portfolio.netPnl >= 0 ? "positive" : "negative"}`}>{fmtMoney(result.portfolio.netPnl)}</div></div>
                        <div className="stat-card"><div className="label">Evals Bought (all accounts)</div><div className="value">{result.portfolio.attemptsBought}</div></div>
                        <div className="stat-card"><div className="label">Times Reached Funded</div><div className="value">{result.portfolio.timesFunded}</div></div>
                      </div>
                    </>
                  )}

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

              {optimizeResult && (
                <div className="bt-results">
                  <p className="bt-explainer">
                    Claude proposed parameter tweaks each round based on how prior variants actually performed against
                    your real historical data. <strong>Profitable</strong> simply means the real-money bottom line
                    (payouts minus fees) came out positive.
                  </p>
                  {optimizeResult.warning && (
                    <div className="test-status test-status-error">{optimizeResult.warning}</div>
                  )}
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>What was tried <Hint text="Claude's one-sentence hypothesis for why this variant might do better." /></th>
                          <th>Win Rate <Hint text="Percentage of all trades that won." /></th>
                          <th>Bottom Line <Hint text="Real cash: payouts received minus fees paid. Positive = profitable." /></th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...optimizeResult.history]
                          .sort((a, b) => b.fitness - a.fitness)
                          .map((c, i) => (
                            <tr key={i}>
                              <td>{i === 0 ? "🏆" : i + 1}</td>
                              <td>{c.rationale}</td>
                              <td>{c.result.winRate.toFixed(1)}%</td>
                              <td>
                                <span className={`badge ${c.result.realWorldNetPnl > 0 ? "win" : "loss"}`}>
                                  {fmtMoney(c.result.realWorldNetPnl)}{c.result.realWorldNetPnl > 0 ? " Profitable" : ""}
                                </span>
                              </td>
                              <td>
                                <button className="btn" onClick={() => useOptimizedConfig(c.config)}>
                                  Use This Config
                                </button>
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
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
