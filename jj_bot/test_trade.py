"""End-to-end connectivity test: authenticate, resolve account(s), and place
one small bracket order — the way to confirm "is this actually wired up to
my account and will it really submit trades" before trusting the live
strategy runner.

Used by both the CLI (`scripts/test_connection.py`) and the bot API server's
`/api/test-trade` endpoint that the dashboard's Test Trade panel calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .config import AppConfig
from .models import Direction, Phase, Signal, SetupGrade, TradeResult
from .tradovate_client import Account, TradovateClient
from .trade_logger import TradeLogger


@dataclass
class ConnectionTestResult:
    accounts: list[str]
    tested_account: str
    contract_symbol: str
    order_response: dict


def list_accounts(cfg: AppConfig) -> list[Account]:
    client = TradovateClient(cfg.tradovate)
    client.authenticate()
    return client.load_accounts()


def run_connection_test(cfg: AppConfig, account_name: Optional[str] = None, direction: str = "Buy") -> ConnectionTestResult:
    """Places one small bracket test trade (default 4pt stop / 6pt target,
    1 contract) on the given account, or the first resolved account if none
    is specified. Logs the result to the dashboard trade log with
    source='connection_test' so it's clearly distinguishable from real
    strategy trades."""
    client = TradovateClient(cfg.tradovate)
    client.authenticate()
    accounts = client.load_accounts()

    target_account = None
    if account_name:
        target_account = next((a for a in accounts if a.name == account_name), None)
        if target_account is None:
            raise ValueError(f"Account '{account_name}' not found among resolved accounts: {[a.name for a in accounts]}")
    else:
        target_account = accounts[0]

    contract = client.find_front_month_contract(cfg.instrument.symbol)
    order_response = client.place_test_trade(
        contract=contract,
        account=target_account,
        action=direction,
        qty=1,
    )

    dollar_per_point = cfg.instrument.tick_value / cfg.instrument.tick_size
    logger = TradeLogger(dollar_per_point=dollar_per_point, source="connection_test")
    signal = Signal(
        timestamp=datetime.now(),
        direction=Direction.LONG if direction == "Buy" else Direction.SHORT,
        entry_price=0.0,
        stop_price=0.0,
        target_price=0.0,
        phase=Phase.TEST,
        grade=SetupGrade.A,
        reason=f"Connectivity test trade on account {target_account.name} ({contract.name})",
    )
    logger.log_trade(
        TradeResult(signal=signal, exit_price=0.0, exit_timestamp=signal.timestamp, win=True, pnl_points=0.0)
    )

    return ConnectionTestResult(
        accounts=[a.name for a in accounts],
        tested_account=target_account.name,
        contract_symbol=contract.name,
        order_response=order_response,
    )
