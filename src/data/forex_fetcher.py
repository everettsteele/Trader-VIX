"""
Trader-VIX — Forex Data Fetcher
OANDA API wrapper for real-time and historical forex rates.
Also computes carry differentials from central bank rate data.

OANDA practice account = paper trading (free, same API as live).
OANDA live account = real money (requires funded account).
"""
import logging
import json
import sqlite3
from datetime import datetime, timezone, timedelta

import requests

import config

logger = logging.getLogger(__name__)

# Central bank benchmark rates — updated manually when they change.
# Source: centralbanks.org / official central bank websites.
# Last updated: April 2026
CENTRAL_BANK_RATES = {
    "USD": 4.50,   # Federal Reserve
    "AUD": 4.10,   # Reserve Bank of Australia
    "NZD": 3.50,   # Reserve Bank of New Zealand
    "GBP": 4.50,   # Bank of England
    "CAD": 2.75,   # Bank of Canada
    "EUR": 2.40,   # European Central Bank
    "CHF": 0.25,   # Swiss National Bank
    "JPY": 0.50,   # Bank of Japan
    "MXN": 8.50,   # Banco de Mexico (higher yield, higher vol)
    "NOK": 4.50,   # Norges Bank
}

# Pairs we trade: format is "BASE_QUOTE" — we are long BASE, short QUOTE
# Carry = BASE rate - QUOTE rate
# We only trade pairs where carry >= MIN_CARRY_DIFFERENTIAL
TRADEABLE_PAIRS = [
    "AUD_JPY",  # carry ~3.6%
    "NZD_JPY",  # carry ~3.0%
    "USD_JPY",  # carry ~4.0%
    "GBP_JPY",  # carry ~4.0%
    "AUD_CHF",  # carry ~3.85%
    "NZD_CHF",  # carry ~3.25%
    "USD_CHF",  # carry ~4.25%
    "CAD_JPY",  # carry ~2.25%
]

MIN_CARRY_DIFFERENTIAL = 2.0  # minimum annualized % to bother trading


def compute_carry_differential(base: str, quote: str) -> float:
    """Annual carry differential = base rate - quote rate."""
    base_rate  = CENTRAL_BANK_RATES.get(base, 0.0)
    quote_rate = CENTRAL_BANK_RATES.get(quote, 0.0)
    return base_rate - quote_rate


def rank_carry_pairs() -> list[dict]:
    """
    Rank all tradeable pairs by carry differential.
    Returns list sorted by carry descending, filtering out pairs below minimum.
    """
    ranked = []
    for pair in TRADEABLE_PAIRS:
        base, quote = pair.split("_")
        carry = compute_carry_differential(base, quote)
        if carry >= MIN_CARRY_DIFFERENTIAL:
            ranked.append({
                "pair": pair,
                "base": base,
                "quote": quote,
                "carry_pct": round(carry, 2),
                "base_rate": CENTRAL_BANK_RATES.get(base, 0),
                "quote_rate": CENTRAL_BANK_RATES.get(quote, 0),
            })
    ranked.sort(key=lambda x: x["carry_pct"], reverse=True)
    return ranked


def get_current_price(pair: str, api_key: str, account_id: str, paper: bool = True) -> dict:
    """
    Get current bid/ask for a forex pair from OANDA.
    Returns dict with bid, ask, mid, spread.
    """
    base_url = "https://api-fxpractice.oanda.com" if paper else "https://api-fxtrade.oanda.com"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    resp = requests.get(
        f"{base_url}/v3/accounts/{account_id}/pricing",
        headers=headers,
        params={"instruments": pair},
        timeout=10,
    )
    resp.raise_for_status()
    price_data = resp.json()["prices"][0]

    bid = float(price_data["bids"][0]["price"])
    ask = float(price_data["asks"][0]["price"])
    mid = (bid + ask) / 2
    spread = ask - bid

    return {"pair": pair, "bid": bid, "ask": ask, "mid": mid, "spread": spread}


def get_historical_candles(
    pair: str,
    granularity: str,  # "D" = daily, "H4" = 4-hour
    count: int,
    api_key: str,
    paper: bool = True,
) -> list[dict]:
    """
    Fetch historical OHLC candles from OANDA.
    Returns list of {time, open, high, low, close} dicts.
    """
    base_url = "https://api-fxpractice.oanda.com" if paper else "https://api-fxtrade.oanda.com"
    headers = {"Authorization": f"Bearer {api_key}"}

    resp = requests.get(
        f"{base_url}/v3/instruments/{pair}/candles",
        headers=headers,
        params={"granularity": granularity, "count": count, "price": "M"},
        timeout=15,
    )
    resp.raise_for_status()
    candles = resp.json().get("candles", [])

    result = []
    for c in candles:
        if c.get("complete", True):
            mid = c.get("mid", {})
            result.append({
                "time": c["time"][:10],
                "open":  float(mid.get("o", 0)),
                "high":  float(mid.get("h", 0)),
                "low":   float(mid.get("l", 0)),
                "close": float(mid.get("c", 0)),
            })
    return result


def compute_volatility_adjusted_carry(pair: str, api_key: str, paper: bool = True) -> float:
    """
    Risk-adjusted carry: carry differential / annualized volatility.
    Higher is better — more carry per unit of risk.
    Used for position sizing and pair selection.
    """
    try:
        candles = get_historical_candles(pair, "D", 60, api_key, paper)
        if len(candles) < 20:
            return 0.0
        import numpy as np
        closes = [c["close"] for c in candles]
        daily_rets = [closes[i] / closes[i-1] - 1 for i in range(1, len(closes))]
        vol = float(np.std(daily_rets) * (252 ** 0.5))
        base, quote = pair.split("_")
        carry = compute_carry_differential(base, quote) / 100.0
        return round(carry / vol if vol > 0 else 0.0, 3)
    except Exception as e:
        logger.warning(f"Vol-adjusted carry failed for {pair}: {e}")
        return 0.0
