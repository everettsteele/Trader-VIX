"""
Trader-VIX — Configuration
All settings driven from environment variables.
Runs on Railway. Both strategies (swing + 0DTE) in one process.
"""
import os
from dotenv import load_dotenv

load_dotenv(".env", override=True)

# ── Tastytrade ────────────────────────────────────────────────────────────────
TASTYTRADE_USERNAME    = os.getenv("TASTYTRADE_USERNAME", "")
TASTYTRADE_PASSWORD    = os.getenv("TASTYTRADE_PASSWORD", "")
TASTYTRADE_ACCOUNT_NUM = os.getenv("TASTYTRADE_ACCOUNT_NUM", "")
TASTYTRADE_PAPER       = os.getenv("TASTYTRADE_PAPER", "false").lower() == "true"

# DRY_RUN: authenticate with production and pull real market data,
# but intercept all order placement calls — nothing is actually traded.
# Use this to paper trade against your live account without a sandbox.
# Set to false only when ready to execute real trades.
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

TASTYTRADE_BASE_URL = (
    "https://api.cert.tastyworks.com" if TASTYTRADE_PAPER
    else "https://api.tastyworks.com"
)

# ── Strategy toggles ──────────────────────────────────────────────────────────
SWING_ENABLED = os.getenv("SWING_ENABLED", "true").lower() == "true"
ZEDTE_ENABLED = os.getenv("ZEDTE_ENABLED", "true").lower() == "true"
CARRY_ENABLED = os.getenv("CARRY_ENABLED", "false").lower() == "true"

# ── Capital allocation ────────────────────────────────────────────────────────
TOTAL_CAPITAL         = float(os.getenv("TOTAL_CAPITAL", "20000"))
SWING_CAPITAL_PCT     = float(os.getenv("SWING_CAPITAL_PCT", "0.60"))
ZEDTE_CAPITAL_PCT     = float(os.getenv("ZEDTE_CAPITAL_PCT", "0.20"))
CARRY_CAPITAL_PCT     = float(os.getenv("CARRY_CAPITAL_PCT", "0.20"))

# ── Swing strategy (30-45 DTE bull put spreads) ───────────────────────────────
SWING_MAX_SPREADS      = int(os.getenv("SWING_MAX_SPREADS", "4"))
SWING_SPREAD_WIDTH     = int(os.getenv("SWING_SPREAD_WIDTH", "5"))
SWING_TARGET_DELTA     = float(os.getenv("SWING_TARGET_DELTA", "0.30"))
SWING_MIN_DTE          = int(os.getenv("SWING_MIN_DTE", "30"))
SWING_MAX_DTE          = int(os.getenv("SWING_MAX_DTE", "45"))
SWING_PROFIT_TARGET    = float(os.getenv("SWING_PROFIT_TARGET", "0.50"))
SWING_LOSS_LIMIT       = float(os.getenv("SWING_LOSS_LIMIT", "2.0"))
SWING_TIME_STOP_DTE    = int(os.getenv("SWING_TIME_STOP_DTE", "21"))
SWING_MIN_CREDIT       = float(os.getenv("SWING_MIN_CREDIT", "0.80"))
SWING_EVAL_HOUR        = int(os.getenv("SWING_EVAL_HOUR", "16"))
SWING_EVAL_MINUTE      = int(os.getenv("SWING_EVAL_MINUTE", "5"))

# ── 0DTE strategy (iron condors) ──────────────────────────────────────────────
ZEDTE_MAX_CONDORS       = int(os.getenv("ZEDTE_MAX_CONDORS", "4"))
ZEDTE_SPREAD_WIDTH      = int(os.getenv("ZEDTE_SPREAD_WIDTH", "5"))
ZEDTE_TARGET_DELTA      = float(os.getenv("ZEDTE_TARGET_DELTA", "0.10"))
ZEDTE_MIN_CREDIT        = float(os.getenv("ZEDTE_MIN_CREDIT", "0.40"))
ZEDTE_PROFIT_TARGET     = float(os.getenv("ZEDTE_PROFIT_TARGET", "0.50"))
ZEDTE_LOSS_LIMIT        = float(os.getenv("ZEDTE_LOSS_LIMIT", "2.0"))
ZEDTE_ENTRY_HOUR        = int(os.getenv("ZEDTE_ENTRY_HOUR", "9"))
ZEDTE_ENTRY_MINUTE      = int(os.getenv("ZEDTE_ENTRY_MINUTE", "45"))
ZEDTE_HARD_CLOSE_HOUR   = int(os.getenv("ZEDTE_HARD_CLOSE_HOUR", "15"))
ZEDTE_HARD_CLOSE_MINUTE = int(os.getenv("ZEDTE_HARD_CLOSE_MINUTE", "45"))
ZEDTE_POLL_INTERVAL_SEC = int(os.getenv("ZEDTE_POLL_INTERVAL_SEC", "120"))
ZEDTE_FINAL_POLL_SEC    = int(os.getenv("ZEDTE_FINAL_POLL_SEC", "60"))

# ── Forex carry strategy ──────────────────────────────────────────────────────
OANDA_API_KEY        = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID     = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_PAPER          = os.getenv("OANDA_PAPER", "true").lower() == "true"
CARRY_MAX_POSITIONS  = int(os.getenv("CARRY_MAX_POSITIONS", "2"))
CARRY_STOP_PCT       = float(os.getenv("CARRY_STOP_PCT", "0.10"))
CARRY_MIN_HOLD       = float(os.getenv("CARRY_MIN_HOLD", "1.0"))
CARRY_LEVERAGE       = float(os.getenv("CARRY_LEVERAGE", "3.0"))
CARRY_REBALANCE_DAY  = int(os.getenv("CARRY_REBALANCE_DAY", "1"))

# ── Entry filters ────────────────────────────────────────────────────────────────
VIX_RANK_MIN           = float(os.getenv("VIX_RANK_MIN", "50"))
VIX_RANK_LOOKBACK_DAYS = int(os.getenv("VIX_RANK_LOOKBACK_DAYS", "252"))
SPY_SMA_LOOKBACK       = int(os.getenv("SPY_SMA_LOOKBACK", "200"))
SPY_MAX_BELOW_SMA_PCT  = float(os.getenv("SPY_MAX_BELOW_SMA_PCT", "0.15"))
FOMC_BLACKOUT_DAYS     = int(os.getenv("FOMC_BLACKOUT_DAYS", "5"))

ZEDTE_VIX_MIN     = float(os.getenv("ZEDTE_VIX_MIN", "14"))
ZEDTE_VIX_MAX     = float(os.getenv("ZEDTE_VIX_MAX", "45"))
ZEDTE_MIN_ATR_PCT = float(os.getenv("ZEDTE_MIN_ATR_PCT", "0.003"))

# ── Portfolio risk ────────────────────────────────────────────────────────────
MAX_PORTFOLIO_DRAWDOWN = float(os.getenv("MAX_PORTFOLIO_DRAWDOWN", "0.15"))
DAILY_LOSS_LIMIT       = float(os.getenv("DAILY_LOSS_LIMIT", "0.05"))

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "meridian")
SECRET_KEY         = os.getenv("SECRET_KEY", "change-me-in-prod")
PORT               = int(os.getenv("PORT", "3005"))

# ── Notifications ─────────────────────────────────────────────────────────────
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL", "everett@tryrebuilt.com")
FROM_EMAIL     = os.getenv("FROM_EMAIL", "emmett@getrebuilt.app")

# ── Data / cache ──────────────────────────────────────────────────────────────
DB_PATH         = os.getenv("DB_PATH", "data/trader.db")
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "4"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
