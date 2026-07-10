# JJ Strategy — TopStep Paper Trading Bot

Automates the NY-session "high-timeframe reversion, low-timeframe continuation"
strategy described by the YouTuber JJ, and runs it against a **Tradovate demo
(paper) account** — the platform TopStep accounts are traded through.

> Paper/demo trading only. This does not place real-money orders and is not
> investment advice. Verify everything against your own TopStep rules before
> ever considering a funded account.

## How the strategy is encoded

1. **Anchor**: the 9:30 AM ET 1-minute candle (or the pre-news candle on red-folder
   news days) is treated as "fair price."
2. **Phase 1 — Continuation (0–10 min after open)**: trade in the direction of
   the opening candle's body, on the first displacement candle that breaks and
   closes beyond recent structure.
3. **Phase 2 — Mean reversion (10–90 min after open)**: once price has
   displaced away from the open, trade the reversion back toward the open on
   the next displacement/break-of-structure candle in the opposite direction.
4. **Entry trigger**: a "displacement" candle — range notably larger than the
   recent average / previous candle, with small wicks — that closes beyond a
   recent swing high/low (break of structure).
5. **Risk**: fixed 1 : 1.5 R:R (default 25 pt stop / 38 pt target on NQ),
   configurable in `config.yaml`.
6. **Trade caps**: max 3–4 trades/day, stop after 2 consecutive losses, no new
   entries after the configured session cutoff (default 11:00 AM ET).
7. **Daily rate limiter**: not specified in JJ's transcript, so set per
   instruction — trading stops for the day once running P&L hits a **+$1,520
   profit cap** or a **-$1,000 loss cap** (`risk.daily_profit_cap` /
   `risk.daily_loss_cap` in `config.yaml`).

All of this is implemented as an explicit, backtestable state machine in
`jj_bot/strategy.py` — see that file for the exact rules, since "displacement"
and "structure" are inherently a bit subjective in the original video and had
to be turned into concrete thresholds.

## Two ways to run it

### 1. Backtest (start here)

Simulates the strategy against historical 1-minute bars and reports results
**the way JJ says to backtest a prop-firm strategy**: one trade sequence per
day against a trailing-drawdown account, not a naive equity curve. Reports
pass rate against a TopStep-style evaluation.

```bash
pip install -r requirements.txt
python scripts/run_backtest.py --bars data/NQ_1min.csv --account-size 50000
```

Bars CSV needs columns: `timestamp,open,high,low,close,volume` (timestamp in
UTC or with tz info; the bot converts to America/New_York internally).

You can also pull historical bars directly from Tradovate (see below) instead
of supplying a CSV.

### 2. Live paper trading against Tradovate demo

Requires a **Tradovate demo account** and an API application registered at
https://trader.tradovate.com (Settings → API Access). Fill in `.env`:

```
TRADOVATE_ENV=demo
TRADOVATE_USERNAME=...
TRADOVATE_PASSWORD=...
TRADOVATE_APP_ID=...
TRADOVATE_APP_VERSION=1.0
TRADOVATE_CID=...
TRADOVATE_SEC=...
TRADOVATE_DEVICE_ID=jj-bot-01
TRADOVATE_ACCOUNT_NAMES=DEMO12345,DEMO67890
```

`TRADOVATE_ACCOUNT_NAMES` is a comma-separated list — supports trading
**multiple accounts** under one Tradovate login at once (e.g. several
TopStep evals/funded accounts). Leave it blank to trade every active
account on the login. Each account gets its own daily $1,520/$1,000 rate
limiter, so one account hitting its cap doesn't stop the others.

**Before running the full live loop, confirm the connection actually
works** (see "Testing the connection" below) — don't find out your
credentials are wrong at 9:30 AM.

Then run the live strategy loop:

```bash
python scripts/run_live.py --symbol NQ
```

