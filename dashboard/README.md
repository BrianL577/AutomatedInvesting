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

Two modes, auto-detected:

- **Live (Supabase)** — if `NEXT_PUBLIC_SUPABASE_URL` and
  `NEXT_PUBLIC_SUPABASE_ANON_KEY` are set, the dashboard reads trades
  straight from Supabase on every page load. This is what the Python bot
  writes to when `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` are set on its
  end (see `supabase/schema.sql` and the root `RAILWAY.md`). Header shows
  "● Live (Supabase)".
- **Static demo file** — otherwise, falls back to `data/trades.json`,
  bundled at deploy time. Only updates when you redeploy. Header shows "○
  Static demo file".

`jj_bot/trade_logger.py` writes to both the local JSON file and Supabase
(when configured) on every closed trade, from both `scripts/run_backtest.py`
and `scripts/run_live.py`.

## Accounts & Authentication

Sign in/sign up (email + password, Supabase Auth) is required to use the
Strategy Creator and the My Accounts page:

- **My Accounts** (`/accounts`) — save the Tradovate account name(s) you
  trade (e.g. `DEMO12345`). Private to you via Postgres row-level security
  (`auth.uid() = user_id`) — no other user can see or edit your accounts.
  Only the account *name* is stored; your Tradovate login/password/CID/SEC
  stay in your bot host's own env vars, never in the database. The page
  gives you a ready-to-copy `TRADOVATE_ACCOUNT_NAMES` value for Railway.
- **Strategies** are private per user the same way — each strategy row is
  owned by exactly one `auth.uid()`, enforced by RLS, not just application
  logic. The built-in JJ default strategy is shown to everyone (it's not a
  database row).

Setup: run `supabase/schema.sql` (fresh project) or, if you already ran an
earlier version of it, `supabase/migrations/002_user_scoped_strategies_and_accounts.sql`
(existing project). No extra env vars beyond `NEXT_PUBLIC_SUPABASE_URL` /
`NEXT_PUBLIC_SUPABASE_ANON_KEY` are needed for auth — Supabase Auth is
enabled by default on every project. In Supabase → Authentication →
Providers, Email is on by default; toggle "Confirm email" under
Authentication → Settings depending on whether you want email verification
before first sign-in.

## Connection & Automation Test panel

The card at the top of the dashboard lets you point at a running
`jj_bot/api_server.py` instance (see the root README), load its resolved
Tradovate accounts, and fire a small test trade — useful for confirming a
remotely-hosted bot (e.g. on Railway) is actually alive and wired up
correctly without SSHing in. Test trades are tagged and excluded from the
stats above.

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
4. Framework preset: Next.js (auto-detected).
5. (Optional, for live data) Add `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` in Environment Variables — see
   `.env.example` in this directory. Use the **anon** key only, never the
   service role key.
6. Deploy.
