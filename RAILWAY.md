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

In each Railway service → Variables, add everything from `.env.example`:

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

SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...

BOT_API_HOST=0.0.0.0
BOT_API_PORT=8787
BOT_API_CORS_ORIGINS=https://your-dashboard.vercel.app
```

**Never put these in chat, a commit, or anywhere public** — set them
directly in Railway's Variables UI.

## 4. Deploy and verify

1. Deploy both services.
2. Check the worker's logs — it should log `Authenticating with
   Tradovate...`, then `Trading N account(s): [...]`, then `Streaming live
   bars.`
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

- The worker process holds a WebSocket connection to Tradovate's market
  data feed and runs for as long as Railway keeps the service alive.
  Railway restarts crashed services automatically — that's the main reason
  to run this here instead of a machine you might close.
- This trades every account listed in `TRADOVATE_ACCOUNT_NAMES` under one
  Tradovate login. Each account has its own $1,520 profit cap / $1,000 loss
  cap (`config.yaml` → `risk.daily_profit_cap` / `daily_loss_cap`) and stops
  trading independently once it hits either.
- `TRADOVATE_ENV` must stay `demo` — the client refuses to run otherwise.
