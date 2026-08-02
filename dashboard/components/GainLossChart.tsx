// Gain vs loss comparison: gain = cash payouts actually received from funded
// accounts, loss = fees spent on evals (mostly the ones that busted before
// paying out). Two-bar diverging comparison — blue/red per the diverging-pair
// palette (not green/red: that pair fails colorblind-safety validation, and
// gain/loss here is a polarity, not a categorical identity). Always
// direct-labeled so color is never the only signal.
function fmt(n: number): string {
  return `$${Math.round(Math.abs(n)).toLocaleString()}`;
}

export default function GainLossChart({ gain, loss, title = "Gain vs. Loss" }: { gain: number; loss: number; title?: string }) {
  const max = Math.max(gain, loss, 1);
  const gainPct = Math.max((gain / max) * 100, gain > 0 ? 2 : 0);
  const lossPct = Math.max((loss / max) * 100, loss > 0 ? 2 : 0);
  const net = gain - loss;

  return (
    <div className="gainloss-chart">
      <div className="gainloss-header">
        <h3>{title}</h3>
        <span className={`gainloss-net ${net >= 0 ? "positive" : "negative"}`}>
          Net {net >= 0 ? "+" : "-"}
          {fmt(net)}
        </span>
      </div>
      <div className="gainloss-row" title={`Gained from funded payouts: ${fmt(gain)}`}>
        <span className="gainloss-label">
          <span className="gainloss-dot gainloss-dot-gain" aria-hidden="true" />
          Gained (funded payouts)
        </span>
        <div className="gainloss-track">
          <div className="gainloss-bar gainloss-bar-gain" style={{ width: `${gainPct}%` }} />
        </div>
        <span className="gainloss-value positive">{fmt(gain)}</span>
      </div>
      <div className="gainloss-row" title={`Lost to eval fees: ${fmt(loss)}`}>
        <span className="gainloss-label">
          <span className="gainloss-dot gainloss-dot-loss" aria-hidden="true" />
          Lost (eval fees)
        </span>
        <div className="gainloss-track">
          <div className="gainloss-bar gainloss-bar-loss" style={{ width: `${lossPct}%` }} />
        </div>
        <span className="gainloss-value negative">{fmt(loss)}</span>
      </div>
    </div>
  );
}
