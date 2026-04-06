"""
Trader-VIX — Regime Backtest Runner

Runs a structured battery of backtests across defined market regimes.
Outputs results as JSON and prints a summary table to stdout.

Usage:
  python run_backtests.py                          # run all regimes, both strategies
  python run_backtests.py --strategy swing         # swing only
  python run_backtests.py --strategy 0dte          # 0DTE only
  python run_backtests.py --capital 20000          # override capital
  python run_backtests.py --output results.json    # custom output file
  python run_backtests.py --periods 2022,2025      # specific period keys only

Output:
  results/backtest_<timestamp>.json   full results with equity curves + trade logs
  stdout                              summary table

IMPORTANT: This script uses the SAME backtest engine as the dashboard.
Do NOT modify parameters based on results from specific periods.
The purpose of this script is observation, not optimization.
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Regime definitions ──────────────────────────────────────────────────────────────
REGIMES = {
    "2018_crash": {
        "label": "2018 Q4 Crash + Recovery",
        "start": "2018-10-01",
        "end":   "2019-06-30",
        "regime": "sharp correction, VIX spike to 36",
        "expected": "strategy should work well",
    },
    "2019_bull": {
        "label": "2019 Low-Vol Bull Run",
        "start": "2019-01-01",
        "end":   "2019-12-31",
        "regime": "low VIX, steady uptrend",
        "expected": "may underperform SPY, should be slightly positive",
    },
    "2020_covid": {
        "label": "2020 COVID Crash + Recovery",
        "start": "2020-01-01",
        "end":   "2020-12-31",
        "regime": "extreme VIX spike (85), fast recovery",
        "expected": "tests 2x loss stops under extreme conditions",
    },
    "2021_bull": {
        "label": "2021 Low-Vol Bull Run",
        "start": "2021-01-01",
        "end":   "2021-12-31",
        "regime": "low VIX, meme stocks, steady gains",
        "expected": "similar to 2019 — positive but lags SPY",
    },
    "2022_rates": {
        "label": "2022 Rate Hike Selloff",
        "start": "2022-01-01",
        "end":   "2022-12-31",
        "regime": "sustained high VIX, SPY -18%",
        "expected": "best environment — should significantly outperform",
    },
    "2023_recovery": {
        "label": "2023 Recovery + AI Bull",
        "start": "2023-01-01",
        "end":   "2023-12-31",
        "regime": "VIX declining, strong bull run",
        "expected": "lags SPY, should be positive",
    },
    "2024_bull": {
        "label": "2024 AI Bull Run",
        "start": "2024-01-01",
        "end":   "2024-12-31",
        "regime": "low VIX, strong gains",
        "expected": "underperforms SPY, positive returns",
    },
    "2025_bull": {
        "label": "2025 Low-Vol Bull Run",
        "start": "2025-01-01",
        "end":   "2025-12-31",
        "regime": "low VIX most of year",
        "expected": "underperforms SPY",
    },
    "2026_ytd": {
        "label": "2026 YTD Correction",
        "start": "2026-01-01",
        "end":   "2026-04-06",
        "regime": "elevated VIX, SPY correction",
        "expected": "strategy conditions are favorable",
    },
    "full_cycle": {
        "label": "Full Cycle 2018–2026",
        "start": "2018-01-01",
        "end":   "2026-04-06",
        "regime": "complete market cycle including bull, bear, recovery",
        "expected": "definitive long-run view",
    },
}


def run_backtest(strategy: str, start: str, end: str, capital: float) -> dict:
    from src.backtest.options_engine import SwingBacktester, ZeroDTEBacktester
    if strategy == "swing":
        result = SwingBacktester(start, end, capital).run()
    elif strategy == "0dte":
        result = ZeroDTEBacktester(start, end, capital * 0.20).run()
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
    return result.to_dict()


def print_summary_table(all_results: list):
    print()
    print("=" * 110)
    print(f"{'TRADER-VIX REGIME BACKTEST SUMMARY':^110}")
    print("=" * 110)
    print(f"  {'Period':<30} {'Strategy':<8} {'Return':>8} {'vs SPY':>8} {'Alpha':>8} "
          f"{'Sharpe':>7} {'MaxDD':>7} {'WinRate':>8} {'Trades':>7}")
    print("-" * 110)

    for r in all_results:
        m = r["metrics"]
        regime_label = r.get("regime_label", r["start_date"] + " to " + r["end_date"])
        strat = "Swing" if "swing" in r["strategy"] else "0DTE"
        ret = m["total_return_pct"]
        bench = m["benchmark_return_pct"]
        alpha = m["alpha_pct"]
        sharpe = m["sharpe_ratio"]
        dd = m["max_drawdown_pct"]
        wr = m["win_rate_pct"]
        trades = m["total_trades"]

        # Color indicators for terminal
        ret_str   = f"{ret:+.1f}%"
        bench_str = f"{bench:+.1f}%"
        alpha_str = f"{alpha:+.1f}%"

        print(f"  {regime_label:<30} {strat:<8} {ret_str:>8} {bench_str:>8} {alpha_str:>8} "
              f"{sharpe:>7.2f} {dd:>6.1f}% {wr:>7.1f}% {trades:>7}")

    print("=" * 110)
    print()
    print("  INTERPRETATION GUIDE:")
    print("  • Positive alpha in high-VIX years (2018 Q4, 2020, 2022, 2026 YTD) = strategy working correctly")
    print("  • Negative alpha in low-VIX bull years (2019, 2021, 2023–2025) = expected, not a bug")
    print("  • Full cycle return matters most for long-term capital allocation decisions")
    print("  • Do NOT adjust parameters to improve results in any specific period")
    print()


def main():
    parser = argparse.ArgumentParser(description="Trader-VIX Regime Backtest Runner")
    parser.add_argument("--strategy", choices=["swing", "0dte", "both"], default="both")
    parser.add_argument("--capital", type=float, default=20000)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--periods", type=str, default=None,
                        help="Comma-separated period keys, e.g. 2022_rates,2025_bull")
    args = parser.parse_args()

    strategies = ["swing", "0dte"] if args.strategy == "both" else [args.strategy]

    period_keys = list(REGIMES.keys())
    if args.periods:
        requested = [p.strip() for p in args.periods.split(",")]
        invalid = [p for p in requested if p not in REGIMES]
        if invalid:
            print(f"Unknown period keys: {invalid}")
            print(f"Valid keys: {list(REGIMES.keys())}")
            sys.exit(1)
        period_keys = requested

    total = len(period_keys) * len(strategies)
    print(f"\nRunning {total} backtests ({len(period_keys)} periods × {len(strategies)} strategies)")
    print("This will take several minutes — VIX and SPY data fetched and cached on first run.\n")

    all_results = []
    failed = []

    for i, period_key in enumerate(period_keys):
        regime = REGIMES[period_key]
        for strategy in strategies:
            label = f"{regime['label']} [{strategy}]"
            print(f"  [{i * len(strategies) + strategies.index(strategy) + 1}/{total}] {label}...", end=" ", flush=True)
            try:
                result = run_backtest(strategy, regime["start"], regime["end"], args.capital)
                result["period_key"]   = period_key
                result["regime_label"] = regime["label"]
                result["regime"]       = regime["regime"]
                result["expected"]     = regime["expected"]
                m = result["metrics"]
                print(f"return={m['total_return_pct']:+.1f}% alpha={m['alpha_pct']:+.1f}% "
                      f"sharpe={m['sharpe_ratio']:.2f} trades={m['total_trades']}")
                all_results.append(result)
            except Exception as e:
                print(f"FAILED: {e}")
                failed.append({"period": period_key, "strategy": strategy, "error": str(e)})

    print_summary_table(all_results)

    # Write JSON output
    os.makedirs("results", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or f"results/backtest_{timestamp}.json"

    output = {
        "run_at": datetime.now().isoformat(),
        "capital": args.capital,
        "strategies": strategies,
        "periods_run": period_keys,
        "label": SYNTHETIC_LABEL,
        "results": all_results,
        "failed": failed,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"  Full results written to: {output_path}")
    print(f"  Equity curves and trade logs included in JSON.\n")

    if failed:
        print(f"  WARNING: {len(failed)} test(s) failed:")
        for f in failed:
            print(f"    {f['period']} [{f['strategy']}]: {f['error']}")
        print()


SYNTHETIC_LABEL = (
    "SYNTHETIC APPROXIMATION — Black-Scholes, VIX as IV proxy, bid-side fills, "
    "20% put skew haircut. Conservative floor estimate. "
    "Do NOT optimize parameters based on these results."
)

if __name__ == "__main__":
    main()
