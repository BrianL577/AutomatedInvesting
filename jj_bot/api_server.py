"""Small always-on HTTP API the Vercel dashboard talks to.

This is NOT hosted on Vercel — Vercel serverless functions can't hold a
persistent broker session and shouldn't hold your Tradovate credentials in
a browser-reachable environment tied to your frontend deploys. Run this on
a small always-on host (a VPS, Railway, Render, Fly.io, or even your own
machine via a tunnel like ngrok/Cloudflare Tunnel) and point the dashboard
at it via the NEXT_PUBLIC_BOT_API_URL env var.

Endpoints:
  GET  /api/health        -> {"ok": true}
  GET  /api/accounts      -> resolves + lists every configured broker account
                              (IBKR by default, or Tradovate if BROKER=tradovate)
  POST /api/test-trade    -> places one small bracket test order, to confirm
                              the automation pipeline is actually wired up
  GET  /api/trades        -> same trade log the dashboard reads directly,
                              exposed here too for convenience/debugging
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import load_config
from .test_trade import list_accounts, run_connection_test
from .trade_logger import TradeLogger

app = FastAPI(title="JJ Strategy Bot API")

_cors_origins = os.getenv("BOT_API_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_origins == "*" else [o.strip() for o in _cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TestTradeRequest(BaseModel):
    account_name: str | None = None
    direction: str = "Buy"  # "Buy" or "Sell"


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/accounts")
def accounts():
    cfg = load_config()
    try:
        names = list_accounts(cfg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "broker": cfg.broker,
        "env": "paper" if cfg.broker == "ibkr" else cfg.tradovate.env,
        "accounts": [{"name": n, "active": True} for n in names],
    }


@app.post("/api/test-trade")
def test_trade(req: TestTradeRequest):
    cfg = load_config()
    if cfg.broker == "tradovate" and cfg.tradovate.env != "demo":
        raise HTTPException(status_code=400, detail="TRADOVATE_ENV must be 'demo'. Refusing to place a test trade.")
    if req.direction not in ("Buy", "Sell"):
        raise HTTPException(status_code=400, detail="direction must be 'Buy' or 'Sell'")
    try:
        result = run_connection_test(cfg, account_name=req.account_name, direction=req.direction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "accounts": result.accounts,
        "tested_account": result.tested_account,
        "contract_symbol": result.contract_symbol,
        "order_response": result.order_response,
    }


@app.get("/api/trades")
def trades():
    cfg = load_config()
    dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
    logger = TradeLogger(dollar_per_point=dollar_per_point)
    return {"trades": logger._read()}
