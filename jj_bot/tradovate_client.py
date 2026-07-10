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

    def load_account(self) -> None:
        resp = requests.get(f"{self.rest_base}/account/list", headers=self._headers(), timeout=15)
        resp.raise_for_status()
        accounts = resp.json()
        if not accounts:
            raise TradovateAuthError("No Tradovate accounts found for this user.")
        match = None
        if self.creds.account_name:
            match = next((a for a in accounts if a.get("name") == self.creds.account_name), None)
        account = match or accounts[0]
        if not account.get("active", True):
            raise TradovateAuthError(f"Account {account.get('name')} is not active.")
        self.account_id = account["id"]
        self.account_spec = account["name"]

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
    ) -> dict:
        """Market entry + OCO stop-loss/take-profit bracket, on the demo account only."""
        if self.creds.env != "demo":
            raise TradovateGuardError("Refusing to place order outside demo env.")
        payload = {
            "accountSpec": self.account_spec,
            "accountId": self.account_id,
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
