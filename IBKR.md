# Running against Interactive Brokers (free paper trading)

Tradovate hides API key generation behind a funded live account ($1,000
minimum) plus a $25/mo API Access add-on — eval and free-trial accounts get
a blank screen. Interactive Brokers' paper trading account is **free, needs
no funding, and its API works on paper accounts immediately.** The tradeoff:
IBKR has no hosted REST API. You connect to a **TWS or IB Gateway process
that has to be running and logged in** — something has to keep that process
alive, which is the one extra piece of infrastructure this path needs.

`BROKER=ibkr` is the default in `.env.example`. Everything else (strategy
engine, dashboard, Strategy Creator, Supabase trade log) is identical
regardless of broker.

## 1. Get an IBKR paper trading account

1. Sign up at interactivebrokers.com (a real account signup, but the paper
   trading sub-account is free and activates immediately — no deposit
   required to use it).
2. Once your account is approved, log into **Client Portal** at least once
   — this activates paper trading (account ID starting with `DU`).
3. Note your **paper account ID** (e.g. `DU1234567`) — you'll enter this in
   the dashboard's My Accounts page (or `IBKR_ACCOUNT_NAMES`).

## 2. Run TWS or IB Gateway (the part that has to stay logged in)

You have three options, roughly in order of how "always-on" you need this
to be:

### Option A — Your own machine (simplest, good for testing)

1. Download **IB Gateway** (lighter than full TWS) from IBKR's website.
2. Log in with your IBKR **paper trading** username/password (Client Portal
   login, not a separate paper-specific login — IB Gateway asks you to pick
   paper vs live at login).
3. In Configuration → Settings → API → Settings: enable "Enable ActiveX and
   Socket Clients", set the paper trading socket port (default **4002**),
   and add `127.0.0.1` to trusted IPs.
4. Leave it running. `IBKR_HOST=127.0.0.1`, `IBKR_PORT=4002`.

This only works while your machine is on and IB Gateway is logged in — fine
for development, not for 24/7 automated trading.

### Option B — A small always-on VPS with a VNC/remote desktop

Run IB Gateway on a cheap VPS (DigitalOcean, etc.) with a lightweight
desktop (e.g. via `xfce4` + `x11vnc` or `tigervnc`), so you can log in
remotely and IB Gateway stays running headless otherwise. More setup than
Option A, but genuinely always-on.

### Option C — Headless via a community IB Gateway + IBC Docker image (Railway)

**Recommended for the Railway setup described in `RAILWAY.md`.** IBC
("IBController") is a well-known open-source tool that automates IB
Gateway's login (including the 2FA/re-auth prompts it periodically shows)
so it can run with no display attached. Several community Docker images
package IB Gateway + IBC together (search "ib-gateway docker ibc" — pick
one with recent activity and read its README, since these are third-party
images, not something Anthropic or this repo maintains).

General shape on Railway:

1. Deploy the IB Gateway+IBC image as its own Railway service, with your
   IBKR paper login credentials as env vars (per that image's README —
   varies by image, but typically `TWS_USERID` / `TWS_PASSWORD` /
   `TRADING_MODE=paper`).
2. Expose its socket port (4002 for paper) **internally only** — do not
   expose IB Gateway's port publicly; only your bot worker service should
   reach it, over Railway's private networking.
3. Deploy this repo's worker service (`python scripts/run_live.py`) as a
   separate Railway service, with `IBKR_HOST` set to the gateway service's
   internal Railway hostname and `IBKR_PORT=4002`.
4. IBKR requires periodic re-authentication (roughly every ~24h, or after
   restarts) even with IBC automating most of it — read the image's docs
   on how it handles this (some support 2FA push notification
   auto-approval; without that, you may need to occasionally re-approve
   login from your phone). This is an IBKR platform constraint, not
   something this repo's code can remove.

## 3. Configure the bot

```
BROKER=ibkr
IBKR_HOST=<gateway host — 127.0.0.1 locally, or the Railway internal hostname>
IBKR_PORT=4002
IBKR_CLIENT_ID=1
IBKR_ACCOUNT_NAMES=DU1234567
```

`IBKR_CLIENT_ID` must be a different integer for each simultaneous
connection to the same gateway (e.g. the live worker uses `1`, a manual
`test_connection.py` run uses `2`) — IB Gateway rejects a second connection
reusing an in-use client ID.

## 4. Test the connection

```bash
python scripts/test_connection.py --list-accounts
python scripts/test_connection.py --account "DU1234567" --direction Buy
```

If this fails with a connection error, IB Gateway isn't running/reachable
at `IBKR_HOST:IBKR_PORT`. If it connects but the account list is empty,
you're not logged into a paper account. If placing the test trade fails on
`get_last_price`, your account may need a CME market data subscription —
check Client Portal → Settings → User Settings → Market Data Subscriptions
(delayed data is often sufficient and free; real-time CME futures data may
require a paid subscription even on paper accounts, since IBKR ties market
data entitlements to your account regardless of paper/live).

## Multiple accounts

IBKR paper trading typically gives you one paper account per login. If you
need to test the strategy across multiple distinct paper accounts, you'd
need multiple IBKR logins (each with its own IB Gateway instance and
client ID) — this is a heavier setup than Tradovate's multi-account-per-
login model. For most users, one IBKR paper account is enough to validate
the automation end-to-end.

## Moving to Tradovate/TopStep later

Nothing about the strategy engine, backtester, or dashboard is IBKR-specific
— `jj_bot/tradovate_client.py` and `jj_bot/live_runner.py` are still in the
repo. If you later fund a live Tradovate account and buy API access, set
`BROKER=tradovate` and fill in the `TRADOVATE_*` env vars — everything else
keeps working unchanged.
