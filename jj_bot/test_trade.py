"""End-to-end connectivity test: authenticate, resolve account(s), and place
one small bracket order — the way to confirm "is this actually wired up to
my account and will it really submit trades" before trusting the live
strategy runner.

Dispatches on `cfg.broker` ("ibkr" by default — free paper trading, no
funding required; "topstepx" for a real TopStep account, TopStep's own
platform; or "tradovate", legacy, only for a Tradovate account opened
outside TopStep).

Used by both the CLI (`scripts/test_connection.py`) and the bot API server's
`/api/test-trade` endpoint that the dashboard's Test Trade panel calls.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .config import AppConfig
from .models import Direction, Phase, Signal, SetupGrade, TradeResult
from .trade_logger import TradeLogger


@dataclass
class ConnectionTestResult:
    accounts: list[str]
    tested_account: str
    contract_symbol: str
    order_response: dict
    # Real, resolved P&L in dollars — only populated for brokers where a
    # "connectivity test" is a real trade with real money on the line
    # (TopstepX). None means either not applicable (paper/demo brokers) or
    # the position hadn't closed yet by the time we stopped polling.
    realized_pnl_dollars: Optional[float] = None


def list_accounts(cfg: AppConfig) -> list[str]:
    if cfg.broker == "ibkr":
        from .ibkr_client import IBKRClient

        client = IBKRClient(cfg.ibkr)
        try:
            client.connect()
            return list(client.accounts)
        finally:
            client.disconnect()

    if cfg.broker == "topstepx":
        from .topstepx_client import TopstepXClient

        client = TopstepXClient(cfg.topstepx)
        client.authenticate()
        return [a.name for a in client.load_accounts()]

    from .tradovate_client import TradovateClient

    client = TradovateClient(cfg.tradovate)
    client.authenticate()
    return [a.name for a in client.load_accounts()]


def run_connection_test(cfg: AppConfig, account_name: Optional[str] = None, direction: str = "Buy") -> ConnectionTestResult:
    """Places one small bracket test trade (default 4pt stop / 6pt target,
    1 contract) on the given account, or the first resolved account if none
    is specified. Logs the result to the dashboard trade log with
    source='connection_test' so it's clearly distinguishable from real
    strategy trades."""
    if cfg.broker == "ibkr":
        result = _run_ibkr_test(cfg, account_name, direction)
    elif cfg.broker == "topstepx":
        result = _run_topstepx_test(cfg, account_name, direction)
    else:
        result = _run_tradovate_test(cfg, account_name, direction)

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
        reason=f"Connectivity test trade on account {result.tested_account} ({result.contract_symbol})",
    )

    if result.realized_pnl_dollars is not None:
        # Real money, resolved outcome (TopstepX) — log the ACTUAL result,
        # not a placeholder. win=True/pnl=0 here would misrepresent a real
        # gain or loss on the real account.
        pnl_points = result.realized_pnl_dollars / dollar_per_point
        win = result.realized_pnl_dollars > 0
    elif result.realized_pnl_dollars is None and cfg.broker == "topstepx":
        # Real money, but the position hadn't closed within our poll window
        # — say so honestly instead of claiming a fake $0 win. This entry
        # stays excluded from stats (source=connection_test) either way,
        # but at least it isn't actively lying in the trade log.
        signal.reason += " — outcome pending, check the TopStep platform for the real result"
        pnl_points, win = 0.0, True
    else:
        # Paper/demo brokers (IBKR paper, Tradovate demo): no real money
        # moved, so a $0 placeholder is accurate, not misleading.
        pnl_points, win = 0.0, True

    logger.log_trade(
        TradeResult(signal=signal, exit_price=0.0, exit_timestamp=signal.timestamp, win=win, pnl_points=pnl_points),
        account_name=result.tested_account,
    )
    return result


def _run_ibkr_test(cfg: AppConfig, account_name: Optional[str], direction: str) -> ConnectionTestResult:
    from .ibkr_client import IBKRClient

    client = IBKRClient(cfg.ibkr)
    client.connect()
    try:
        accounts = client.accounts
        target_account = account_name or accounts[0]
        if account_name and account_name not in accounts:
            raise ValueError(f"Account '{account_name}' not found among resolved accounts: {accounts}")

        contract = client.find_front_month_contract(cfg.instrument.symbol)
        ib_action = "BUY" if direction == "Buy" else "SELL"
        order_ids = client.place_test_trade(contract=contract, account=target_account, action=ib_action, qty=1)

        return ConnectionTestResult(
            accounts=accounts,
            tested_account=target_account,
            contract_symbol=contract.ib_contract.localSymbol,
            order_response={
                "parent_order_id": order_ids.parent_id,
                "take_profit_order_id": order_ids.take_profit_id,
                "stop_loss_order_id": order_ids.stop_loss_id,
            },
        )
    finally:
        client.disconnect()


def _run_topstepx_test(cfg: AppConfig, account_name: Optional[str], direction: str) -> ConnectionTestResult:
    from .topstepx_client import TopstepXClient

    client = TopstepXClient(cfg.topstepx)
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
    placed_at = datetime.now(timezone.utc).isoformat()
    order_response = client.place_test_trade(contract=contract, account=target_account, action=direction, qty=1)

    # Real money, real order — poll briefly for the actual outcome instead
    # of assuming it filled/closed instantly. A 4-6pt bracket on NQ usually
    # resolves within seconds to a couple minutes; give it up to ~60s before
    # giving up and logging it as pending (see run_connection_test above).
    realized_pnl = None
    for _ in range(12):
        time.sleep(5)
        realized_pnl = client.get_recent_trade_pnl(target_account.id, after_iso=placed_at)
        if realized_pnl is not None:
            break

    return ConnectionTestResult(
        accounts=[a.name for a in accounts],
        tested_account=target_account.name,
        contract_symbol=contract.name,
        order_response=order_response,
        realized_pnl_dollars=realized_pnl,
    )


def _run_tradovate_test(cfg: AppConfig, account_name: Optional[str], direction: str) -> ConnectionTestResult:
    from .tradovate_client import TradovateClient

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
    order_response = client.place_test_trade(contract=contract, account=target_account, action=direction, qty=1)

    return ConnectionTestResult(
        accounts=[a.name for a in accounts],
        tested_account=target_account.name,
        contract_symbol=contract.name,
        order_response=order_response,
    )
