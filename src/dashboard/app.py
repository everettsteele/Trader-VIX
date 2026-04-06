"""
Trader-VIX — Flask Dashboard
Port 3005. Password-protected. Meridian dark theme.
Routes: /, /health, /backtest, /api/*
"""
import json
import logging
from datetime import datetime

from flask import Flask, render_template, request, redirect, session, jsonify, url_for

import config
from src.live.db import get_conn, get_open_swing_positions, get_open_zedte_positions

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
executor = None  # attached by main.py at startup


def _require_auth():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == config.DASHBOARD_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "Incorrect password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    redir = _require_auth()
    if redir:
        return redir
    db = get_conn()
    open_swing = get_open_swing_positions(db)
    open_zedte = get_open_zedte_positions(db)
    rows = db.execute("SELECT ts, net_liquidating_value FROM portfolio_snapshots ORDER BY ts DESC LIMIT 90").fetchall()
    snapshots = list(reversed([{"ts": r["ts"][:10], "nlv": r["net_liquidating_value"]} for r in rows]))
    closed_swing = list(db.execute("SELECT * FROM swing_positions WHERE status='closed' ORDER BY close_date DESC LIMIT 20").fetchall())
    closed_zedte = list(db.execute("SELECT * FROM zedte_positions WHERE status='closed' ORDER BY close_time DESC LIMIT 20").fetchall())
    return render_template("index.html",
        open_swing=open_swing, open_zedte=open_zedte,
        snapshots=json.dumps(snapshots),
        closed_swing=closed_swing, closed_zedte=closed_zedte,
        status=executor.get_status() if executor else {},
        mode="PAPER" if config.TASTYTRADE_PAPER else "LIVE",
        now=datetime.now().strftime("%Y-%m-%d %H:%M ET"))


@app.route("/backtest")
def backtest_page():
    redir = _require_auth()
    if redir:
        return redir
    return render_template("backtest.html")


@app.route("/api/backtest/run", methods=["POST"])
def run_backtest():
    if not session.get("authenticated"):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    strategy = data.get("strategy", "swing")
    start_date = data.get("start_date", "2020-01-01")
    end_date = data.get("end_date", "2024-12-31")
    capital = float(data.get("capital", config.TOTAL_CAPITAL))
    try:
        if strategy == "swing":
            from src.backtest.options_engine import SwingBacktester
            result = SwingBacktester(start_date, end_date, capital).run()
        elif strategy == "0dte":
            from src.backtest.options_engine import ZeroDTEBacktester
            result = ZeroDTEBacktester(start_date, end_date, capital * config.ZEDTE_CAPITAL_PCT).run()
        else:
            return jsonify({"error": f"Unknown strategy: {strategy}"}), 400
        return jsonify(result.to_dict())
    except Exception as e:
        logger.error(f"Backtest error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/positions")
def api_positions():
    if not session.get("authenticated"):
        return jsonify({"error": "unauthorized"}), 401
    db = get_conn()
    return jsonify({"swing": get_open_swing_positions(db), "zedte": get_open_zedte_positions(db)})


@app.route("/api/status")
def api_status():
    return jsonify(executor.get_status() if executor else {})


@app.route("/health")
def health():
    """
    Public endpoint. Gladys pre-deploy hook checks this.
    Returns 503 if deploy_locked (0DTE positions open during market hours).
    """
    db = get_conn()
    deploy_locked = executor.deploy_locked if executor else False
    payload = {
        "status": "ok",
        "deploy_locked": deploy_locked,
        "open_swing_positions": len(get_open_swing_positions(db)),
        "open_zedte_positions": len(get_open_zedte_positions(db)),
        "mode": "PAPER" if config.TASTYTRADE_PAPER else "LIVE",
        "timestamp": datetime.utcnow().isoformat(),
    }
    return jsonify(payload), 503 if deploy_locked else 200
