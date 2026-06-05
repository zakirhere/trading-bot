from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from . import config


@dataclass
class OrderResult:
    broker_order_id: str
    status: str
    symbol: str
    side: str
    qty: float
    raw: dict


class AlpacaBroker:
    def __init__(self, cfg: config.AlpacaConfig):
        self.cfg = cfg
        self._client = httpx.Client(
            base_url=cfg.base_url,
            headers={
                "APCA-API-KEY-ID": cfg.key_id,
                "APCA-API-SECRET-KEY": cfg.secret_key,
            },
            timeout=10.0,
        )

    def close(self) -> None:
        self._client.close()

    def get_account(self) -> dict[str, Any]:
        r = self._client.get("/v2/account")
        r.raise_for_status()
        return r.json()

    def get_clock(self) -> dict[str, Any]:
        r = self._client.get("/v2/clock")
        r.raise_for_status()
        return r.json()

    def get_positions(self) -> list[dict[str, Any]]:
        r = self._client.get("/v2/positions")
        r.raise_for_status()
        return r.json()

    def submit_market_order(
        self,
        *,
        symbol: str,
        qty: float,
        side: str,
        client_order_id: str,
        time_in_force: str = "day",
    ) -> OrderResult:
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "market",
            "time_in_force": time_in_force,
            "client_order_id": client_order_id,
        }
        r = self._client.post("/v2/orders", json=payload)
        r.raise_for_status()
        j = r.json()
        return OrderResult(
            broker_order_id=j["id"],
            status=j["status"],
            symbol=j["symbol"],
            side=j["side"],
            qty=float(j["qty"]),
            raw=j,
        )
