"""
Trader-VIX — Bull Put Spread Strategy (Swing, 30-45 DTE)

Entry: once daily at 4:05 PM ET.
Exit: 50% profit | 21 DTE | 2x loss | gap stop.
Defined risk: assignment impossible with vertical spreads.
"""
import logging
from datetime import datetime, timedelta

import config
from src.data.vix_fetcher import compute_vix_rank, get_vix_on_date
from src.data.price_fetcher import fetch_ohlcv, compute_sma
from src.data.options_pricer import estimate_spread_credit, find_put_strike_for_delta
from src.data.fomc_calendar import is_fomc_blackout

logger = logging.getLogger(__name__)


class BullPutSpread:
    def __init__(self, id, symbol, short_strike, long_strike, expiration,
                 dte_at_open, credit_received, margin_held, num_contracts,
                 opened_date, order_id="", status="open"):
        self.id = id
        self.symbol = symbol
        self.short_strike = short_strike
        self.long_strike = long_strike
        self.expiration = expiration
        self.dte_at_open = dte_at_open
        self.credit_received = credit_received
        self.margin_held = margin_held
        self.num_contracts = num_contracts
        self.opened_date = opened_date
        self.order_id = order_id
        self.status = status

    @property
    def profit_target(self):
        return self.credit_received * config.SWING_PROFIT_TARGET

    @property
    def loss_limit(self):
        return self.credit_received * config.SWING_LOSS_LIMIT

    def days_to_expiration(self, as_of):
        return (datetime.strptime(self.expiration, "%Y-%m-%d") - datetime.strptime(as_of, "%Y-%m-%d")).days


def evaluate_entry(as_of_date, underlying_price=None):
    result = {"should_open": False, "reason": "", "spread": None}

    vix_rank = compute_vix_rank(as_of_date)
    vix = get_vix_on_date(as_of_date)
    result["vix_rank"] = round(vix_rank, 1)
    result["vix"] = round(vix, 2)

    if vix_rank < config.VIX_RANK_MIN:
        result["reason"] = f"VIX rank {vix_rank:.0f} below minimum {config.VIX_RANK_MIN} — premium too cheap"
        return result

    sma_200 = compute_sma("SPY", config.SPY_SMA_LOOKBACK, as_of_date)
    if underlying_price is None:
        end = (datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        df = fetch_ohlcv("SPY", as_of_date, end)
        available = df[df.index <= as_of_date]
        underlying_price = float(available["Close"].iloc[-1]) if not available.empty else sma_200

    if underlying_price < sma_200 * (1 - config.SPY_MAX_BELOW_SMA_PCT):
        pct = (sma_200 - underlying_price) / sma_200 * 100
        result["reason"] = f"SPY {pct:.1f}% below 200d SMA — freefall filter active"
        return result

    if is_fomc_blackout(as_of_date, config.FOMC_BLACKOUT_DAYS):
        result["reason"] = "FOMC blackout window"
        return result

    T = config.SWING_MIN_DTE / 365.0
    short_strike = find_put_strike_for_delta(underlying_price, T, vix/100, config.SWING_TARGET_DELTA)
    long_strike = short_strike - config.SWING_SPREAD_WIDTH
    spread = estimate_spread_credit(underlying_price, short_strike, long_strike, T, vix)

    if spread["net_credit"] < config.SWING_MIN_CREDIT:
        result["reason"] = f"Net credit ${spread['net_credit']:.2f} below minimum ${config.SWING_MIN_CREDIT:.2f}"
        return result

    target_dte = (config.SWING_MIN_DTE + config.SWING_MAX_DTE) // 2
    expiration = (datetime.strptime(as_of_date, "%Y-%m-%d") + timedelta(days=target_dte)).strftime("%Y-%m-%d")

    result["should_open"] = True
    result["spread"] = spread
    result["expiration"] = expiration
    result["dte"] = target_dte
    return result


def evaluate_exit(position, current_mark, as_of_date):
    result = {"should_close": False, "reason": "", "urgency": "normal"}
    dte = position.days_to_expiration(as_of_date)

    if dte <= config.SWING_TIME_STOP_DTE:
        result.update(should_close=True, reason=f"Time stop: {dte} DTE",
                      urgency="urgent" if dte <= 7 else "normal")
        return result

    pnl = position.credit_received - current_mark
    if pnl >= position.profit_target:
        result.update(should_close=True, reason=f"50% profit target ({pnl/position.credit_received*100:.0f}% captured)")
        return result

    loss = current_mark - position.credit_received
    if loss >= position.loss_limit:
        result.update(should_close=True, reason=f"2x loss stop (mark=${current_mark:.2f})", urgency="urgent")
        return result

    return result
