"""Minimal Tradovate REST + WebSocket client: auth, contract lookup, historical
bars, live quotes, and bracket order placement — enough to run the JJ
strategy against a **demo** account.

Tradovate API docs: https://api.tradovate.com/
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests
import websocket

from .config import TradovateCreds

REST_HOSTS = {
    "demo": "https://demo.tradovateapi.com/v1",
    "live": "https://live.tradovateapi.com/v1",
}
WS_HOSTS = {
    "demo": "wss://demo.tradovateapi.com/v1/websocket",
    "live": "wss://live.tradovateapi.com/v1/websocket",
}
MD_WS_HOST = "wss://md.tradovateapi.com/v1/websocket"


class TradovateAuthError(RuntimeError):
    pass


class TradovateGuardError(RuntimeError):
    """Raised if code accidentally tries to trade a non-demo account."""


@dataclass
class Contract:
    id: int
    name: str


@dataclass
class Account:
    id: int
    name: str
    active: bool = True


class TradovateClient:
    def __init__(self, creds: TradovateCreds):
        if creds.env != "demo":
            raise TradovateGuardError(
                "Refusing to run: TRADOVATE_ENV must be 'demo'. "
                "This bot is for paper trading only."
            )
        self.creds = creds
        self.rest_base = REST_HOSTS[creds.env]
        self.access_token: Optional[str] = None
        self.md_access_token: Optional[str] = None
        self.accounts: list[Account] = []
        # Kept for backward compatibility / single-account call sites —
        # points at the first resolved account after load_accounts().
        self.account_id: Optional[int] = None
        self.account_spec: Optional[str] = None

    # ---- REST auth / account -------------------------------------------------

    def authenticate(self) -> None:
        resp = requests.post(
            f"{self.rest_base}/auth/accesstokenrequest",
            json={
                "name": self.creds.username,
                "password": self.creds.password,
                "appId": self.creds.app_id,
                "appVersion": self.creds.app_version,
                "cid": self.creds.cid,
                "sec": self.creds.sec,
                "deviceId": self.creds.device_id,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "accessToken" not in data:
            raise TradovateAuthError(f"Tradovate auth failed: {data}")
        self.access_token = data["accessToken"]
        self.md_access_token = data.get("mdAccessToken", self.access_token)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def load_accounts(self) -> list[Account]:
        """Resolve every configured account (or all active accounts if none
        are named) under this Tradovate login. Supports trading several
        accounts — e.g. multiple TopStep evals/funded accounts — at once."""
        resp = requests.get(f"{self.rest_base}/account/list", headers=self._headers(), timeout=15)
        resp.raise_for_status()
        raw_accounts = resp.json()
        if not raw_accounts:
            raise TradovateAuthError("No Tradovate accounts found for this user.")

        if self.creds.account_names:
            wanted = set(self.creds.account_names)
            matched = [a for a in raw_accounts if a.get("name") in wanted]
            missing = wanted - {a.get("name") for a in matched}
            if missing:
                raise TradovateAuthError(f"Configured account name(s) not found: {sorted(missing)}")
            selected = matched
        else:
            selected = [a for a in raw_accounts if a.get("active", True)]

        if not selected:
            raise TradovateAuthError("No active/matching Tradovate accounts resolved.")

        self.accounts = [Account(id=a["id"], name=a["name"], active=a.get("active", True)) for a in selected]
        inactive = [a.name for a in self.accounts if not a.active]
        if inactive:
            raise TradovateAuthError(f"Account(s) not active: {inactive}")

        self.account_id = self.accounts[0].id
        self.account_spec = self.accounts[0].name
        return self.accounts

    def load_account(self) -> None:
        """Deprecated alias for load_accounts(), kept for older call sites."""
        self.load_accounts()

    def find_front_month_contract(self, root_symbol: str) -> Contract:
        """Resolve e.g. 'NQ' to the current front-month contract via /contract/suggest."""
        resp = requests.get(
            f"{self.rest_base}/contract/suggest",
            params={"t": root_symbol, "l": 20},
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        candidates = resp.json()
        if not candidates:
            raise RuntimeError(f"No contracts found for {root_symbol}")
        # Tradovate returns candidates ordered by relevance; the first exact
        # root-symbol match is generally the active front-month contract.
        best = next((c for c in candidates if c["name"].startswith(root_symbol)), candidates[0])
        return Contract(id=best["id"], name=best["name"])

    # ---- Orders ----------------------------------------------------------

    def place_bracket_order(
        self,
        contract: Contract,
        action: str,  # "Buy" or "Sell"
        qty: int,
        stop_price: float,
        target_price: float,
        account: Optional[Account] = None,
    ) -> dict:
        """Market entry + OCO stop-loss/take-profit bracket, on the demo
        account only. Defaults to the first resolved account; pass `account`
        to target a specific one when trading multiple accounts."""
        if self.creds.env != "demo":
            raise TradovateGuardError("Refusing to place order outside demo env.")
        acct = account or (self.accounts[0] if self.accounts else None)
        if acct is None:
            raise TradovateAuthError("No account resolved — call load_accounts() first.")
        payload = {
            "accountSpec": acct.name,
            "accountId": acct.id,
            "action": action,
            "symbol": contract.name,
            "orderQty": qty,
            "orderType": "Market",
            "isAutomated": True,
            "bracket1": {
                "action": "Sell" if action == "Buy" else "Buy",
                "orderType": "Stop",
                "stopPrice": stop_price,
            },
            "bracket2": {
                "action": "Sell" if action == "Buy" else "Buy",
                "orderType": "Limit",
                "price": target_price,
            },
        }
        resp = requests.post(
            f"{self.rest_base}/order/placeoso",
            json=payload,
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def place_test_trade(
        self,
        contract: Contract,
        account: Account,
        action: str = "Buy",
        qty: int = 1,
        stop_points: float = 4.0,
        target_points: float = 6.0,
    ) -> dict:
        """Places a minimal 1-lot bracket order sized for connectivity
        testing (small stop/target so it resolves quickly), not for actual
        strategy trading. Used by the 'test automation' flow to confirm the
        broker connection, account, and order pipeline all work end to end."""
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
        resp = requests.get(
            f"{self.rest_base}/md/getquote",
            params={"contractId": contract.id},
            headers=self._headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get("last") or data.get("close")
            if price:
                return float(price)
        # Fallback: some Tradovate plans require the WS market-data endpoint
        # instead of a REST quote snapshot. Try contract/item as a last resort.
        resp = requests.get(
            f"{self.rest_base}/contract/item",
            params={"id": contract.id},
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        raise TradovateAuthError(
            "Could not fetch a live quote for the test trade. Your Tradovate "
            "plan may require the market-data WebSocket instead of REST "
            "snapshots — pass an explicit price to place_bracket_order directly."
        )


class TradovateMarketDataStream:
    """Subscribes to real-time quotes for a contract and aggregates them into
    1-minute bars via a supplied BarAggregator, invoking on_bar for each
    closed bar."""

    def __init__(self, md_access_token: str, on_bar: Callable, on_tick: Optional[Callable] = None):
        self.md_access_token = md_access_token
        self.on_bar = on_bar
        self.on_tick = on_tick
        self._ws: Optional[websocket.WebSocket] = None
        self._id_counter = itertools.count(1)
        self._stop = threading.Event()

    def connect(self) -> None:
        self._ws = websocket.create_connection(MD_WS_HOST, timeout=15)
        # Tradovate WS protocol: first frame is "authorize\n<id>\n\n<token>"
        self._ws.send(f"authorize\n0\n\n{self.md_access_token}")
        opening = self._ws.recv()
        if not opening.startswith("o"):
            raise TradovateAuthError(f"Unexpected MD WS handshake: {opening[:100]}")
        auth_frame = self._ws.recv()
        self._check_frame_ok(auth_frame)

    def _check_frame_ok(self, frame: str) -> None:
        if frame.startswith("a"):
            payload = json.loads(frame[1:])
            for msg in payload:
                if msg.get("s") not in (200,):
                    raise TradovateAuthError(f"MD WS error: {msg}")

    def subscribe_quote(self, symbol: str) -> None:
        req_id = next(self._id_counter)
        self._ws.send(f"md/subscribequote\n{req_id}\n\n{json.dumps({'symbol': symbol})}")

    def run_forever(self, bar_aggregator) -> None:
        """Blocking receive loop; call from a dedicated thread."""
        while not self._stop.is_set():
            frame = self._ws.recv()
            if not frame or frame[0] != "a":
                continue
            payload = json.loads(frame[1:])
            for msg in payload:
                if msg.get("e") != "md":
                    continue
                quotes = msg.get("d", {}).get("quotes", [])
                for q in quotes:
                    price = q.get("entries", {}).get("Trade", {}).get("price")
                    ts = q.get("timestamp")
                    if price is None or ts is None:
                        continue
                    if self.on_tick:
                        self.on_tick(price, ts)
                    closed_bar = bar_aggregator.add_tick(price, ts)
                    if closed_bar is not None:
                        self.on_bar(closed_bar)

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close()
