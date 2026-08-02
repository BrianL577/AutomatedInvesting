"use client";

// Free TradingView embed for glancing at live NQ price action next to the
// bot's stats — view only, no API key, no account link, doesn't talk to the
// bot in any way.
export default function MarketChart() {
  const src =
    "https://s.tradingview.com/embed-widget/advanced-chart/?locale=en#" +
    encodeURIComponent(
      JSON.stringify({
        symbol: "CME_MINI:NQ1!",
        interval: "5",
        timezone: "America/New_York",
        theme: "dark",
        style: "1",
        withdateranges: true,
        hide_side_toolbar: false,
        allow_symbol_change: true,
        autosize: true,
      })
    );

  return (
    <div className="chart-panel">
      <div className="chart-panel-header">
        <h2>Market — NQ Futures</h2>
        <span className="chart-panel-note">Live chart via TradingView, view only</span>
      </div>
      <iframe
        className="chart-iframe"
        src={src}
        title="NQ futures chart"
        frameBorder="0"
        allowTransparency
        scrolling="no"
      />
    </div>
  );
}
