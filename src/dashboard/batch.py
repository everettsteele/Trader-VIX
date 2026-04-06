"""
Trader-VIX — Batch Backtest API endpoint
Add to src/dashboard/app.py routes.
Called by the dashboard's regime testing UI.
"""
# This file is imported by app.py — not run directly.
# The route is registered in app.py via:
#   from src.dashboard.batch import batch_backtest_route
#   app.add_url_rule('/api/backtest/batch', 'batch_backtest', batch_backtest_route, methods=['POST'])

import json
import logging
from flask import request, jsonify, session

logger = logging.getLogger(__name__)

REGIMES = {
    "2018_crash":    {"label": "2018 Q4 Crash",        "start": "2018-10-01", "end": "2019-06-30"},
    "2019_bull":     {"label": "2019 Bull Run",         "start": "2019-01-01", "end": "2019-12-31"},
    "2020_covid":    {"label": "2020 COVID",            "start": "2020-01-01", "end": "2020-12-31"},
    "2021_bull":     {"label": "2021 Bull Run",         "start": "2021-01-01", "end": "2021-12-31"},
    "2022_rates":    {"label": "2022 Rate Hikes",       "start": "2022-01-01", "end": "2022-12-31"},
    "2023_recovery": {"label": "2023 Recovery",        "start": "2023-01-01", "end": "2023-12-31"},
    "2024_bull":     {"label": "2024 Bull Run",         "start": "2024-01-01", "end": "2024-12-31"},
    "2025_bull":     {"label": "2025 Bull Run",         "start": "2025-01-01", "end": "2025-12-31"},
    "2026_ytd":      {"label": "2026 YTD Correction",   "start": "2026-01-01", "end": "2026-04-06"},
    "full_cycle":    {"label": "Full Cycle 2018–2026",  "start": "2018-01-01", "end": "2026-04-06"},
}


def batch_backtest_route():
    if not session.get("authenticated"):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json()
    strategy  = data.get("strategy", "swing")
    capital   = float(data.get("capital", 20000))
    periods   = data.get("periods", list(REGIMES.keys()))

    results = []
    errors  = []

    for period_key in periods:
        if period_key not in REGIMES:
            errors.append({"period": period_key, "error": "unknown period key"})
            continue

        regime = REGIMES[period_key]
        try:
            if strategy == "swing":
                from src.backtest.options_engine import SwingBacktester
                result = SwingBacktester(regime["start"], regime["end"], capital).run()
            elif strategy == "0dte":
                from src.backtest.options_engine import ZeroDTEBacktester
                result = ZeroDTEBacktester(regime["start"], regime["end"], capital * 0.20).run()
            else:
                errors.append({"period": period_key, "error": f"unknown strategy: {strategy}"})
                continue

            d = result.to_dict()
            d["period_key"]   = period_key
            d["regime_label"] = regime["label"]
            results.append(d)

        except Exception as e:
            logger.error(f"Batch backtest error {period_key}: {e}", exc_info=True)
            errors.append({"period": period_key, "label": regime["label"], "error": str(e)})

    return jsonify({
        "strategy": strategy,
        "capital": capital,
        "results": results,
        "errors": errors,
        "regimes_available": REGIMES,
    })
