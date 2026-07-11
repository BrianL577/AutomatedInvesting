// JJBotExporter — NinjaScript indicator that exports closed 1-minute bars
// and order fills for the configured account to CSV files that the Python
// bot (jj_bot/ninjatrader_client.py) tails. Install via NinjaTrader 8:
// New > NinjaScript Editor > Indicators > right-click > Import > this file
// (or paste into a new Indicator), then Compile (F5), then add it to a
// 1-minute chart of your NQ contract. See NINJATRADER.md for full setup.
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

        private string barsPath;
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
            if (CurrentBar < 1 || State != State.Realtime)
                return;

            string line = string.Format(
                "{0:yyyy-MM-ddTHH:mm:ss},{1},{2},{3},{4},{5}\n",
                Time[0], Open[0], High[0], Low[0], Close[0], Volume[0]);
            File.AppendAllText(barsPath, line);
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
