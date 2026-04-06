"""
Trader-VIX — 0DTE Iron Condor Strategy

Entry: 9:45 AM ET. Exit: 50% profit | 2x loss | 3:45 PM hard close.
Broker-side contingency orders placed at open.
10-delta wings, 5-wide spreads on SPY.
"""
import logging
from datetime import datetime

import config
from src.data.vix_fetcher import get_current_vix, compute_vix_rank
from src.data.price_fetcher import compute_atr
from src.data.options_pricer import (
    estimate_iron_condor_credit, find_put_strike_for_delta, find_call_strike_for_delta
)
from src.data.fomc_calendar import is_fomc_blackout

logger = logging.getLogger(__name__)


def evaluate_0dte_entry(as_of_date, spy_price, vix=None):
    result = {"should_open": False, "reason": "", "condor": None, "go_no_go_reasons": []}
    checks = []

    if vix is None:
        vix = get_current_vix()

    if vix < config.ZEDTE_VIX_MIN:
        checks.append(f"FAIL: VIX {vix:.1f} below minimum {config.ZEDTE_VIX_MIN} — premium too thin")
    elif vix > config.ZEDTE_VIX_MAX:
        checks.append(f"FAIL: VIX {vix:.1f} above maximum {config.ZEDTE_VIX_MAX} — too chaotic")
    else:
        checks.append(f"PASS: VIX {vix:.1f} in range")

    vix_rank = compute_vix_rank(as_of_date)
    checks.append(f"{'PASS' if vix_rank >= 40 else 'FAIL'}: VIX rank {vix_rank:.0f}")

    atr_pct = compute_atr("SPY", period=5, as_of_date=as_of_date)
    checks.append(f"{'PASS' if atr_pct >= config.ZEDTE_MIN_ATR_PCT else 'FAIL'}: ATR {atr_pct:.3%}")

    if is_fomc_blackout(as_of_date, config.FOMC_BLACKOUT_DAYS):
        checks.append("FAIL: FOMC blackout")
    else:
        checks.append("PASS: No FOMC blackout")

    result["go_no_go_reasons"] = checks
    if any(c.startswith("FAIL") for c in checks):
        result["reason"] = " | ".join(c for c in checks if c.startswith("FAIL"))
        return result

    T = max(1 / 365.0, 0.003)
    put_short  = find_put_strike_for_delta(spy_price, T, vix/100, config.ZEDTE_TARGET_DELTA)
    put_long   = put_short - config.ZEDTE_SPREAD_WIDTH
    call_short = find_call_strike_for_delta(spy_price, T, vix/100, config.ZEDTE_TARGET_DELTA)
    call_long  = call_short + config.ZEDTE_SPREAD_WIDTH

    condor = estimate_iron_condor_credit(spy_price, put_short, put_long, call_short, call_long, T, vix)

    if condor["net_credit"] < config.ZEDTE_MIN_CREDIT:
        result["reason"] = f"Net credit ${condor['net_credit']:.2f} below minimum ${config.ZEDTE_MIN_CREDIT:.2f}"
        return result

    result["should_open"] = True
    result["condor"] = condor
    result["vix"] = vix
    result["vix_rank"] = vix_rank
    return result


def evaluate_0dte_exit(credit_received, current_mark, current_time):
    result = {"should_close": False, "reason": "", "urgency": "normal"}
    hard_close = current_time.replace(hour=config.ZEDTE_HARD_CLOSE_HOUR,
                                       minute=config.ZEDTE_HARD_CLOSE_MINUTE, second=0, microsecond=0)
    warn_time  = current_time.replace(hour=config.ZEDTE_HARD_CLOSE_HOUR,
                                       minute=config.ZEDTE_HARD_CLOSE_MINUTE - 15, second=0, microsecond=0)

    if current_time >= hard_close:
        return {"should_close": True, "reason": "Hard time close: 3:45 PM ET", "urgency": "emergency"}

    pnl = credit_received - current_mark
    if pnl >= credit_received * config.ZEDTE_PROFIT_TARGET:
        return {"should_close": True, "reason": f"Profit target: {pnl/credit_received*100:.0f}% captured", "urgency": "normal"}

    loss = current_mark - credit_received
    if loss >= credit_received * config.ZEDTE_LOSS_LIMIT:
        return {"should_close": True, "reason": f"2x loss stop (mark=${current_mark:.2f})", "urgency": "urgent"}

    if current_time >= warn_time and current_mark > credit_received:
        return {"should_close": True, "reason": "15-min warning: loss inside final window", "urgency": "urgent"}

    return result
