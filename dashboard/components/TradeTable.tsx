"use client";

import { useState } from "react";
import type { Trade } from "../lib/types";
import { gainLossDollars } from "../lib/tradeMath";

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

/** Shared trade table used by both the main dashboard and the Virtual
 * Practice page — clicking any row opens a detail panel with the trade's
 * chart snapshot (when one was captured) and its planned gain:loss ratio.
 * `sourceBadges`, when true, distinguishes Test/Virtual/Win/Loss results
 * (main dashboard); when false every row just gets a plain Win/Loss badge
 * (virtual-practice page, where every row is already known to be virtual). */
export default function TradeTable({ trades, sourceBadges = false }: { trades: Trade[]; sourceBadges?: boolean }) {
  const [selected, setSelected] = useState<Trade | null>(null);

  if (trades.length === 0) return null;

  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Entry Time (ET)</th>
              <th>Account</th>
              <th>Phase</th>
              <th>Direction</th>
              <th>Grade</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Result</th>
              <th>P&amp;L (pts)</th>
              <th>P&amp;L ($)</th>
              <th title="Planned gain:loss in dollars for this trade -- what it was risked to make, not the outcome">
                Gain:Loss ($)
              </th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {[...trades].reverse().map((t) => {
              const isTest = sourceBadges && (t.source === "connection_test" || t.phase === "test");
              const isVirtual = sourceBadges && t.source === "virtual_practice";
              const { gain, loss } = gainLossDollars(t);
              return (
                <tr key={t.id} className="trade-row" onClick={() => setSelected(t)}>
                  <td>{fmtTime(t.timestamp)}</td>
                  <td>{t.account_name || "—"}</td>
                  <td>{t.phase}</td>
                  <td>
                    <span className={`badge ${t.direction}`}>{t.direction}</span>
                  </td>
                  <td>{t.grade}</td>
                  <td>{t.entry_price.toFixed(2)}</td>
                  <td>{t.exit_price.toFixed(2)}</td>
                  <td>
                    {isTest ? (
                      <span className="badge test">Test</span>
                    ) : isVirtual ? (
                      <span className="badge test" title={`Virtual practice — ${t.win ? "win" : "loss"}, no real order`}>
                        Virtual {t.win ? "Win" : "Loss"}
                      </span>
                    ) : (
                      <span className={`badge ${t.win ? "win" : "loss"}`}>{t.win ? "Win" : "Loss"}</span>
                    )}
                  </td>
                  <td className={t.pnl_points >= 0 ? "positive" : "negative"}>{t.pnl_points.toFixed(2)}</td>
                  <td className={t.pnl_dollars >= 0 ? "positive" : "negative"}>{fmtMoney(t.pnl_dollars)}</td>
                  <td className="ratio-cell">
                    <span className="positive">{gain}</span>:<span className="negative">{loss}</span>
                  </td>
                  <td className="reason-cell" title={t.reason}>
                    {t.reason}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selected && <TradeDetailModal trade={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function TradeDetailModal({ trade, onClose }: { trade: Trade; onClose: () => void }) {
  const { gain, loss } = gainLossDollars(trade);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>
            {trade.account_name || "—"} — {trade.direction.toUpperCase()} {trade.win ? "Win" : "Loss"} —{" "}
            {fmtTime(trade.timestamp)}
          </h3>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-chart">
          {trade.chart_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={trade.chart_url} alt={`Chart for ${trade.account_name || "trade"} at ${trade.timestamp}`} />
          ) : (
            <div className="modal-chart-empty">
              No chart snapshot available for this trade — either it was logged before charting was wired up, or
              rendering/upload failed for this specific trade (never blocks logging the trade itself).
            </div>
          )}
        </div>

        <div className="modal-details">
          <div className="modal-detail">
            <span className="modal-detail-label">Entry</span>
            {trade.entry_price.toFixed(2)}
          </div>
          <div className="modal-detail">
            <span className="modal-detail-label">Exit</span>
            {trade.exit_price.toFixed(2)}
          </div>
          <div className="modal-detail">
            <span className="modal-detail-label">Stop</span>
            {trade.stop_price.toFixed(2)}
          </div>
          <div className="modal-detail">
            <span className="modal-detail-label">Target</span>
            {trade.target_price.toFixed(2)}
          </div>
          <div className="modal-detail">
            <span className="modal-detail-label">Gain:Loss ($)</span>
            <span className="positive">{gain}</span>:<span className="negative">{loss}</span>
          </div>
          <div className="modal-detail">
            <span className="modal-detail-label">P&amp;L</span>
            <span className={trade.pnl_dollars >= 0 ? "positive" : "negative"}>
              {trade.pnl_points.toFixed(2)} pts / {fmtMoney(trade.pnl_dollars)}
            </span>
          </div>
          <div className="modal-detail modal-detail-wide">
            <span className="modal-detail-label">Reason</span>
            {trade.reason}
          </div>
        </div>
      </div>
    </div>
  );
}
