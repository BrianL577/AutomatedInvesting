# Deploying the live trading loop to Railway

This runs `scripts/run_live.py` continuously so the bot actually trades your
paper account during NY session hours, without depending on this chat
session or any machine you have to keep open. Optionally also deploy
`scripts/run_api_server.py` so the dashboard's Test Trade panel has
something to talk to.

## 1. Set up Supabase first (recommended)

Without Supabase, the dashboard only shows trades as of its last deploy —
with it, trades appear live. See `supabase/schema.sql`:

1. Create a project at supabase.com.
2. Project → SQL Editor → New query → paste `supabase/schema.sql` → Run.
3. Project → Settings → API: copy the **Project URL**, the **anon/public
   key**, and the **service_role key** (keep the service role key secret —
   it bypasses row-level security).

## 2. Create the Railway project

1. https://railway.app → New Project → Deploy from GitHub repo →
   `BrianL577/AutomatedInvesting`.
2. Railway auto-detects Python via `requirements.txt` (Nixpacks).
3. This repo's `Procfile` defines two process types — `worker` (the live
   trading loop) and `web` (the test-trade API). Create **two services**
   from the same repo:
   - **Trading worker**: Settings → Deploy → Custom Start Command:
     `python scripts/run_live.py`
   - **Bot API** (optional, for the dashboard's Test Trade panel):
     Settings → Deploy → Custom Start Command:
     `python scripts/run_api_server.py`, and note the public URL Railway
     assigns it (Settings → Networking → Generate Domain) — you'll paste
     this into the dashboard later.

## 3. Set environment variables (on both services)

**If using IBKR (default, `BROKER=ibkr`)**: you also need an IB Gateway
process reachable from the worker — read `IBKR.md` first, since this
usually means a *third* Railway service (a community IB Gateway+IBC Docker
image) that the worker connects to over Railway's private networking.
`IBKR_HOST` then points at that service's internal hostname, not
`127.0.0.1`.

In each Railway service → Variables, add everything from `.env.example`:

```
BROKER=ibkr

IBKR_HOST=<your ib-gateway service's internal hostname>
IBKR_PORT=4002
IBKR_CLIENT_ID=1
IBKR_ACCOUNT_NAMES=DU1234567

SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...

BOT_API_HOST=0.0.0.0
BOT_API_PORT=8787
BOT_API_CORS_ORIGINS=https://your-dashboard.vercel.app

# Enables /api/ai-job on the Bot API service — the dashboard's AI strategy
# chat dispatches its (slow, extended-thinking) Claude calls here instead of
# running them inline on Vercel, since Vercel Hobby hard-caps serverless
# functions at 10s. Same key you'd put in the dashboard's own
# ANTHROPIC_API_KEY — get one at https://platform.claude.com/. Set the
# dashboard's BOT_API_URL (Vercel env var, see dashboard/.env.example) to
# this Railway service's public URL.
ANTHROPIC_API_KEY=

# Crash alert email (optional, but recommended for an unattended worker)
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<Gmail App Password from myaccount.google.com/apppasswords>
ALERT_EMAIL_TO=you@gmail.com
```

Or, once you've funded a live Tradovate account and bought API access:

```
BROKER=tradovate
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

**Never put these in chat, a commit, or anywhere public** — set them
directly in Railway's Variables UI.

## 4. Deploy and verify

1. Deploy both/all services.
2. Check the worker's logs — for IBKR it should log `Connecting to IBKR at
   ...`, then `Trading N paper account(s): [...]`, then `Streaming live
   bars.` (Tradovate: `Authenticating with Tradovate...` instead.)
3. Before market open, run a connection test to confirm the pipeline
   actually reaches your account (from your own machine, pointed at the
   same `.env` values, or via the Bot API service once deployed):
   ```
   python scripts/test_connection.py --list-accounts
   ```
4. On Vercel (the dashboard project), add:
   ```
   NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=...   (the anon key, NOT the service role key)
   ```
   and redeploy. The dashboard header will show "● Live (Supabase)" once
   it's picking up live data instead of the static file.
5. If you deployed the Bot API service, paste its public URL into the
   dashboard's "Connection & Automation Test" panel to use the Test Trade
   button from the browser.

## Notes

- The worker process holds a live connection to your broker (IB Gateway
  socket, or Tradovate's WebSocket market data feed) and runs for as long
  as Railway keeps the service alive. Railway restarts crashed services
  automatically — that's the main reason to run this here instead of a
  machine you might close.
- This trades every account listed in `IBKR_ACCOUNT_NAMES` /
  `TRADOVATE_ACCOUNT_NAMES`. Each account has its own $1,520 profit cap /
  $1,000 loss cap (`config.yaml` → `risk.daily_profit_cap` /
  `daily_loss_cap`) and stops trading independently once it hits either.
- Paper only: IBKR refuses to run if any resolved account isn't a paper
  account (IDs must start with `DU`/`DF`); `TRADOVATE_ENV` must stay
  `demo`.
