"""
Trader-VIX — Price Fetcher
yfinance wrapper with SQLite caching.
"""
import os
import json
import logging
import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

import config

logger = logging.getLogger(__name__)


def _get_conn():
    os.makedirs(os.path.dirname(config.DB_PATH) if os.path.dirname(config.DB_PATH) else ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS price_cache (
        cache_key TEXT PRIMARY KEY, symbol TEXT, data_json TEXT NOT NULL, cached_at TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def fetch_ohlcv(symbol, start, end, force_refresh=False):
    key = hashlib.sha256(f"{symbol}|{start}|{end}".encode()).hexdigest()[:20]
    if not force_refresh:
        conn = _get_conn()
        row = conn.execute("SELECT data_json, cached_at FROM price_cache WHERE cache_key = ?", (key,)).fetchone()
        conn.close()
        if row:
            cached_at = datetime.fromisoformat(row[1])
            age_h = (datetime.now(timezone.utc) - cached_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            is_hist = datetime.strptime(end, "%Y-%m-%d").date() < datetime.now().date()
            if is_hist or age_h < config.CACHE_TTL_HOURS:
                df = pd.DataFrame(json.loads(row[0]))
                df["Date"] = pd.to_datetime(df["Date"])
                return df.set_index("Date")
    logger.info(f"Fetching {symbol} {start} to {end}")
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, end=end, interval="1d", auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data for {symbol}")
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index.name = "Date"
    df.index = pd.to_datetime(df.index).tz_localize(None)
    to_cache = df.reset_index()
    to_cache["Date"] = to_cache["Date"].astype(str)
    conn = _get_conn()
    conn.execute("INSERT OR REPLACE INTO price_cache (cache_key,symbol,data_json,cached_at) VALUES(?,?,?,?)",
        (key, symbol, json.dumps(to_cache.to_dict(orient="records")), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return df


def compute_sma(symbol, lookback, as_of_date):
    end = (datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=lookback * 2)).strftime("%Y-%m-%d")
    df = fetch_ohlcv(symbol, start, end)
    available = df[df.index <= as_of_date]
    if len(available) < lookback:
        return float(available["Close"].mean())
    return float(available["Close"].iloc[-lookback:].mean())


def get_current_price(symbol):
    data = yf.Ticker(symbol).history(period="1d")
    if data.empty:
        raise ValueError(f"No current price for {symbol}")
    return float(data["Close"].iloc[-1])


def compute_atr(symbol, period=14, as_of_date=None):
    end = (datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") if as_of_date else datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    df = fetch_ohlcv("SPY", start, end)
    if as_of_date:
        df = df[df.index <= as_of_date]
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    atr = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(period).mean().iloc[-1]
    return float(atr / df["Close"].iloc[-1])
