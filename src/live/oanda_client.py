"""
Trader-VIX — OANDA API Client

REST wrapper for OANDA's v20 API.
Practice: api-fxpractice.oanda.com (free paper trading)
Live:     api-fxtrade.oanda.com

OANDA accounts:
- Create free practice account at oanda.com
- Get API token from: My Account > Manage API Access
- Account ID visible in the practice dashboard

All forex orders are market orders (spot FX fills immediately at quoted price).
Limit orders supported for entries but not required for this strategy.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class OANDAClient:
    def __init__(self, api_key: str, account_id: str, paper: bool = True):
        self.api_key = api_key
        self.account_id = account_id
        self.paper = paper
        self.base_url = (
            "https://api-fxpractice.oanda.com"
            if paper else
            "https://api-fxtrade.oanda.com"
        )
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        logger.info(f"OANDAClient initialized ({'PRACTICE' if paper else 'LIVE'})")

    def _get(self, path: str, params: dict = None) -> dict:
        resp = requests.get(
            f"{self.base_url}{path}",
            headers=self.headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = requests.post(
            f"{self.base_url}{path}",
            headers=self.headers,
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, body: dict) -> dict:
        resp = requests.put(
            f"{self.base_url}{path}",
            headers=self.headers,
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        data = self._get(f"/v3/accounts/{self.account_id}/summary")["account"]
        return {
            "balance": float(data["balance"]),
            "nav": float(data["NAV"]),
            "unrealized_pl": float(data["unrealizedPL"]),
            "open_trade_count": int(data["openTradeCount"]),
            "margin_used": float(data["marginUsed"]),
            "margin_available": float(data["marginAvailable"]),
            "mode": "PRACTICE" if self.paper else "LIVE",
        }

    def get_open_trades(self) -> list[dict]:
        data = self._get(f"/v3/accounts/{self.account_id}/openTrades")
        trades = []
        for t in data.get("trades", []):
            trades.append({
                "trade_id": t["id"],
                "instrument": t["instrument"],
                "units": int(t["currentUnits"]),
                "open_price": float(t["price"]),
                "unrealized_pl": float(t["unrealizedPL"]),
                "open_time": t["openTime"][:10],
            })
        return trades

    # ── Pricing ───────────────────────────────────────────────────────────────

    def get_price(self, instrument: str) -> dict:
        data = self._get(
            f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": instrument},
        )
        price = data["prices"][0]
        bid = float(price["bids"][0]["price"])
        ask = float(price["asks"][0]["price"])
        return {
            "instrument": instrument,
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2, 5),
            "spread": round(ask - bid, 5),
            "tradeable": price["tradeable"],
        }

    # ── Orders ────────────────────────────────────────────────────────────────

    def market_order(self, instrument: str, units: int, stop_loss_price: float = None) -> dict:
        """
        Place a market order.
        units > 0 = buy (long base currency)
        units < 0 = sell (short base currency)

        stop_loss_price: optional hard stop loss attached to the order.
        """
        order = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",  # fill or kill
                "positionFill": "DEFAULT",
            }
        }

        if stop_loss_price is not None:
            order["order"]["stopLossOnFill"] = {
                "price": f"{stop_loss_price:.5f}",
                "timeInForce": "GTC",
            }

        logger.info(f"[{'PRACTICE' if self.paper else 'LIVE'}] Market order: {units} {instrument}")
        result = self._post(f"/v3/accounts/{self.account_id}/orders", order)

        fill = result.get("orderFillTransaction", {})
        return {
            "order_id": fill.get("id", ""),
            "trade_id": fill.get("tradeOpened", {}).get("tradeID", ""),
            "instrument": instrument,
            "units": units,
            "fill_price": float(fill.get("price", 0)),
            "status": "filled" if fill else "pending",
        }

    def close_trade(self, trade_id: str) -> dict:
        """Close a specific trade by ID."""
        logger.info(f"Closing trade {trade_id}")
        try:
            result = self._put(
                f"/v3/accounts/{self.account_id}/trades/{trade_id}/close",
                {"units": "ALL"},
            )
            fill = result.get("orderFillTransaction", {})
            return {
                "trade_id": trade_id,
                "close_price": float(fill.get("price", 0)),
                "realized_pl": float(fill.get("pl", 0)),
                "status": "closed",
            }
        except Exception as e:
            logger.error(f"Close trade {trade_id} failed: {e}")
            return {"trade_id": trade_id, "error": str(e)}

    def close_all_positions(self) -> list[dict]:
        """Emergency close all open trades."""
        logger.warning("CLOSING ALL FOREX POSITIONS")
        trades = self.get_open_trades()
        results = []
        for trade in trades:
            result = self.close_trade(trade["trade_id"])
            results.append(result)
        return results
