"""Loads strategy/risk config from config.yaml and credentials from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fetch_saved_account_names() -> list[str]:
    """Pulls account names saved via the dashboard's "My Accounts" page
    (Supabase `tradovate_accounts` table — used for any broker's account
    names, not just Tradovate's) instead of requiring them to be
    hand-copied into an env var. This assumes a single-operator bot: it
    reads every saved row regardless of which dashboard user added it. If
    SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY aren't set, returns []."""
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        return []
    try:
        resp = requests.get(
            f"{url}/rest/v1/tradovate_accounts",
            params={"select": "account_name", "order": "created_at.asc"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=10,
        )
        resp.raise_for_status()
        return [row["account_name"] for row in resp.json()]
    except requests.RequestException:
        return []


@dataclass
class StrategyConfig:
    timezone: str
    session_open: str
    continuation_end_minutes: int
    reversion_end_minutes: int
    hard_cutoff: str
    min_extension_points: float
    displacement_size_ratio: float
    displacement_prev_ratio: float
    max_wick_ratio: float
    structure_lookback: int
    swing_strength: int
    break_buffer_points: float = 1.0


@dataclass
class RiskConfig:
    stop_points: float
    target_points: float
    max_trades_per_day: int
    stop_after_consecutive_losses: int
    contracts_per_trade: int
    daily_profit_cap: float = 1520.0
    daily_loss_cap: float = 1000.0


@dataclass
class InstrumentConfig:
    symbol: str
    tick_size: float
    tick_value: float


@dataclass
class TopstepEvalConfig:
    account_size: float
    profit_target: float
    trailing_max_drawdown: float
    daily_loss_limit: float | None = None


@dataclass
class TradovateCreds:
    """One Tradovate login can have several accounts under it (multiple
    TopStep evals/funded accounts, for example). `account_names` lists which
    of those to trade; leave empty to trade every active account found."""

    env: str = "demo"
    username: str = ""
    password: str = ""
    app_id: str = ""
    app_version: str = "1.0"
    cid: str = ""
    sec: str = ""
    device_id: str = "jj-bot-01"
    account_names: list[str] = field(default_factory=list)


@dataclass
class IBKRCreds:
    """Interactive Brokers connects via a running TWS or IB Gateway process
    (not a hosted REST API) — host/port/client_id point at that process.
    Paper trading account IDs start with 'DU'; IBKR paper accounts are free
    and don't require funding a live account, unlike Tradovate's API access
    (which requires a funded live account + a paid add-on).

    Default ports: TWS paper 7497, IB Gateway paper 4002 (live: 7496 / 4001).
    """

    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 1
    account_names: list[str] = field(default_factory=list)


@dataclass
class NinjaTraderCreds:
    """NinjaTrader 8 has no REST API — automation goes through the free ATI
    (Automated Trading Interface), a file-drop protocol. `incoming_dir` is
    where order command files are written (NinjaTrader watches and consumes
    them); `export_dir` is where the companion NinjaScript exporter
    (ninjatrader/JJBotExporter.cs) writes bars.csv/fills.csv for this client
    to tail. Must run on the same Windows machine as NinjaTrader (or a
    Windows VPS with NinjaTrader installed) — NinjaTrader has no Linux mode.
    """

    incoming_dir: str = ""
    export_dir: str = ""
    account_names: list[str] = field(default_factory=lambda: ["Sim101"])
    instrument: str = ""


@dataclass
class AppConfig:
    strategy: StrategyConfig
    risk: RiskConfig
    instrument: InstrumentConfig
    topstep_eval: TopstepEvalConfig
    broker: str = "ibkr"  # "ibkr" (default, free paper trading), "tradovate", or "ninjatrader"
    tradovate: TradovateCreds = field(default_factory=TradovateCreds)
    ibkr: IBKRCreds = field(default_factory=IBKRCreds)
    ninjatrader: NinjaTraderCreds = field(default_factory=NinjaTraderCreds)


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path) if path else REPO_ROOT / "config.yaml"
    with open(path) as f:
        raw = yaml.safe_load(f)

    load_dotenv(REPO_ROOT / ".env")

    # TRADOVATE_ACCOUNT_NAMES="EVAL123,EVAL456,FUNDED789" (comma-separated).
    # TRADOVATE_ACCOUNT_NAME (singular) still works for a single account.
    # If neither is set, fall back to whatever's saved on the dashboard's
    # "My Accounts" page (Supabase) — set the env var to override.
    names_raw = os.getenv("TRADOVATE_ACCOUNT_NAMES") or os.getenv("TRADOVATE_ACCOUNT_NAME", "")
    account_names = [n.strip() for n in names_raw.split(",") if n.strip()] or _fetch_saved_account_names()

    tradovate_creds = TradovateCreds(
        env=os.getenv("TRADOVATE_ENV", "demo"),
        username=os.getenv("TRADOVATE_USERNAME", ""),
        password=os.getenv("TRADOVATE_PASSWORD", ""),
        app_id=os.getenv("TRADOVATE_APP_ID", ""),
        app_version=os.getenv("TRADOVATE_APP_VERSION", "1.0"),
        cid=os.getenv("TRADOVATE_CID", ""),
        sec=os.getenv("TRADOVATE_SEC", ""),
        device_id=os.getenv("TRADOVATE_DEVICE_ID", "jj-bot-01"),
        account_names=account_names,
    )

    ibkr_names_raw = os.getenv("IBKR_ACCOUNT_NAMES", "")
    ibkr_account_names = [n.strip() for n in ibkr_names_raw.split(",") if n.strip()] or _fetch_saved_account_names()
    ibkr_creds = IBKRCreds(
        host=os.getenv("IBKR_HOST", "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT", "4002")),
        client_id=int(os.getenv("IBKR_CLIENT_ID", "1")),
        account_names=ibkr_account_names,
    )

    # NT_ACCOUNT_NAMES="DEMO8217187,Sim101" (comma-separated) — same
    # dashboard-first pattern as IBKR/Tradovate above. NT_ACCOUNT_NAME
    # (singular) still works for a single account.
    nt_names_raw = os.getenv("NT_ACCOUNT_NAMES") or os.getenv("NT_ACCOUNT_NAME", "")
    nt_account_names = [n.strip() for n in nt_names_raw.split(",") if n.strip()] or _fetch_saved_account_names() or ["Sim101"]
    ninjatrader_creds = NinjaTraderCreds(
        incoming_dir=os.getenv("NT_INCOMING_DIR", ""),
        export_dir=os.getenv("NT_EXPORT_DIR", ""),
        account_names=nt_account_names,
        instrument=os.getenv("NT_INSTRUMENT", ""),
    )

    return AppConfig(
        strategy=StrategyConfig(**raw["strategy"]),
        risk=RiskConfig(**raw["risk"]),
        instrument=InstrumentConfig(**raw["instrument"]),
        topstep_eval=TopstepEvalConfig(**raw["topstep_eval"]),
        broker=os.getenv("BROKER", "ibkr").strip().lower(),
        tradovate=tradovate_creds,
        ibkr=ibkr_creds,
        ninjatrader=ninjatrader_creds,
    )
