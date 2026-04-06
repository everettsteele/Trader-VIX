"""
Trader-VIX — Tastytrade API Client

Paper: api.cert.tastyworks.com  |  Live: api.tastyworks.com
Auth: username/password → session token (~24h, auto-refreshed).
All options orders are limit orders. Market orders on options are disabled.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
import config

logger = logging.getLogger(__name__)


class TastytradeClient:
    def __init__(self):
        self.base_url = config.TASTYTRADE_BASE_URL
        self.session_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.account_number = config.TASTYTRADE_ACCOUNT_NUM
        self._authenticate()

    def _authenticate(self):
        if not config.TASTYTRADE_USERNAME or not config.TASTYTRADE_PASSWORD:
            raise ValueError("TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD must be set before trading.")
        logger.info(f"Authenticating Tastytrade ({'PAPER' if config.TASTYTRADE_PAPER else 'LIVE'})")
        resp = requests.post(f"{self.base_url}/sessions",
            json={"login": config.TASTYTRADE_USERNAME, "password": config.TASTYTRADE_PASSWORD, "remember-me": True},
            timeout=15)
        resp.raise_for_status()
        self.session_token = resp.json()["data"]["session-token"]
        self.token_expiry = datetime.now(timezone.utc) + timedelta(hours=23)
        logger.info("Tastytrade auth OK")

    def _ensure_auth(self):
        if not self.session_token or datetime.now(timezone.utc) >= self.token_expiry:
            self._authenticate()

    def _headers(self):
        self._ensure_auth()
        return {"Authorization": self.session_token, "Content-Type": "application/json"}

    def _get(self, path):
        resp = requests.get(f"{self.base_url}{path}", headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body):
        resp = requests.post(f"{self.base_url}{path}", json=body, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path):
        resp = requests.delete(f"{self.base_url}{path}", headers=self._headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_account(self):
        data = self._get(f"/accounts/{self.account_number}/balances")["data"]
        return {
            "cash": float(data.get("cash-balance", 0)),
            "net_liquidating_value": float(data.get("net-liquidating-value", 0)),
            "buying_power": float(data.get("derivative-buying-power", 0)),
            "mode": "PAPER" if config.TASTYTRADE_PAPER else "LIVE",
        }

    def get_positions(self):
        items = self._get(f"/accounts/{self.account_number}/positions")["data"]["items"]
        return [{"symbol": p.get("symbol"), "quantity": int(p.get("quantity", 0)),
                 "average_open_price": float(p.get("average-open-price", 0)),
                 "expires_at": p.get("expires-at", "")} for p in items]

    def _occ(self, symbol, expiration, opt_type, strike):
        exp = datetime.strptime(expiration, "%Y-%m-%d").strftime("%y%m%d")
        return f"{symbol}{exp}{opt_type}{int(strike * 1000):08d}"

    def place_spread_order(self, symbol, short_strike, long_strike, expiration,
                           net_credit, num_contracts, is_put_spread=True, action="open"):
        opt = "P" if is_put_spread else "C"
        sa = "Sell to Open" if action == "open" else "Buy to Close"
        la = "Buy to Open"  if action == "open" else "Sell to Close"
        body = {"order-type": "Limit", "time-in-force": "Day",
                "price": round(net_credit, 2),
                "price-effect": "Credit" if action == "open" else "Debit",
                "legs": [
                    {"instrument-type": "Equity Option", "symbol": self._occ(symbol, expiration, opt, short_strike), "quantity": num_contracts, "action": sa},
                    {"instrument-type": "Equity Option", "symbol": self._occ(symbol, expiration, opt, long_strike),  "quantity": num_contracts, "action": la},
                ]}
        result = self._post(f"/accounts/{self.account_number}/orders", body)
        return {"order_id": str(result["data"]["order"].get("id", "")),
                "status": result["data"]["order"].get("status", "")}

    def place_iron_condor_order(self, symbol, put_short, put_long, call_short, call_long,
                                expiration, net_credit, num_contracts):
        body = {"order-type": "Limit", "time-in-force": "Day",
                "price": round(net_credit, 2), "price-effect": "Credit",
                "legs": [
                    {"instrument-type": "Equity Option", "symbol": self._occ(symbol, expiration, "P", put_short),  "quantity": num_contracts, "action": "Sell to Open"},
                    {"instrument-type": "Equity Option", "symbol": self._occ(symbol, expiration, "P", put_long),   "quantity": num_contracts, "action": "Buy to Open"},
                    {"instrument-type": "Equity Option", "symbol": self._occ(symbol, expiration, "C", call_short), "quantity": num_contracts, "action": "Sell to Open"},
                    {"instrument-type": "Equity Option", "symbol": self._occ(symbol, expiration, "C", call_long),  "quantity": num_contracts, "action": "Buy to Open"},
                ]}
        result = self._post(f"/accounts/{self.account_number}/orders", body)
        return {"order_id": str(result["data"]["order"].get("id", "")), "status": result["data"]["order"].get("status", "")}

    def place_contingency_orders(self, symbol, put_short, put_long, call_short, call_long,
                                  expiration, credit_received, num_contracts):
        """Place profit + loss exit orders at broker immediately after condor opens."""
        results = {}
        for name, debit in [("profit_order", round(credit_received * (1 - config.ZEDTE_PROFIT_TARGET), 2)),
                             ("loss_order",   round(credit_received * (1 + config.ZEDTE_LOSS_LIMIT), 2))]:
            try:
                results[name] = self.place_iron_condor_order(
                    symbol, put_long, put_short, call_long, call_short, expiration, debit, num_contracts)
            except Exception as e:
                logger.error(f"Contingency {name} failed: {e}")
                results[name] = {"error": str(e)}
        return results

    def cancel_order(self, order_id):
        try:
            return self._delete(f"/accounts/{self.account_number}/orders/{order_id}")
        except Exception as e:
            return {"error": str(e)}
