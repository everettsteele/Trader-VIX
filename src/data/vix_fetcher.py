"""
Trader-VIX — VIX Data Fetcher
Pulls CBOE VIX daily data from FRED (St. Louis Fed).
Free, reliable, goes back to 1990.

FRED series: VIXCLS (VIX closing price, daily)
No API key required for basic access.
"""
import os
import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd

import config

logger = logging.getLogger(__name__)

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
VIXCLS_CACHE_KEY = "vix_daily_full"


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH) if os.path.dirname(config.DB_PATH) else ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vix_cache (
            cache_key TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def fetch_vix_history(force_refresh: bool = False) -> pd.Series:
    """
    Returns a daily VIX closing price Series indexed by date (pandas DatetimeIndex).
    Cached in SQLite. Refreshes once per day.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT data_json, cached_at FROM vix_cache WHERE cache_key = ?",
        (VIXCLS_CACHE_KEY,)
    ).fetchone()
    conn.close()

    if row and not force_refresh:
        cached_at = datetime.fromisoformat(row[1])
        age_hours = (datetime.now(timezone.utc) - cached_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if age_hours < 20:
            records = json.loads(row[0])
            s = pd.Series(records)
            s.index = pd.to_datetime(s.index)
            return s.dropna()

    logger.info("Fetching VIX history from FRED")
    try:
        resp = requests.get(FRED_URL, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"FRED fetch failed: {e}")
        if row:
            records = json.loads(row[0])
            s = pd.Series(records)
            s.index = pd.to_datetime(s.index)
            return s.dropna()
        raise

    lines = resp.text.strip().split("\n")
    records = {}
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        date_str, val_str = parts
        try:
            records[date_str] = float(val_str)
        except (ValueError, TypeError):
            continue

    series = pd.Series(records)
    series.index = pd.to_datetime(series.index)
    series = series.dropna().sort_index()

    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO vix_cache (cache_key, data_json, cached_at) VALUES (?, ?, ?)",
        (VIXCLS_CACHE_KEY, json.dumps({str(k.date()): v for k, v in series.items()}),
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()
    logger.info(f"VIX history loaded: {len(series)} records")
    return series


def compute_vix_rank(as_of_date: str, lookback_days: int = None) -> float:
    """
    IV rank: percentile of VIX(as_of_date) vs trailing lookback_days.
    HONESTY GUARANTEE: only uses data available up to and including as_of_date.
    """
    lookback_days = lookback_days or config.VIX_RANK_LOOKBACK_DAYS
    vix = fetch_vix_history()
    target_dt = pd.Timestamp(as_of_date)
    available = vix[vix.index <= target_dt]
    if len(available) < 10:
        return 50.0
    current_vix = available.iloc[-1]
    window = available.iloc[-(lookback_days + 1):-1]
    if len(window) < 20:
        return 50.0
    return float((window < current_vix).sum() / len(window) * 100)


def get_current_vix() -> float:
    return float(fetch_vix_history().iloc[-1])


def get_vix_on_date(date_str: str) -> float:
    vix = fetch_vix_history()
    target = pd.Timestamp(date_str)
    prior = vix[vix.index <= target]
    if prior.empty:
        return float("nan")
    return float(prior.iloc[-1])
