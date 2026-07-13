// JJBotExporter — NinjaScript indicator that exports closed 1-minute bars
// and order fills for the configured account to CSV files that the Python
// bot (jj_bot/ninjatrader_client.py) tails. Install via NinjaTrader 8:
// New > NinjaScript Editor > Indicators > right-click > Import > this file
// (or paste into a new Indicator), then Compile (F5), then add it to a
// 1-minute chart of your NQ contract. See NINJATRADER.md for full setup.
//
// Writes closed bars to TWO separate files, split by NinjaScript's own
// State.Historical vs State.Realtime distinction:
//   history.csv — the one-time historical replay (as far back as the
//                 chart's "Days to load" setting goes), for backtesting.
//   bars.csv    — only genuinely live bars going forward, for the live
//                 trading bot (jj_bot/ninjatrader_client.py tails this one
//                 for order pricing — it must never contain years-old
//                 replay data, or price lookups return stale numbers).
// scripts/sync_bars_to_supabase.py reads both (history.csv once, bars.csv
// continuously) so the backtesting dataset still grows over time.
//
// IMPORTANT — for the LIVE TRADING chart, keep "Days to load" small (e.g.
// 5-10 days) and leave "Export History" OFF (default). A large lookback
// makes NinjaTrader replay years of 1-minute bars before it reaches live
// data at all, which can freeze the UI for a long time and delays
// bars.csv — that replay time is a NinjaTrader chart setting, this
// indicator can't shortcut it. If you want a growing history.csv for
// backtesting, attach a SECOND instance of this indicator to a separate
// chart with "Export History" ON and as large a lookback as you want —
// keep that entirely separate from the live-trading chart/setup.
//
// Set ExportDir below (or via the indicator's Properties panel) to match
// NT_EXPORT_DIR in your .env — must be the same folder on this machine.

#region Using declarations
using System;
using System.ComponentModel.DataAnnotations;
using System.IO;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class JJBotExporter : Indicator
    {
        [NinjaScriptProperty]
        [Display(Name = "Export Directory", Order = 1, GroupName = "Parameters")]
        public string ExportDir { get; set; } = @"C:\jjbot-export";

        [NinjaScriptProperty]
        [Display(Name = "Account Name", Order = 2, GroupName = "Parameters")]
        public string AccountName { get; set; } = "Sim101";

        [NinjaScriptProperty]
        [Display(Name = "Export History", Order = 3, GroupName = "Parameters",
            Description = "OFF for the live-trading chart (default). Only turn ON for a " +
                           "separate chart instance dedicated to building history.csv for backtesting.")]
        public bool ExportHistory { get; set; } = false;

        private string barsPath;
        private string historyPath;
        private string fillsPath;
        private Account account;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Exports closed bars and fills for the JJ strategy bot.";
                Name = "JJBotExporter";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
            }
            else if (State == State.Configure)
            {
                Directory.CreateDirectory(ExportDir);
                barsPath = Path.Combine(ExportDir, "bars.csv");
                historyPath = Path.Combine(ExportDir, "history.csv");
                fillsPath = Path.Combine(ExportDir, "fills.csv");
            }
            else if (State == State.DataLoaded)
            {
                account = Account.All.Find(a => a.Name == AccountName);
                if (account != null)
                    account.ExecutionUpdate += OnExecutionUpdate;
            }
            else if (State == State.Terminated)
            {
                if (account != null)
                    account.ExecutionUpdate -= OnExecutionUpdate;
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < 1)
                return;

            string line = string.Format(
                "{0:yyyy-MM-ddTHH:mm:ss},{1},{2},{3},{4},{5}\n",
                Time[0], Open[0], High[0], Low[0], Close[0], Volume[0]);

            // State.Historical = the one-time replay of past bars on
            // attach; State.Realtime = genuinely live bars going forward.
            // Keeping these in separate files means the live trading bot's
            // price lookups (bars.csv) never see years-old replay data.
            // Skipping the write entirely when ExportHistory is off avoids
            // the extra disk I/O during a large replay on the live chart.
            if (State == State.Historical)
            {
                if (ExportHistory)
                    File.AppendAllText(historyPath, line);
            }
            else
            {
                File.AppendAllText(barsPath, line);
            }
        }

        private void OnExecutionUpdate(object sender, ExecutionEventArgs e)
        {
            string line = string.Format(
                "{0:yyyy-MM-ddTHH:mm:ss},{1},{2},{3},{4},{5}\n",
                e.Execution.Time, e.Execution.Order.Name, AccountName,
                e.Execution.Order.OrderAction, e.Execution.Price, e.Execution.Quantity);
            File.AppendAllText(fillsPath, line);
        }
    }
}
