"""
Trader-VIX — Forex Carry Trade Strategy

Buy high-yield currency, sell low-yield currency.
Earn the interest rate differential daily (rolled overnight).

Entry: monthly rebalance + opportunistic entry when carry rank shifts
Exit: 10% position drawdown stop | carry differential collapses below 1% | monthly rebalance out

Risk: carry unwinds are the primary risk. JPY carry unwind August 2024
was -15 to -30% in days for unhedged carry positions. We mitigate this
with strict position stops and limited allocation (20% of portfolio).

Capital allocation: 20% of total portfolio by default.
Max 2 pairs active simultaneously.
Max leverage: 3:1 on forex positions (conservative vs. OANDA's 50:1 max).
"""
import logging
from datetime import datetime, timedelta

import config
from src.data.forex_fetcher import rank_carry_pairs, compute_volatility_adjusted_carry

logger = logging.getLogger(__name__)

MAX_PAIRS           = int(getattr(config, "CARRY_MAX_POSITIONS", 2))
STOP_LOSS_PCT       = float(getattr(config, "CARRY_STOP_PCT", 0.10))     # 10% on position
MIN_CARRY_TO_HOLD   = float(getattr(config, "CARRY_MIN_HOLD", 1.0))      # exit if carry drops below 1%
LEVERAGE            = float(getattr(config, "CARRY_LEVERAGE", 3.0))       # 3:1
REBALANCE_DAY       = int(getattr(config, "CARRY_REBALANCE_DAY", 1))      # 1st of month


class CarryPosition:
    def __init__(
        self,
        pair: str,
        base: str,
        quote: str,
        carry_pct: float,
        units: int,
        entry_price: float,
        entry_date: str,
        allocated_capital: float,
        order_id: str = "",
    ):
        self.pair = pair
        self.base = base
        self.quote = quote
        self.carry_pct = carry_pct
        self.units = units
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.allocated_capital = allocated_capital
        self.order_id = order_id

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """Price-only P&L as percentage of allocated capital."""
        price_change = (current_price - self.entry_price) / self.entry_price
        return price_change * LEVERAGE

    def daily_carry_income(self) -> float:
        """
        Estimated daily carry income in dollars.
        carry_pct is annual — divide by 365.
        Applied to notional (allocated_capital * leverage).
        """
        notional = self.allocated_capital * LEVERAGE
        return notional * (self.carry_pct / 100.0) / 365.0


def select_pairs(api_key: str = None, paper: bool = True) -> list[dict]:
    """
    Select the top MAX_PAIRS carry pairs for the current period.
    If OANDA credentials available, uses volatility-adjusted carry for ranking.
    Otherwise falls back to raw carry differential ranking.
    """
    ranked = rank_carry_pairs()
    if not ranked:
        logger.warning("No pairs meet minimum carry threshold")
        return []

    if api_key:
        # Enrich with volatility-adjusted carry if we have live data
        for pair_data in ranked:
            pair_data["vol_adj_carry"] = compute_volatility_adjusted_carry(
                pair_data["pair"], api_key, paper
            )
        ranked.sort(key=lambda x: x.get("vol_adj_carry", 0), reverse=True)

    return ranked[:MAX_PAIRS]


def evaluate_carry_entry(pair_data: dict, available_capital: float) -> dict:
    """
    Evaluate whether to enter a carry position on a given pair.
    Returns dict with should_enter, reason, position_size_usd.
    """
    result = {"should_enter": False, "reason": "", "position_size_usd": 0}

    carry = pair_data.get("carry_pct", 0)

    if carry < config.__dict__.get("CARRY_MIN_HOLD", 1.0):
        result["reason"] = f"Carry {carry:.2f}% below minimum threshold"
        return result

    # Allocate equally across MAX_PAIRS
    per_pair_capital = available_capital / MAX_PAIRS

    result["should_enter"] = True
    result["position_size_usd"] = round(per_pair_capital, 2)
    result["notional_usd"] = round(per_pair_capital * LEVERAGE, 2)
    result["estimated_annual_income"] = round(per_pair_capital * LEVERAGE * carry / 100, 2)
    result["estimated_daily_income"] = round(per_pair_capital * LEVERAGE * carry / 100 / 365, 4)
    return result


def evaluate_carry_exit(position: CarryPosition, current_price: float, current_date: str) -> dict:
    """
    Evaluate exit conditions for an open carry position.
    Returns dict with should_exit, reason, urgency.
    """
    result = {"should_exit": False, "reason": "", "urgency": "normal"}

    # 1. Stop loss: 10% drawdown on position (price-only)
    price_pnl_pct = position.unrealized_pnl_pct(current_price)
    if price_pnl_pct <= -STOP_LOSS_PCT:
        result["should_exit"] = True
        result["reason"] = f"Stop loss: position down {price_pnl_pct:.1%} (limit: {-STOP_LOSS_PCT:.0%})"
        result["urgency"] = "urgent"
        return result

    # 2. Carry collapsed: if the rate differential no longer justifies the position
    from src.data.forex_fetcher import compute_carry_differential
    current_carry = compute_carry_differential(position.base, position.quote)
    if current_carry < MIN_CARRY_TO_HOLD:
        result["should_exit"] = True
        result["reason"] = f"Carry differential collapsed to {current_carry:.2f}% — below hold threshold"
        return result

    # 3. Monthly rebalance day: exit and re-evaluate
    try:
        current_dt = datetime.strptime(current_date, "%Y-%m-%d")
        if current_dt.day == REBALANCE_DAY:
            result["should_exit"] = True
            result["reason"] = "Monthly rebalance — re-evaluating pair selection"
            return result
    except ValueError:
        pass

    return result


def estimate_annual_carry_return(
    allocated_capital: float,
    top_carry_pct: float = None,
    leverage: float = LEVERAGE,
) -> dict:
    """
    Estimate annual P&L from carry on an allocated capital amount.
    Used for projection and reporting.

    Returns bad/base/good scenarios based on:
    - Carry income (certain if no unwind)
    - Price movement (uncertain, uses historical distribution)
    """
    if top_carry_pct is None:
        ranked = rank_carry_pairs()
        top_carry_pct = sum(p["carry_pct"] for p in ranked[:MAX_PAIRS]) / max(len(ranked[:MAX_PAIRS]), 1)

    notional = allocated_capital * leverage
    gross_carry_income = notional * top_carry_pct / 100

    return {
        "allocated_capital": allocated_capital,
        "leverage": leverage,
        "notional": notional,
        "avg_carry_pct": round(top_carry_pct, 2),
        "gross_carry_income_annual": round(gross_carry_income, 2),
        # Scenarios: price movement ± carry income
        "bad_case": round(-allocated_capital * 0.18, 2),    # carry unwind, -18% on allocated
        "base_case": round(gross_carry_income * 0.80, 2),   # earn 80% of gross carry (costs/slippage)
        "good_case": round(gross_carry_income * 1.30, 2),   # earn carry + favorable price drift
        "bad_pct":  -18.0,
        "base_pct": round(gross_carry_income * 0.80 / allocated_capital * 100, 1),
        "good_pct": round(gross_carry_income * 1.30 / allocated_capital * 100, 1),
    }
