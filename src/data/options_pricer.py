"""
Trader-VIX — Options Pricer
Black-Scholes pricing for synthetic backtest.

SYNTHETIC LABEL: Black-Scholes with VIX as IV proxy, bid fills, 20% put skew haircut.
Conservative floor estimate. Real performance should equal or exceed this.
"""
import math
import logging
from scipy.stats import norm

logger = logging.getLogger(__name__)

PUT_SKEW_HAIRCUT = 0.20
HALF_SPREAD_EST  = 0.05
SLIPPAGE_PER_LEG = 0.05


def black_scholes_put(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return max((K * math.exp(-r * T) * norm.cdf(-d2)) - (S * norm.cdf(-d1)), 0.0)


def black_scholes_call(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return max((S * norm.cdf(d1)) - (K * math.exp(-r * T) * norm.cdf(d2)), 0.0)


def put_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return -1.0 if K > S else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1) - 1.0


def call_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1)


def find_put_strike_for_delta(S, T, sigma, target_delta=0.30, r=0.05, strike_increment=1.0):
    best_strike, best_diff = S, float("inf")
    k = S * 0.995
    while k >= S * 0.80:
        d = abs(put_delta(S, k, T, r, sigma))
        diff = abs(d - target_delta)
        if diff < best_diff:
            best_diff, best_strike = diff, k
        k -= strike_increment
    return round(best_strike / strike_increment) * strike_increment


def find_call_strike_for_delta(S, T, sigma, target_delta=0.10, r=0.05, strike_increment=1.0):
    best_strike, best_diff = S, float("inf")
    k = S * 1.005
    while k <= S * 1.20:
        d = call_delta(S, k, T, r, sigma)
        diff = abs(d - target_delta)
        if diff < best_diff:
            best_diff, best_strike = diff, k
        k += strike_increment
    return round(best_strike / strike_increment) * strike_increment


def estimate_spread_credit(S, short_strike, long_strike, T, vix, r=0.05, is_put_spread=True):
    sigma = vix / 100.0
    if is_put_spread:
        short_mid = black_scholes_put(S, short_strike, T, r, sigma * (1 + PUT_SKEW_HAIRCUT))
        long_mid  = black_scholes_put(S, long_strike,  T, r, sigma * (1 + PUT_SKEW_HAIRCUT * 1.2))
    else:
        short_mid = black_scholes_call(S, short_strike, T, r, sigma)
        long_mid  = black_scholes_call(S, long_strike,  T, r, sigma)
    short_fill = max(short_mid - HALF_SPREAD_EST - SLIPPAGE_PER_LEG, 0.01)
    long_fill  = long_mid + HALF_SPREAD_EST + SLIPPAGE_PER_LEG
    net_credit = short_fill - long_fill
    spread_width = abs(short_strike - long_strike)
    max_loss = spread_width - max(net_credit, 0)
    return {
        "short_strike": short_strike, "long_strike": long_strike,
        "short_price": round(short_fill, 4), "long_price": round(long_fill, 4),
        "net_credit": round(net_credit, 4),
        "max_loss_per_share": round(max_loss, 4),
        "margin_required": round(max_loss * 100, 2),
        "spread_width": spread_width,
    }


def estimate_iron_condor_credit(S, put_short, put_long, call_short, call_long, T, vix, r=0.05):
    put_s  = estimate_spread_credit(S, put_short, put_long, T, vix, r, is_put_spread=True)
    call_s = estimate_spread_credit(S, call_short, call_long, T, vix, r, is_put_spread=False)
    net_credit = put_s["net_credit"] + call_s["net_credit"]
    max_loss = max(put_s["spread_width"], call_s["spread_width"]) - max(net_credit, 0)
    return {
        "put_short": put_short, "put_long": put_long,
        "call_short": call_short, "call_long": call_long,
        "put_credit": put_s["net_credit"], "call_credit": call_s["net_credit"],
        "net_credit": round(net_credit, 4),
        "max_loss_per_share": round(max_loss, 4),
        "margin_required": round(max_loss * 100, 2),
    }
