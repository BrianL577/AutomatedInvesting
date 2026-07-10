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
TRADOVATE_ACCOUNT_NAME=DEMO...
```

Then run:

```bash
python scripts/run_live.py --symbol NQ
```

This polls/streams 1-minute bars for the front-month NQ contract from
Tradovate's market data API, runs the same strategy state machine live during
the NY session, and places bracket orders (market entry + OCO stop/target)
against your **demo** account only. It refuses to run against a live account
(`TRADOVATE_ENV` must be `demo`).

## Dashboard (Vercel)

`dashboard/` is a separate Next.js app you deploy to Vercel as its own
project (Root Directory = `dashboard/`). It shows:

- Success rate % (wins / total trades)
- Total dollars gained / lost, and net P&L
- Rate-limiter status (the $1,520 profit cap / $1,000 loss cap above)
- A full log of every trade the bot took — win or loss, phase, direction,
  setup grade, entry/exit price, P&L, and the reason it entered

Both `scripts/run_backtest.py` and `scripts/run_live.py` write to
`dashboard/data/trades.json` via `jj_bot/trade_logger.py`, which is what the
dashboard reads. See `dashboard/README.md` for local dev + deploy steps.

## Project layout

```
jj_bot/
  config.py          # loads config.yaml + .env
  time_utils.py       # session boundaries, ET conversion
  models.py            # Bar, Trade, Signal dataclasses
  strategy.py          # displacement/BOS detection + state machine (core logic)
  risk_manager.py       # position sizing, daily trade caps, trailing drawdown sim
  trade_logger.py        # writes trade results to dashboard/data/trades.json
  tradovate_client.py   # Tradovate REST/WebSocket client (auth, bars, orders)
  backtest.py            # prop-firm-style backtest runner
  live_runner.py          # live loop wiring strategy -> Tradovate orders
scripts/
  run_backtest.py
  run_live.py
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