This polls/streams 1-minute bars for the front-month NQ contract from
Tradovate's market data API, runs the same strategy state machine live during
the NY session, and fans out bracket orders (market entry + OCO stop/target)
to every configured account. It refuses to run against a live account
(`TRADOVATE_ENV` must be `demo`).

## Testing the connection / automation

Before trusting the live loop, confirm the whole pipeline — auth, account
resolution, contract lookup, order placement — actually works against your
account(s):

```bash
# See exactly which accounts the bot resolves under your login
python scripts/test_connection.py --list-accounts

# Place one small bracket test order (1 contract, 4pt stop / 6pt target) on
# a specific account, to confirm automation really reaches your paper account
python scripts/test_connection.py --account "DEMO12345" --direction Buy
```

Test trades are logged to the dashboard with `source: "connection_test"` and
shown with a distinct "Test" badge — they're excluded from the success-rate
and P&L stats so they can't skew your real results.

You can also trigger a test trade **from the dashboard itself**: run the bot
API server (`python scripts/run_api_server.py`, defaults to port 8787),
paste its URL into the "Connection & Automation Test" panel at the top of
the dashboard, click **Load Accounts**, pick an account, and click **Send
Test Trade**. This is the same connection test, just from the UI instead of
the CLI — useful once the bot is running on a remote host and you want to
confirm it's alive without SSHing in.

## Dashboard (Vercel)

`dashboard/` is a separate Next.js app you deploy to Vercel as its own
project (Root Directory = `dashboard/`). It shows:

- A "Connection & Automation Test" panel to load your accounts and fire a
  test trade against the bot API, right from the browser
- Success rate % (wins / total trades) — real strategy trades only, test
  trades are excluded
- Total dollars gained / lost, and net P&L
- Rate-limiter status (the $1,520 profit cap / $1,000 loss cap above)
- A full log of every trade the bot took — win or loss, phase, direction,
  setup grade, entry/exit price, P&L, and the reason it entered

Both `scripts/run_backtest.py` and `scripts/run_live.py` write to
`dashboard/data/trades.json` via `jj_bot/trade_logger.py`, which is what the
dashboard reads. See `dashboard/README.md` for local dev + deploy steps.

The dashboard's Test Trade panel and its trade data on Vercel are two
separate concerns: `dashboard/data/trades.json` is bundled at deploy time
(static — needs a redeploy to pick up new trades), while the Test Trade
panel calls `jj_bot/api_server.py` **live**, wherever you're running it (see
`scripts/run_api_server.py` — this needs to run on an always-on host, not
Vercel, since it holds a live broker session).

## Project layout

```
jj_bot/
  config.py          # loads config.yaml + .env
  time_utils.py       # session boundaries, ET conversion
  models.py            # Bar, Trade, Signal dataclasses
  strategy.py          # displacement/BOS detection + state machine (core logic)
  risk_manager.py       # position sizing, daily trade caps, trailing drawdown sim
  trade_logger.py        # writes trade results to dashboard/data/trades.json
  tradovate_client.py   # Tradovate REST/WebSocket client (auth, bars, orders, multi-account)
  test_trade.py           # connection/automation test trade helper
  api_server.py            # FastAPI service backing the dashboard's Test Trade panel
  backtest.py               # prop-firm-style backtest runner
  live_runner.py             # live loop wiring strategy -> Tradovate orders (multi-account fan-out)
scripts/
  run_backtest.py
  run_live.py
  run_api_server.py
  test_connection.py
config.yaml            # strategy + risk parameters
dashboard/              # Next.js dashboard (deploy to Vercel separately)
```

## Disclaimer

This is a best-effort, rules-based encoding of a discretionary strategy
described in a YouTube video. "Displacement," "structure," and "fair price"
are judgment calls in the original video; the thresholds here are reasonable
defaults, not guarantees of the same results. Backtest and paper-trade
extensively before trusting it with anything real, and note that TopStep's
rules generally prohibit unattended/fully automated trading on evaluation and
funded accounts without prior approval — this project is for paper/demo
accounts and strategy research.
