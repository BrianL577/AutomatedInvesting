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
    env: str = "demo"
    username: str = ""
    password: str = ""
    app_id: str = ""
    app_version: str = "1.0"
    cid: str = ""
    sec: str = ""
    device_id: str = "jj-bot-01"
    account_name: str = ""


@dataclass
class AppConfig:
    strategy: StrategyConfig
    risk: RiskConfig
    instrument: InstrumentConfig
    topstep_eval: TopstepEvalConfig
    tradovate: TradovateCreds = field(default_factory=TradovateCreds)


def load_config(path: str | Path | None = None) -> AppConfig:
    path = Path(path) if path else REPO_ROOT / "config.yaml"
    with open(path) as f:
        raw = yaml.safe_load(f)

    load_dotenv(REPO_ROOT / ".env")

    creds = TradovateCreds(
        env=os.getenv("TRADOVATE_ENV", "demo"),
        username=os.getenv("TRADOVATE_USERNAME", ""),
        password=os.getenv("TRADOVATE_PASSWORD", ""),
        app_id=os.getenv("TRADOVATE_APP_ID", ""),
        app_version=os.getenv("TRADOVATE_APP_VERSION", "1.0"),
        cid=os.getenv("TRADOVATE_CID", ""),
        sec=os.getenv("TRADOVATE_SEC", ""),
        device_id=os.getenv("TRADOVATE_DEVICE_ID", "jj-bot-01"),
        account_name=os.getenv("TRADOVATE_ACCOUNT_NAME", ""),
    )

    return AppConfig(
        strategy=StrategyConfig(**raw["strategy"]),
        risk=RiskConfig(**raw["risk"]),
        instrument=InstrumentConfig(**raw["instrument"]),
        topstep_eval=TopstepEvalConfig(**raw["topstep_eval"]),
        tradovate=creds,
    )
