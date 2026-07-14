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
  POST /api/ai-job        -> starts a long-running Anthropic Messages API
                              call in a background thread and returns a
                              job_id immediately. Exists because Vercel
                              Hobby hard-caps serverless functions at 10s
                              (ignoring any maxDuration set in the Next.js
                              route code) while Claude calls with extended
                              thinking routinely take longer — this host has
                              no such limit, so the dashboard dispatches the
                              slow call here and polls GET /api/ai-job/{id}
                              instead of waiting on it inline. This endpoint
                              is a dumb proxy: it forwards whatever request
                              body it's given straight to
                              api.anthropic.com/v1/messages and hands back
                              the raw response — all prompt/schema logic
                              stays in the dashboard's TypeScript code, not
                              duplicated here.
  GET  /api/ai-job/{id}   -> poll a job started above: {"status": "pending"}
                              while running, or {"status": "done", "result":
                              <raw Anthropic response>} / {"status": "error",
                              "error": "..."} once finished.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

import requests
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


class AIJobRequest(BaseModel):
    # Raw Anthropic Messages API request body (model, max_tokens, system,
    # messages, thinking, output_config, ...) — passed through verbatim, see
    # module docstring.
    body: dict[str, Any]


_AI_JOBS: dict[str, dict[str, Any]] = {}
_AI_JOBS_LOCK = threading.Lock()
_AI_JOB_TTL_SECONDS = 30 * 60  # prune finished jobs after 30 min


def _prune_ai_jobs() -> None:
    cutoff = time.time() - _AI_JOB_TTL_SECONDS
    stale = [jid for jid, job in _AI_JOBS.items() if job.get("created_at", 0) < cutoff]
    for jid in stale:
        del _AI_JOBS[jid]


def _run_ai_job(job_id: str, body: dict[str, Any], api_key: str) -> None:
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            # Generous — this background thread isn't bound by any per-request
            # platform timeout the way the dashboard's own serverless
            # functions are, so there's no need to cut this close.
            timeout=280,
        )
        resp.raise_for_status()
        result = resp.json()
        with _AI_JOBS_LOCK:
            created_at = _AI_JOBS.get(job_id, {}).get("created_at", time.time())
            _AI_JOBS[job_id] = {"status": "done", "result": result, "created_at": created_at}
    except requests.RequestException as exc:
        resp_obj = getattr(exc, "response", None)
        detail = resp_obj.text if resp_obj is not None else str(exc)
        status_code = resp_obj.status_code if resp_obj is not None else None
        with _AI_JOBS_LOCK:
            created_at = _AI_JOBS.get(job_id, {}).get("created_at", time.time())
            _AI_JOBS[job_id] = {
                "status": "error",
                "error": detail,
                "status_code": status_code,
                "created_at": created_at,
            }


@app.post("/api/ai-job")
def create_ai_job(req: AIJobRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not set on the bot API host.")
    job_id = uuid.uuid4().hex
    with _AI_JOBS_LOCK:
        _prune_ai_jobs()
        _AI_JOBS[job_id] = {"status": "pending", "created_at": time.time()}
    threading.Thread(target=_run_ai_job, args=(job_id, req.body, api_key), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/ai-job/{job_id}")
def get_ai_job(job_id: str):
    with _AI_JOBS_LOCK:
        job = _AI_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job_id")
    return job
