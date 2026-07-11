"""Interactive Brokers client via `ib_insync`, wrapping a running TWS or IB
Gateway process (there is no hosted REST API like Tradovate's — IBKR
connects over a local socket to software you run and stay logged into).

Why IBKR instead of Tradovate: Tradovate hides API key generation entirely
behind a funded live account ($1,000 min) plus a paid API add-on. IBKR's
paper trading account is free, requires no funding, and its API works on
paper accounts out of the box — the tradeoff is it's a desktop-app socket
connection (TWS/IB Gateway) rather than a cloud REST API, so *something*
has to keep that process logged in and running (see RAILWAY.md / IBKR.md).

Paper trading account IDs start with "DU". Default ports:
  TWS paper:        7497   TWS live:        7496
  IB Gateway paper:  4002   IB Gateway live:  4001
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Callable, Optional

from ib_insync import IB, Future, MarketOrder, LimitOrder, StopOrder, Contract as IBContract

from .config import IBKRCreds

logger = logging.getLogger("jj_bot.ibkr")


class IBKRConnectionError(RuntimeError):
    pass


@dataclass
class Contract:
    symbol: str
    ib_contract: IBContract


@dataclass
class BracketOrderIds:
    parent_id: int
    take_profit_id: int
    stop_loss_id: int


class IBKRClient:
    def __init__(self, creds: IBKRCreds):
        self.creds = creds
        self.ib = IB()
        self.accounts: list[str] = []

    # ---- connection -------------------------------------------------

    def connect(self, timeout: float = 15.0) -> None:
        try:
            self.ib.connect(self.creds.host, self.creds.port, clientId=self.creds.client_id, timeout=timeout)
        except Exception as exc:
            raise IBKRConnectionError(
                f"Could not connect to TWS/IB Gateway at {self.creds.host}:{self.creds.port}. "
                f"Is it running and logged in? ({exc})"
            ) from exc

        all_accounts = list(self.ib.managedAccounts())
        if not all_accounts:
            raise IBKRConnectionError("Connected, but no managed accounts were returned.")

        live_accounts = [a for a in all_accounts if not a.startswith("DU") and not a.startswith("DF")]
        if live_accounts:
            raise IBKRConnectionError(
                f"Refusing to run: account(s) {live_accounts} are not paper accounts "
                f"(paper account IDs start with 'DU'). This bot is for paper trading only."
            )

        if self.creds.account_names:
            wanted = set(self.creds.account_names)
            missing = wanted - set(all_accounts)
            if missing:
                raise IBKRConnectionError(f"Configured account name(s) not found: {sorted(missing)}")
            self.accounts = [a for a in all_accounts if a in wanted]
        else:
            self.accounts = all_accounts

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    # ---- contracts --------------------------------------------------

    def find_front_month_contract(self, root_symbol: str, exchange: str = "CME") -> Contract:
        details = self.ib.reqContractDetails(Future(symbol=root_symbol, exchange=exchange, currency="USD"))
        if not details:
            raise IBKRConnectionError(f"No contracts found for {root_symbol} on {exchange}")

        today = date.today()

        def expiry(d) -> date:
            s = d.contract.lastTradeDateOrContractMonth
            return datetime.strptime(s[:8], "%Y%m%d").date()

        upcoming = sorted((d for d in details if expiry(d) >= today), key=expiry)
        if not upcoming:
            raise IBKRConnectionError(f"No unexpired contracts found for {root_symbol}")
        chosen = upcoming[0].contract
        self.ib.qualifyContracts(chosen)
        return Contract(symbol=root_symbol, ib_contract=chosen)

    def get_last_price(self, contract: Contract) -> float:
        ticker = self.ib.reqMktData(contract.ib_contract, "", False, False)
        self.ib.sleep(2)
        price = ticker.marketPrice()
        self.ib.cancelMktData(contract.ib_contract)
        if price != price or price <= 0:  # NaN or invalid
            raise IBKRConnectionError(
                "Could not get a live quote — your IBKR account may need a CME market data "
                "subscription (delayed data may also work; check Account Management > Market Data Subscriptions)."
            )
        return float(price)

    # ---- historical / streaming bars ---------------------------------

    def stream_bars(self, contract: Contract, on_closed_bar: Callable, bar_size: str = "1 min"):
        """Requests live-updating historical bars (keepUpToDate=True). Returns
        the ib_insync BarDataList; caller must keep a reference and eventually
        call ib.cancelHistoricalData(bars). Feeds each newly-closed bar to
        `on_closed_bar(bar)` (an object with .date/.open/.high/.low/.close)."""
        bars = self.ib.reqHistoricalData(
            contract.ib_contract,
            endDateTime="",
            durationStr="2 D",
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=False,
            keepUpToDate=True,
        )

        def _on_update(bars_list, has_new_bar):
            if has_new_bar and len(bars_list) >= 2:
                on_closed_bar(bars_list[-2])

        bars.updateEvent += _on_update
        return bars

    def get_historical_bars(self, contract: Contract, duration: str = "5 D", bar_size: str = "1 min"):
        return self.ib.reqHistoricalData(
            contract.ib_contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=False,
            keepUpToDate=False,
        )

    # ---- orders -------------------------------------------------------

    def place_bracket_order(
        self,
        contract: Contract,
        account: str,
        action: str,  # "BUY" or "SELL"
        qty: int,
        stop_price: float,
        target_price: float,
    ) -> BracketOrderIds:
        reverse = "SELL" if action == "BUY" else "BUY"

        parent = MarketOrder(action, qty)
        parent.account = account
        parent.orderId = self.ib.client.getReqId()
        parent.transmit = False

        oca_group = f"jjbot-{parent.orderId}"

        take_profit = LimitOrder(reverse, qty, round(target_price, 2))
        take_profit.account = account
        take_profit.orderId = self.ib.client.getReqId()
        take_profit.parentId = parent.orderId
        take_profit.ocaGroup = oca_group
        take_profit.ocaType = 1
        take_profit.transmit = False

        stop_loss = StopOrder(reverse, qty, round(stop_price, 2))
        stop_loss.account = account
        stop_loss.orderId = self.ib.client.getReqId()
        stop_loss.parentId = parent.orderId
        stop_loss.ocaGroup = oca_group
        stop_loss.ocaType = 1
        stop_loss.transmit = True  # last leg transmits the whole bracket

        self.ib.placeOrder(contract.ib_contract, parent)
        self.ib.placeOrder(contract.ib_contract, take_profit)
        self.ib.placeOrder(contract.ib_contract, stop_loss)

        return BracketOrderIds(parent_id=parent.orderId, take_profit_id=take_profit.orderId, stop_loss_id=stop_loss.orderId)

    def place_test_trade(
        self,
        contract: Contract,
        account: str,
        action: str = "BUY",
        qty: int = 1,
        stop_points: float = 4.0,
        target_points: float = 6.0,
    ) -> BracketOrderIds:
        price = self.get_last_price(contract)
        if action == "BUY":
            stop_price = price - stop_points
            target_price = price + target_points
        else:
            stop_price = price + stop_points
            target_price = price - target_points
        return self.place_bracket_order(contract, account, action, qty, stop_price, target_price)
