"""Loads strategy/risk config from config.yaml and credentials from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent


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
    account_name: str = "Sim101"
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
    names_raw = os.getenv("TRADOVATE_ACCOUNT_NAMES") or os.getenv("TRADOVATE_ACCOUNT_NAME", "")
    account_names = [n.strip() for n in names_raw.split(",") if n.strip()]

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
    ibkr_creds = IBKRCreds(
        host=os.getenv("IBKR_HOST", "127.0.0.1"),
        port=int(os.getenv("IBKR_PORT", "4002")),
        client_id=int(os.getenv("IBKR_CLIENT_ID", "1")),
        account_names=[n.strip() for n in ibkr_names_raw.split(",") if n.strip()],
    )

    ninjatrader_creds = NinjaTraderCreds(
        incoming_dir=os.getenv("NT_INCOMING_DIR", ""),
        export_dir=os.getenv("NT_EXPORT_DIR", ""),
        account_name=os.getenv("NT_ACCOUNT_NAME", "Sim101"),
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
