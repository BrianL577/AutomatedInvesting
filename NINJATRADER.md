# Running against NinjaTrader (free sim account)

NinjaTrader gives you a free, no-funding-required simulated futures account
(`Sim101`, or a TopStep demo account) with real NQ market data and an easy
signup. The tradeoff: **NinjaTrader is Windows-only desktop software** —
there's no Linux or headless mode, so unlike Tradovate, you cannot host
this on Railway. The bot has to run on the same Windows machine as
NinjaTrader (or a Windows VPS with NinjaTrader installed and always
running).

`BROKER=ninjatrader` in `.env` selects this broker. Everything else
(strategy engine, dashboard, Strategy Creator, Supabase trade log) is
identical regardless of broker.

## 1. Install NinjaTrader and open Sim101

1. Download NinjaTrader 8 from ninjatrader.com (free) and create an account
   — no funding required, this is just a software license signup.
2. Launch NinjaTrader. It comes with a `Sim101` simulated account by
   default — no separate paper-account activation step needed.
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
small NinjaScript indicator that writes three files for the Python bot to
read: `bars.csv` (live bars only — the trading bot uses this for order
pricing), `history.csv` (the one-time historical replay, used only for
backtesting, kept separate so a live price lookup never sees years-old
data), and `fills.csv`.

1. In NinjaTrader: **New > NinjaScript Editor**.
2. Right-click **Indicators** > **Import** > select
   `ninjatrader/JJBotExporter.cs` from this repo (or open the file and
   paste its contents into a new Indicator).
3. **Compile** (F5). Fix any version-specific API errors if NinjaTrader
   flags them — the `ExecutionUpdate` event signature has changed slightly
   across NinjaTrader 8 releases; check NinjaTrader's NinjaScript docs for
   your installed version if compilation fails.
4. **Before adding the indicator**, set your NQ 1-minute chart's history to
   something small: right-click the chart > **Data Series...** > set "Days
   to load" to ~5-10 days (not years). A large lookback makes NinjaTrader
   replay years of bars before reaching live data, which can freeze the UI
   for a long time — this setting is what actually controls that, not the
   indicator.
5. Add the indicator to the chart: right-click the chart > **Indicators** >
   add **JJBotExporter**.
6. In its Properties, set:
   - **Export Directory**: a folder like `C:\jjbot-export` — this must
     match `NT_EXPORT_DIR` in `.env`.
   - **Account Name**: your sim account (e.g. `DEMO8217187` or `Sim101`) — must match the account name saved on the dashboard's My Accounts page, or `NT_ACCOUNT_NAMES` if set.
   - **Export History**: leave **OFF** on this chart — this is the live
     trading chart; historical export is a separate, optional step (§6).
7. Leave the chart open with the indicator attached and NinjaTrader
   connected — this is what keeps `bars.csv`/`fills.csv` updating live.
   Because "Days to load" is small, `bars.csv` starts filling with
   genuinely live bars within a minute or two, not after a long historical
   replay.

## 4. Configure the bot

```
BROKER=ninjatrader
NT_INCOMING_DIR=C:\Users\<you>\Documents\NinjaTrader 8\incoming
NT_EXPORT_DIR=C:\jjbot-export
NT_ACCOUNT_NAMES=DEMO8217187
NT_INSTRUMENT=NQ 12-26
```

`NT_INSTRUMENT` must be the exact NinjaTrader instrument name for the
current front-month contract (ATI has no contract-lookup command, unlike
Tradovate) — update this each time the front-month contract rolls
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
attached and the chart is receiving live data (market must be open) — note
that `bars.csv` only gets live bars now, so it stays empty until the market
is actually open and printing new bars, even right after attaching.

## 6. Building a growing historical dataset (for backtesting) — optional, separate from live trading

This is entirely optional and doesn't affect the live-trading setup above —
skip it if you just want to trade right now.

`history.csv` only fills when a JJBotExporter instance has **Export
History** turned ON. Do this on a **second, separate chart** — don't turn
it on for your live-trading chart, since a large lookback there is what
causes the freeze you hit earlier.

1. Open a **new** NQ chart (right-click a chart tab → New Chart, or
   Workspaces → new chart window) — leave your live-trading chart alone.
2. Right-click this new chart → **Data Series...** → increase the
   historical lookback as far as your data feed allows (how far back you
   can actually go depends on what NinjaTrader's data provider gives you on
   a sim account).
