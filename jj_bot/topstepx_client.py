"""TopstepX (ProjectX Gateway) REST + SignalR client: auth, account/contract
lookup, live quotes, and order placement — this is what a real TopStep
account actually runs on today (TopStep replaced Tradovate/NinjaTrader
routing with its own TopstepX platform; every account still active as of
Feb 2026 runs on TopstepX, not Tradovate). See tradovate_client.py for the
legacy path, kept only for a Tradovate account you opened yourself outside
TopStep.

IMPORTANT — there is no sandbox/demo environment. Every order placed through
this client is a real order against real money the moment TOPSTEPX_ALLOW_LIVE
is set. There is no "env=demo" safety net like Tradovate had.

Endpoint/schema details below are assembled from public ProjectX Gateway
documentation (gateway.docs.projectx.com), not verified against a live
account from this session — confirm with a real `test_connection.py` run
(small size, tight stop/target) before trusting this for real strategy
trading, and open an issue/adjust field names here if TopStep's docs have
since changed.

Docs: https://gateway.docs.projectx.com/
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import requests
import websocket

from .config import TopstepXCreds

REST_BASE = "https://api.topstepx.com/api"
USER_HUB_URL = "wss://rtc.topstepx.com/hubs/user"
MARKET_HUB_URL = "wss://rtc.topstepx.com/hubs/market"

# ProjectX Gateway order type/side enums (confirmed via public docs).
ORDER_TYPE_LIMIT = 1
ORDER_TYPE_MARKET = 2
ORDER_TYPE_STOP_LIMIT = 3
ORDER_TYPE_STOP = 4
ORDER_TYPE_TRAILING_STOP = 5
ORDER_SIDE_BUY = 0
ORDER_SIDE_SELL = 1


class TopstepXAuthError(RuntimeError):
    pass


class TopstepXGuardError(RuntimeError):
    """Raised if code tries to place a live order without the explicit
    TOPSTEPX_ALLOW_LIVE opt-in. Unlike Tradovate, TopstepX has no demo
    environment — this is the only safety net."""


@dataclass
class Contract:
    id: str  # e.g. "CON.F.US.ENQ.U25"
    name: str
    tick_size: float
    tick_value: float


@dataclass
class Account:
    id: int
    name: str
    can_trade: bool = True


class TopstepXClient:
    def __init__(self, creds: TopstepXCreds):
        if not creds.allow_live:
            raise TopstepXGuardError(
                "Refusing to run: TOPSTEPX_ALLOW_LIVE=true is required in .env as an "
                "explicit opt-in. TopstepX has no demo/sandbox environment — every "
                "order this client places is real money against your real TopStep "
                "account the moment this is enabled. Make sure that's intentional."
            )
        self.creds = creds
        self.token: Optional[str] = None
        self.accounts: list[Account] = []
        self.account_id: Optional[int] = None

    # ---- REST auth / account -------------------------------------------------

    def authenticate(self) -> None:
        resp = requests.post(
            f"{REST_BASE}/Auth/loginKey",
            json={"userName": self.creds.username, "apiKey": self.creds.api_key},
            headers={"accept": "text/plain", "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success") or not data.get("token"):
            raise TopstepXAuthError(f"TopstepX auth failed: {data}")
        self.token = data["token"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def load_accounts(self) -> list[Account]:
        """Resolve every configured account (or all active accounts if none
        are named) under this TopstepX login — supports trading several
        TopStep eval/funded accounts under one login at once."""
        resp = requests.post(
            f"{REST_BASE}/Account/search",
            json={"onlyActiveAccounts": True},
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise TopstepXAuthError(f"Account search failed: {data}")
        raw_accounts = data.get("accounts", [])
        if not raw_accounts:
            raise TopstepXAuthError("No active TopstepX accounts found for this user.")

        if self.creds.account_names:
            wanted = set(self.creds.account_names)
            matched = [a for a in raw_accounts if a.get("name") in wanted]
            missing = wanted - {a.get("name") for a in matched}
            if missing:
                raise TopstepXAuthError(f"Configured account name(s) not found: {sorted(missing)}")
            selected = matched
        else:
            selected = raw_accounts

        self.accounts = [
            Account(id=a["id"], name=a["name"], can_trade=a.get("canTrade", True)) for a in selected
        ]
        not_tradeable = [a.name for a in self.accounts if not a.can_trade]
        if not_tradeable:
            raise TopstepXAuthError(f"Account(s) not tradeable: {not_tradeable}")

        self.account_id = self.accounts[0].id
        return self.accounts

    def find_front_month_contract(self, root_symbol: str) -> Contract:
        """Resolve e.g. 'NQ' to the current front-month contract via
        /Contract/search. `live=False` here means "search the paper contract
        catalog" per ProjectX's own terminology — has nothing to do with
        real-vs-demo money (TopstepX has no demo money); keep False unless
        you've confirmed otherwise against your account."""
        resp = requests.post(
            f"{REST_BASE}/Contract/search",
            json={"live": False, "searchText": root_symbol},
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise TopstepXAuthError(f"Contract search failed: {data}")
        candidates = data.get("contracts", [])
        if not candidates:
            raise RuntimeError(f"No contracts found for {root_symbol}")
        best = next((c for c in candidates if c.get("activeContract")), candidates[0])
        return Contract(
            id=best["id"],
            name=best.get("name", best["id"]),
            tick_size=float(best.get("tickSize", 0.25)),
            tick_value=float(best.get("tickValue", 5.0)),
        )

    # ---- Orders ----------------------------------------------------------

    def place_order(
        self,
        account: Account,
        contract: Contract,
        order_type: int,
        side: int,
        size: int,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        linked_order_id: Optional[int] = None,
        custom_tag: Optional[str] = None,
    ) -> dict:
        payload: dict = {
            "accountId": account.id,
            "contractId": contract.id,
            "type": order_type,
            "side": side,
            "size": size,
        }
        if limit_price is not None:
            payload["limitPrice"] = limit_price
        if stop_price is not None:
            payload["stopPrice"] = stop_price
        if linked_order_id is not None:
            payload["linkedOrderId"] = linked_order_id
        if custom_tag is not None:
            payload["customTag"] = custom_tag

        resp = requests.post(f"{REST_BASE}/Order/place", json=payload, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise TopstepXAuthError(f"Order placement failed: {data}")
        return data

    def place_bracket_order(
        self,
        contract: Contract,
        action: str,  # "Buy" or "Sell"
        qty: int,
        stop_price: float,
        target_price: float,
        account: Optional[Account] = None,
    ) -> dict:
        """Market entry, then a linked stop + limit OCO pair (per ProjectX's
        Auto-OCO bracket model — the two exit orders reference each other via
        linkedOrderId so filling one cancels the other). UNVERIFIED against a
        live account: confirm this linkage behaves as an actual OCO on your
        account before trusting it for real strategy trading — TopStep's
        docs describe Auto-OCO brackets as a per-order feature but the exact
        API wiring here is inferred, not confirmed end-to-end."""
        acct = account or (self.accounts[0] if self.accounts else None)
        if acct is None:
            raise TopstepXAuthError("No account resolved — call load_accounts() first.")

        side = ORDER_SIDE_BUY if action == "Buy" else ORDER_SIDE_SELL
        exit_side = ORDER_SIDE_SELL if action == "Buy" else ORDER_SIDE_BUY

        entry = self.place_order(acct, contract, ORDER_TYPE_MARKET, side, qty, custom_tag="jj-bot-entry")
        entry_order_id = entry.get("orderId")

        stop = self.place_order(
            acct, contract, ORDER_TYPE_STOP, exit_side, qty,
            stop_price=stop_price, custom_tag="jj-bot-stop",
        )
        target = self.place_order(
            acct, contract, ORDER_TYPE_LIMIT, exit_side, qty,
            limit_price=target_price, linked_order_id=stop.get("orderId"), custom_tag="jj-bot-target",
        )
        return {"entry": entry, "entry_order_id": entry_order_id, "stop": stop, "target": target}

    def place_test_trade(
        self,
        contract: Contract,
        account: Account,
        action: str = "Buy",
        qty: int = 1,
        stop_points: float = 4.0,
        target_points: float = 6.0,
    ) -> dict:
        """Places a minimal 1-lot bracket sized for connectivity testing.
        There is no paper account here — this is a real, if small, trade
        against real money. Used by the 'test automation' flow to confirm
        the pipeline works end to end."""
        quote = self.get_last_price(contract)
        if action == "Buy":
            stop_price = quote - stop_points
            target_price = quote + target_points
        else:
            stop_price = quote + stop_points
            target_price = quote - target_points
        return self.place_bracket_order(
            contract=contract, action=action, qty=qty,
            stop_price=stop_price, target_price=target_price, account=account,
        )

    def get_last_price(self, contract: Contract) -> float:
        """No documented REST quote-snapshot endpoint — TopstepX pushes
        quotes over the market SignalR hub instead. Connects briefly, waits
        for one GatewayQuote event, and disconnects. UNVERIFIED: the exact
        hub subscription method name (SubscribeContractQuotes) is inferred
        from the documented event name, not confirmed against a live feed."""
        result: dict = {}
        done = threading.Event()

        def on_quote(price: float) -> None:
            result["price"] = price
            done.set()

        stream = TopstepXMarketDataStream(self.token, on_quote=on_quote)
        stream.connect()
        stream.subscribe_quotes(contract.id)
        thread = threading.Thread(target=stream.run_forever, daemon=True)
        thread.start()
        got_price = done.wait(timeout=10)
        stream.stop()
        if not got_price or "price" not in result:
            raise TopstepXAuthError(
                "Could not fetch a live quote within 10s over the market data hub — "
                "confirm the contract is actively trading and the hub subscription "
                "method name is correct for your account."
            )
        return result["price"]


class TopstepXMarketDataStream:
    """Minimal SignalR (JSON hub protocol) client over a raw WebSocket —
    avoids pulling in a full SignalR client dependency for what's otherwise a
    small, well-documented framing: each message is JSON followed by the
    record separator 0x1e. UNVERIFIED against a live hub connection; confirm
    the handshake and subscribe method name work before relying on this for
    live trading."""

    RECORD_SEP = "\x1e"

    def __init__(self, token: str, on_quote: Optional[Callable[[float], None]] = None, on_bar: Optional[Callable] = None):
        self.token = token
        self.on_quote = on_quote
        self.on_bar = on_bar
        self._ws: Optional[websocket.WebSocket] = None
        self._stop = threading.Event()

    def connect(self) -> None:
        self._ws = websocket.create_connection(f"{MARKET_HUB_URL}?access_token={self.token}", timeout=15)
        # SignalR handshake: negotiate the JSON hub protocol, version 1.
        self._send({"protocol": "json", "version": 1})
        self._ws.recv()  # handshake response (empty JSON object on success)

    def _send(self, obj: dict) -> None:
        self._ws.send(json.dumps(obj) + self.RECORD_SEP)

    def subscribe_quotes(self, contract_id: str) -> None:
        self._send({"type": 1, "target": "SubscribeContractQuotes", "arguments": [contract_id]})

    def run_forever(self) -> None:
        """Blocking receive loop; call from a dedicated thread."""
        while not self._stop.is_set():
            try:
                raw = self._ws.recv()
            except Exception:
                break
            for frame in raw.split(self.RECORD_SEP):
                if not frame:
                    continue
                try:
                    msg = json.loads(frame)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == 1 and msg.get("target") == "GatewayQuote":
                    args = msg.get("arguments", [])
                    if args and self.on_quote:
                        price = args[-1].get("lastPrice") or args[-1].get("last")
                        if price is not None:
                            self.on_quote(float(price))

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close()
