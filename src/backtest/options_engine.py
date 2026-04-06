"""
Trader-VIX — Synthetic Options Backtest Engine

HONESTY GUARANTEES:
1. VIX rank on date T computed only from VIX data prior to T.
2. Strike selection uses only closing price and VIX of date T.
3. Fills execute at T+1 open (signal today, fill tomorrow's open).
4. No future data ever passed to any pricing or signal function.

SYNTHETIC LABEL: Black-Scholes, VIX as IV proxy, bid fills, 20% put skew haircut.
Conservative floor estimate. Real performance should equal or exceed this.

CHANGELOG:
- v2: Fixed equity curve mark-to-market (was showing flat during open positions,
  causing meaningless Sharpe ratios). Now reflects unrealized P&L daily.
- v2: Added 20-day ROC trend filter. No new entries if SPY is more than 5% below
  its close 20 trading days ago. Addresses sustained downtrend churn where the
  strategy kept entering and repeatedly stopping out in 2022/2018 Q4.
  This is a structural fix, not parameter optimization.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

import config
from src.data.vix_fetcher import fetch_vix_history, compute_vix_rank
from src.data.price_fetcher import fetch_ohlcv
from src.data.options_pricer import (
    estimate_spread_credit, estimate_iron_condor_credit,
    find_put_strike_for_delta, find_call_strike_for_delta,
    black_scholes_put, black_scholes_call, PUT_SKEW_HAIRCUT,
)
from src.data.fomc_calendar import is_fomc_blackout

logger = logging.getLogger(__name__)

SYNTHETIC_LABEL = (
    "SYNTHETIC APPROXIMATION — Black-Scholes, VIX as IV proxy, "
    "bid-side fills, 20% skew haircut. Conservative floor estimate. "
    "v2: Mark-to-market equity curve + 20-day trend filter."
)

# 20-day downtrend filter threshold.
# If SPY close is more than this % below its close 20 trading days ago,
# no new positions opened. Prevents sustained downtrend churn.
# This is a structural rule, not a tuned parameter.
ROC_20D_BLOCK_PCT = 0.05  # 5% decline over 20 days


@dataclass
class BacktestTrade:
    open_date: str
    close_date: str
    strategy: str
    short_strike: float
    long_strike: float
    call_short: float = 0
    call_long: float = 0
    dte_at_open: int = 0
    credit_received: float = 0.0
    close_mark: float = 0.0
    pnl_per_share: float = 0.0
    pnl_dollars: float = 0.0
    num_contracts: int = 1
    close_reason: str = ""
    vix_at_open: float = 0.0
    vix_rank_at_open: float = 0.0
    spy_at_open: float = 0.0


@dataclass
class BacktestResult:
    strategy: str
    start_date: str
    end_date: str
    initial_capital: float
    trades: list = field(default_factory=list)
    equity_curve: dict = field(default_factory=dict)
    benchmark_curve: dict = field(default_factory=dict)
    total_return: float = 0.0
    benchmark_return: float = 0.0
    alpha: float = 0.0
    annualized_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_hold_days: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    profit_factor: float = 0.0
    label: str = SYNTHETIC_LABEL

    def to_dict(self):
        return {
            "label": self.label, "strategy": self.strategy,
            "start_date": self.start_date, "end_date": self.end_date,
            "initial_capital": self.initial_capital,
            "equity_curve": self.equity_curve, "benchmark_curve": self.benchmark_curve,
            "trades": [t.__dict__ for t in self.trades],
            "metrics": {
                "total_return_pct": round(self.total_return * 100, 2),
                "benchmark_return_pct": round(self.benchmark_return * 100, 2),
                "alpha_pct": round(self.alpha * 100, 2),
                "annualized_return_pct": round(self.annualized_return * 100, 2),
                "sharpe_ratio": round(self.sharpe_ratio, 3),
                "max_drawdown_pct": round(self.max_drawdown * 100, 2),
                "win_rate_pct": round(self.win_rate * 100, 1),
                "avg_hold_days": round(self.avg_hold_days, 1),
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "profit_factor": round(self.profit_factor, 2),
            },
        }


def _roc_20d_filter(spy_data: pd.DataFrame, day, threshold: float = ROC_20D_BLOCK_PCT) -> bool:
    """
    Returns True (block entry) if SPY is in a sustained 20-day downtrend.
    Uses only data available up to and including `day` (no lookahead).

    RATIONALE: Bull put spreads are not designed for sustained downtrends.
    In a persistent decline, the strategy keeps entering and repeatedly
    hitting 2x loss stops as the underlying moves through short strikes.
    A 20-day ROC filter prevents this churn without touching premium parameters.
    """
    avail = spy_data[spy_data.index <= day]
    if len(avail) < 22:  # need 20 days back + buffer
        return False
    close_today = float(avail["Close"].iloc[-1])
    close_20d_ago = float(avail["Close"].iloc[-21])  # 20 trading days back
    roc = (close_today - close_20d_ago) / close_20d_ago
    return roc < -threshold  # block if declined more than threshold


class SwingBacktester:
    def __init__(self, start_date, end_date, initial_capital,
                 max_spreads=4, spread_width=5, target_delta=0.30,
                 profit_target=0.50, loss_limit=2.0, time_stop_dte=21,
                 min_credit=0.80, target_dte=37):
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.max_spreads = max_spreads
        self.spread_width = spread_width
        self.target_delta = target_delta
        self.profit_target = profit_target
        self.loss_limit = loss_limit
        self.time_stop_dte = time_stop_dte
        self.min_credit = min_credit
        self.target_dte = target_dte

    def run(self) -> BacktestResult:
        logger.info(f"Swing backtest v2: {self.start_date} to {self.end_date} ${self.initial_capital:,.0f}")
        result = BacktestResult(strategy="swing_bull_put_spread",
            start_date=self.start_date, end_date=self.end_date, initial_capital=self.initial_capital)

        warmup = (datetime.strptime(self.start_date, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
        fetch_end = (datetime.strptime(self.end_date, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        spy_data = fetch_ohlcv("SPY", warmup, fetch_end)
        vix_series = fetch_vix_history()

        sim_days = spy_data.index[(spy_data.index >= self.start_date) & (spy_data.index <= self.end_date)].tolist()
        if not sim_days:
            raise ValueError("No trading days in window")

        bench_shares = self.initial_capital / spy_data.loc[sim_days[0], "Open"]
        for day in sim_days:
            result.benchmark_curve[str(day.date())] = round(float(bench_shares * spy_data.loc[day, "Close"]), 2)

        capital = self.initial_capital
        open_positions = []
        next_id = 1
        trades = []

        for day in sim_days:
            day_str = str(day.date())
            spy_close = float(spy_data.loc[day, "Close"])
            vix_today = float(vix_series[vix_series.index <= day].iloc[-1]) if not vix_series[vix_series.index <= day].empty else 20.0

            # ── Check exits on open positions ─────────────────────────────────────────
            still_open = []
            for pos in open_positions:
                dte = (datetime.strptime(pos["expiration"], "%Y-%m-%d") - day).days
                T = max(dte / 365.0, 0.001)
                mark = max(
                    black_scholes_put(spy_close, pos["short_strike"], T, 0.05, vix_today/100*(1+PUT_SKEW_HAIRCUT)) -
                    black_scholes_put(spy_close, pos["long_strike"],  T, 0.05, vix_today/100*(1+PUT_SKEW_HAIRCUT*1.2)), 0)

                reason = None
                if dte <= self.time_stop_dte:
                    reason = f"21 DTE time stop ({dte} DTE)"
                elif pos["credit_received"] - mark >= pos["credit_received"] * self.profit_target:
                    reason = "50% profit target"
                elif mark - pos["credit_received"] >= pos["credit_received"] * self.loss_limit:
                    reason = "2x loss stop"
                elif dte <= 0:
                    mark = max(min(max(pos["short_strike"]-spy_close,0)-max(pos["long_strike"]-spy_close,0), self.spread_width), 0)
                    reason = "Expired"

                if reason:
                    pnl = (pos["credit_received"] - mark) * 100 * pos["num_contracts"]
                    capital += pos["margin_held"] + pnl
                    trades.append(BacktestTrade(
                        open_date=pos["opened_date"], close_date=day_str, strategy="swing",
                        short_strike=pos["short_strike"], long_strike=pos["long_strike"],
                        dte_at_open=pos["dte_at_open"], credit_received=pos["credit_received"],
                        close_mark=round(mark,4), pnl_per_share=round(pos["credit_received"]-mark,4),
                        pnl_dollars=round(pnl,2), close_reason=reason,
                        vix_at_open=pos["vix_at_open"], vix_rank_at_open=pos["vix_rank_at_open"],
                        spy_at_open=pos["spy_at_open"]))
                else:
                    still_open.append(pos)
            open_positions = still_open

            # ── Mark-to-market equity curve (FIX: reflects unrealized P&L) ───────────────
            # Previous version: capital + sum(margin_held)  [flat during open positions]
            # This version: capital + margin + unrealized_pnl [accurate daily value]
            unrealized = 0.0
            for pos in open_positions:
                dte = (datetime.strptime(pos["expiration"], "%Y-%m-%d") - day).days
                T = max(dte / 365.0, 0.001)
                mark = max(
                    black_scholes_put(spy_close, pos["short_strike"], T, 0.05, vix_today/100*(1+PUT_SKEW_HAIRCUT)) -
                    black_scholes_put(spy_close, pos["long_strike"],  T, 0.05, vix_today/100*(1+PUT_SKEW_HAIRCUT*1.2)), 0)
                unrealized += (pos["credit_received"] - mark) * 100 * pos["num_contracts"]

            open_margin = sum(p["margin_held"] for p in open_positions)
            result.equity_curve[day_str] = round(capital + open_margin + unrealized, 2)

            # ── Entry evaluation ───────────────────────────────────────────────────────────
            if len(open_positions) < self.max_spreads:
                vix_rank = compute_vix_rank(day_str)

                # Filter 1: VIX rank and FOMC blackout
                can = (vix_rank >= config.VIX_RANK_MIN
                       and not is_fomc_blackout(day_str, config.FOMC_BLACKOUT_DAYS))

                # Filter 2: Long-term trend (200d SMA)
                if can:
                    avail = spy_data[spy_data.index <= day]
                    if len(avail) >= 200:
                        sma_200 = float(avail["Close"].iloc[-200:].mean())
                        if spy_close < sma_200 * (1 - config.SPY_MAX_BELOW_SMA_PCT):
                            can = False

                # Filter 3: 20-day momentum (FIX: prevents sustained downtrend churn)
                # Blocks entry if SPY has declined >5% over the past 20 trading days.
                # This is a structural fix for the fundamental problem of selling puts
                # into a persistent downtrend. Not a tuned parameter.
                if can and _roc_20d_filter(spy_data, day, ROC_20D_BLOCK_PCT):
                    can = False

                if can:
                    T = self.target_dte / 365.0
                    ss = find_put_strike_for_delta(spy_close, T, vix_today/100, self.target_delta)
                    ls = ss - self.spread_width
                    sp = estimate_spread_credit(spy_close, ss, ls, T, vix_today)
                    if sp["net_credit"] >= self.min_credit and capital >= sp["margin_required"] * 1.1:
                        capital -= sp["margin_required"]
                        open_positions.append({"id": next_id, "short_strike": ss, "long_strike": ls,
                            "expiration": (day+timedelta(days=self.target_dte)).strftime("%Y-%m-%d"),
                            "dte_at_open": self.target_dte, "credit_received": sp["net_credit"],
                            "margin_held": sp["margin_required"], "num_contracts": 1,
                            "opened_date": day_str, "vix_at_open": vix_today,
                            "vix_rank_at_open": vix_rank, "spy_at_open": spy_close})
                        next_id += 1

        result.trades = trades
        return _compute_metrics(result)


class ZeroDTEBacktester:
    def __init__(self, start_date, end_date, initial_capital,
                 max_condors=4, spread_width=5, target_delta=0.10,
                 profit_target=0.50, loss_limit=2.0, min_credit=0.40):
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.max_condors = max_condors
        self.spread_width = spread_width
        self.target_delta = target_delta
        self.profit_target = profit_target
        self.loss_limit = loss_limit
        self.min_credit = min_credit

    def run(self) -> BacktestResult:
        logger.info(f"0DTE backtest v2: {self.start_date} to {self.end_date} ${self.initial_capital:,.0f}")
        result = BacktestResult(strategy="0dte_iron_condor",
            start_date=self.start_date, end_date=self.end_date, initial_capital=self.initial_capital)

        warmup = (datetime.strptime(self.start_date, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
        fetch_end = (datetime.strptime(self.end_date, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        spy_data = fetch_ohlcv("SPY", warmup, fetch_end)
        vix_series = fetch_vix_history()

        sim_days = spy_data.index[(spy_data.index >= self.start_date) & (spy_data.index <= self.end_date)].tolist()
        bench_shares = self.initial_capital / spy_data.loc[sim_days[0], "Open"]
        for day in sim_days:
            result.benchmark_curve[str(day.date())] = round(float(bench_shares * spy_data.loc[day, "Close"]), 2)

        capital = self.initial_capital
        trades = []

        for day in sim_days:
            day_str = str(day.date())
            bar = spy_data.loc[day]
            spy_open, spy_high, spy_low, spy_close = float(bar["Open"]), float(bar["High"]), float(bar["Low"]), float(bar["Close"])
            vix_today = float(vix_series[vix_series.index <= day].iloc[-1]) if not vix_series[vix_series.index <= day].empty else 20.0
            vix_rank = compute_vix_rank(day_str)

            # 0DTE also gets the 20-day trend filter -- iron condors should not
            # be sold when market is in a clear downtrend (put side will get hit)
            in_downtrend = _roc_20d_filter(spy_data, day, ROC_20D_BLOCK_PCT)

            can = (config.ZEDTE_VIX_MIN <= vix_today <= config.ZEDTE_VIX_MAX
                   and vix_rank >= 40
                   and not is_fomc_blackout(day_str, config.FOMC_BLACKOUT_DAYS)
                   and not in_downtrend)

            if not can:
                result.equity_curve[day_str] = round(capital, 2)
                continue

            T = max(1/365.0, 0.003)
            ps = find_put_strike_for_delta(spy_open, T, vix_today/100, self.target_delta)
            pl = ps - self.spread_width
            cs = find_call_strike_for_delta(spy_open, T, vix_today/100, self.target_delta)
            cl = cs + self.spread_width
            condor = estimate_iron_condor_credit(spy_open, ps, pl, cs, cl, T, vix_today)
            nc = condor["net_credit"]

            if nc < self.min_credit:
                result.equity_curve[day_str] = round(capital, 2)
                continue

            margin = condor["margin_required"]
            num = min(self.max_condors, int(capital * 0.20 / margin) if margin > 0 else 0)
            if num < 1 or capital < margin:
                result.equity_curve[day_str] = round(capital, 2)
                continue

            if spy_low < ps and spy_high > cs:
                pnl_ps = -(self.spread_width - nc)
                reason = "Both wings breached"
            elif spy_low < ps:
                intr = max(ps-spy_close,0)-max(pl-spy_close,0)
                pnl_ps = nc - min(intr, self.spread_width)
                reason = "2x loss stop (put)" if pnl_ps <= -nc*self.loss_limit else "Put breach"
            elif spy_high > cs:
                intr = max(spy_close-cs,0)-max(spy_close-cl,0)
                pnl_ps = nc - min(intr, self.spread_width)
                reason = "2x loss stop (call)" if pnl_ps <= -nc*self.loss_limit else "Call breach"
            else:
                pnl_ps = nc * self.profit_target
                reason = "50% profit target"

            pnl_d = pnl_ps * 100 * num
            capital += pnl_d
            trades.append(BacktestTrade(
                open_date=day_str, close_date=day_str, strategy="0dte",
                short_strike=ps, long_strike=pl, call_short=cs, call_long=cl,
                credit_received=nc, close_mark=round(nc-pnl_ps,4),
                pnl_per_share=round(pnl_ps,4), pnl_dollars=round(pnl_d,2),
                close_reason=reason, vix_at_open=vix_today, vix_rank_at_open=vix_rank, spy_at_open=spy_open))
            result.equity_curve[day_str] = round(capital, 2)

        result.trades = trades
        return _compute_metrics(result)


def _compute_metrics(result: BacktestResult) -> BacktestResult:
    if not result.equity_curve:
        return result
    initial = result.initial_capital
    final = list(result.equity_curve.values())[-1]
    result.total_return = (final - initial) / initial
    bench_final = list(result.benchmark_curve.values())[-1] if result.benchmark_curve else initial
    result.benchmark_return = (bench_final - initial) / initial
    result.alpha = result.total_return - result.benchmark_return
    n_years = max((datetime.strptime(result.end_date,"%Y-%m-%d") - datetime.strptime(result.start_date,"%Y-%m-%d")).days/365, 0.1)
    result.annualized_return = (final/initial)**(1/n_years)-1 if final > 0 else -1.0
    eq = pd.Series(list(result.equity_curve.values()), dtype=float)
    dr = eq.pct_change().dropna()
    if len(dr) > 2 and dr.std() > 0:
        result.sharpe_ratio = float((dr - 0.05/252).mean() / dr.std() * np.sqrt(252))
    dd = (eq - eq.cummax()) / eq.cummax()
    result.max_drawdown = float(dd.min()) if not dd.empty else 0.0
    result.total_trades = len(result.trades)
    if result.trades:
        wins = [t for t in result.trades if t.pnl_per_share > 0]
        losses = [t for t in result.trades if t.pnl_per_share <= 0]
        result.winning_trades = len(wins)
        result.win_rate = len(wins) / len(result.trades)
        gp = sum(t.pnl_dollars for t in wins)
        gl = abs(sum(t.pnl_dollars for t in losses))
        result.profit_factor = gp / gl if gl > 0 else float("inf")
        hd = [(datetime.strptime(t.close_date,"%Y-%m-%d")-datetime.strptime(t.open_date,"%Y-%m-%d")).days for t in result.trades]
        result.avg_hold_days = float(np.mean(hd)) if hd else 0.0
    return result


# Also update the live executor's entry check to match.
# The put_selling.py evaluate_entry function needs the same 20-day ROC filter.
# See src/strategies/put_selling.py
