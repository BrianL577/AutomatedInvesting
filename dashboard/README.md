# JJ Strategy Dashboard

Next.js dashboard for the JJ NY-session paper trading bot. Deploy this
directory to Vercel as its own project (set the Vercel project's **Root
Directory** to `dashboard/`).

## What it shows

- Success rate % (wins / total trades)
- Total dollars gained, total dollars lost, net P&L
- The daily rate-limiter status: max gain cap **$1,520** / max loss cap
  **$1,000** — trading stops for the day once either is hit
- A full trade log: entry/exit time, phase (continuation/reversion),
  direction, setup grade, entry/exit price, win/loss, P&L in points and
  dollars, and the reason the strategy engine took the trade

## Data source

The dashboard reads `data/trades.json`, which is written by the Python bot:

- `jj_bot/trade_logger.py` — `TradeLogger.log_trade()` appends each closed
  trade here
- `scripts/run_backtest.py` calls this automatically on every run
- `scripts/run_live.py` (live paper trading against Tradovate demo) calls
  this whenever a position closes

On Vercel, `data/trades.json` is bundled at deploy time — the dashboard
updates whenever you redeploy after the bot writes new trades (e.g. commit
the updated file and push, or wire a small sync job/API if you want
real-time updates without a redeploy).

## Local development

```bash
cd dashboard
npm install
npm run dev
```

Then open http://localhost:3000.

## Deploying to Vercel

1. Push this repo to GitHub (already done).
2. In Vercel, "Add New Project" → import the repo.
3. Set **Root Directory** to `dashboard`.
4. Framework preset: Next.js (auto-detected). No environment variables
   required for the dashboard itself.
5. Deploy.