2. Check what timezone your chart's timestamps are actually in — NinjaTrader
   Tools > General Options > Time Zone (commonly `America/Chicago`, CME's
   exchange timezone, unless you've changed it). Set `NT_BAR_TIMEZONE` in
   `.env` to match — the strategy's session logic depends on this being
   correct, since it needs true UTC under the hood.
3. Add **JJBotExporter** to this new chart too, with the same **Export
   Directory** as the live chart, but **Export History** turned **ON** this
   time.
4. Run the sync script (separately from `run_live.py`, can run alongside
   it):
   ```bash
   python scripts/sync_bars_to_supabase.py
   ```
   It uploads `history.csv` (the backlog from this second chart) once, then
   polls `bars.csv` (the live chart's genuinely live bars) every 30 seconds
   going forward, so as long as this keeps running, the dataset keeps
   growing — a year from now, it'll hold roughly two years of history.
5. The dashboard's Strategy Creator automatically backtests against
   whatever's in Supabase's `bars` table, so no further action is needed
   once this is syncing.

## 7. Running it fully automated

Since NinjaTrader can't run headless on Railway, "fully automated" here
means: leave a Windows machine on, NinjaTrader logged into your account, the
JJBotExporter indicator attached to a live NQ chart, and
`scripts/run_live.py` running continuously on that same machine — with
something restarting it if it crashes, since (unlike Railway) nothing does
that by default here.

### Auto-restart with `run_live_loop.ps1`

`scripts/run_live_loop.ps1` wraps `run_live.py` in a loop: if it exits for
any reason (crash, NT8 hiccup, network blip), it relaunches after 15
seconds, logging every start/stop to `logs\run_live_loop.log`.

Test it manually first:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_live_loop.ps1
```

### Making it survive reboots — Task Scheduler

1. Open **Task Scheduler** (search it in the Start menu).
2. **Action → Create Task...** (not "Basic Task" — the full dialog gives more control).
3. **General** tab:
   - Name: `JJ Bot - NinjaTrader Live Loop`
   - Select **"Run whether user is logged on or not"** if this is a remote
     desktop that might not always have an active session — otherwise
     "Run only when user is logged on" is fine and simpler.
   - Check **"Run with highest privileges"**.
4. **Triggers** tab → **New...**:
   - Begin the task: **At startup** (add a second trigger **At log on** too,
     if you chose "only when logged on" above).
5. **Actions** tab → **New...**:
   - Action: **Start a program**
   - Program/script: `powershell.exe`
   - Add arguments: `-ExecutionPolicy Bypass -File "C:\AutomatedInvesting\scripts\run_live_loop.ps1"`
   - (Adjust the path if this repo isn't at `C:\AutomatedInvesting`.)
6. **Settings** tab:
   - Uncheck **"Stop the task if it runs longer than..."** (default 3 days —
     this needs to run indefinitely).
   - Check **"If the task fails, restart every:"** → 1 minute, a few
     attempts, as an extra safety net on top of the script's own loop.
7. Click OK, enter your Windows password if prompted.
8. Right-click the task → **Run**, to test it fires correctly. Check
   `logs\run_live_loop.log` for the startup line, and confirm NT8's Orders
   tab reflects activity once the market's live.

**Important**: NinjaTrader itself still needs to already be open and logged
in for any of this to work — Task Scheduler restarts the Python bot, not
NinjaTrader. If NT8 also needs to survive a full machine reboot, add its own
Task Scheduler "at startup" entry, or (simpler) just don't reboot the
machine and let Task Scheduler only handle crash-recovery of the Python
side.

A cheap Windows VPS (e.g. Amazon WorkSpaces, a Windows Azure VM, or a
dedicated Windows mini-PC) works if you don't want to keep your own
computer on 24/7 — install NinjaTrader + this repo there instead of trying
to adapt the Railway setup in `RAILWAY.md` (which assumes a broker with a
network API, not a desktop app).

The dashboard (Vercel) and Supabase trade log work exactly the same
regardless of which machine runs the bot — see `README.md`.

## Moving to Tradovate/TopStep later

Nothing about the strategy engine, backtester, or dashboard is
NinjaTrader-specific — switch `BROKER=tradovate` and fill in that broker's
env vars whenever you're ready; everything else keeps working unchanged.
