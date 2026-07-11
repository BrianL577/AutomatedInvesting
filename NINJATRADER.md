# Running against NinjaTrader (free sim account)

NinjaTrader gives you a free, no-funding-required simulated futures account
(`Sim101`) with real NQ market data and a much friendlier signup than IBKR.
The tradeoff: **NinjaTrader is Windows-only desktop software** — there's no
Linux or headless mode, so unlike IBKR/Tradovate, you cannot host this on
Railway. The bot has to run on the same Windows machine as NinjaTrader (or a
Windows VPS with NinjaTrader installed and always running).

`BROKER=ninjatrader` in `.env` selects this broker. Everything else
(strategy engine, dashboard, Strategy Creator, Supabase trade log) is
identical regardless of broker.

## 1. Install NinjaTrader and open Sim101

1. Download NinjaTrader 8 from ninjatrader.com (free) and create an account
   — no funding required, this is just a software license signup.
2. Launch NinjaTrader. It comes with a `Sim101` simulated account by
   default — no separate paper-account activation step like IBKR.
3. Open a chart for the NQ front-month contract (e.g. search "NQ 12-26" —
   use whatever the current front-month contract is) at a 1-minute
   timeframe.

## 2. Enable ATI (Automated Trading Interface)

1. In NinjaTrader's Control Center: **Tools > Options > Automated Trading
   Interface**.
2. Check **"ATI Enabled"**.
3. Note the **incoming folder path** shown there (default:
   `%USERPROFILE%\Documents\NinjaTrader 8\incoming`) — this goes in
   `NT_INCOMING_DIR`.

## 3. Install the companion NinjaScript exporter

ATI only handles order placement (one-way, file-drop). For live bars and
fill confirmations, this repo includes `ninjatrader/JJBotExporter.cs`, a
small NinjaScript indicator that writes `bars.csv` and `fills.csv` for the
Python bot to read.

1. In NinjaTrader: **New > NinjaScript Editor**.
2. Right-click **Indicators** > **Import** > select
   `ninjatrader/JJBotExporter.cs` from this repo (or open the file and
   paste its contents into a new Indicator).
3. **Compile** (F5). Fix any version-specific API errors if NinjaTrader
   flags them — the `ExecutionUpdate` event signature has changed slightly
   across NinjaTrader 8 releases; check NinjaTrader's NinjaScript docs for
   your installed version if compilation fails.
4. Add the indicator to your NQ 1-minute chart: right-click the chart >
   **Indicators** > add **JJBotExporter**.
5. In its Properties, set:
   - **Export Directory**: a folder like `C:\jjbot-export` — this must
     match `NT_EXPORT_DIR` in `.env`.
   - **Account Name**: `Sim101` (must match `NT_ACCOUNT_NAME`).
6. Leave the chart open with the indicator attached — this is what keeps
   `bars.csv`/`fills.csv` updating live, similar to how IB Gateway has to
   stay logged in for IBKR.

## 4. Configure the bot

```
BROKER=ninjatrader
NT_INCOMING_DIR=C:\Users\<you>\Documents\NinjaTrader 8\incoming
NT_EXPORT_DIR=C:\jjbot-export
NT_ACCOUNT_NAME=Sim101
NT_INSTRUMENT=NQ 12-26
```

`NT_INSTRUMENT` must be the exact NinjaTrader instrument name for the
current front-month contract (ATI has no contract-lookup command, unlike
IBKR/Tradovate) — update this each time the front-month contract rolls
(quarterly for NQ).

## 5. Test the connection

Run this on the same Windows machine, with NinjaTrader open, Sim101 active,
and the JJBotExporter indicator attached to a live NQ chart:

```bash
python scripts/test_connection.py --list-accounts
python scripts/test_connection.py --account "Sim101" --direction Buy
```

If `list-accounts` fails, check `NT_INCOMING_DIR` points at a real,
existing folder and ATI is enabled. If the test order doesn't fill or
`bars.csv` stays empty, confirm the JJBotExporter indicator is actually
attached and the chart is receiving live data (market must be open).

## 6. Running it fully automated

Since NinjaTrader can't run headless on Railway, "fully automated" here
means: leave a Windows machine on, NinjaTrader logged into Sim101, the
JJBotExporter indicator attached to a live NQ chart, and
`python scripts/run_live.py --symbol NQ` running continuously on that same
machine. A cheap Windows VPS (e.g. Amazon WorkSpaces, a Windows Azure VM,
or a dedicated Windows mini-PC) works if you don't want to keep your own
computer on 24/7 — install NinjaTrader + this repo there instead of trying
to adapt the Railway setup in `RAILWAY.md` (which assumes a broker with a
network API, not a desktop app).

The dashboard (Vercel) and Supabase trade log work exactly the same
regardless of which machine runs the bot — see `README.md`.

## Moving to Tradovate/TopStep later

Nothing about the strategy engine, backtester, or dashboard is
NinjaTrader-specific — switch `BROKER=tradovate` (or back to `ibkr`) and
fill in that broker's env vars whenever you're ready; everything else keeps
working unchanged.
