"""
Trader-VIX — Main Executor

Runs both strategies on a shared APScheduler instance.
- Swing: daily eval at 4:05 PM ET
- 0DTE: entry at 9:45 AM ET, polling every 2 min until 4:00 PM ET
- Connectivity watchdog: alerts if Tastytrade unreachable 3+ min with open 0DTE

State: always reconciled from SQLite + broker on startup. Crash-safe.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

import config
from src.live.db import (
    get_conn, get_open_swing_positions, get_open_zedte_positions,
    insert_swing_position, close_swing_position,
    insert_zedte_position, close_zedte_position,
    save_snapshot,
)
from src.live import notify
from src.risk.manager import RiskManager
from src.data.price_fetcher import get_current_price
from src.data.vix_fetcher import get_current_vix, compute_vix_rank
from src.strategies.put_selling import evaluate_entry, evaluate_exit, BullPutSpread
from src.strategies.iron_condor_0dte import evaluate_0dte_entry, evaluate_0dte_exit
from src.data.options_pricer import black_scholes_put, black_scholes_call, PUT_SKEW_HAIRCUT

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


class TradingExecutor:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone=ET)
        self.risk = None
        self._client = None
        self._last_connectivity_ok = datetime.now(timezone.utc)
        self._connectivity_alert_sent = False
        self._deploy_locked = False
        self.db = get_conn()
        logger.info("TradingExecutor initialized")

    def _client_lazy(self):
        if self._client is None:
            from src.live.tastytrade_client import TastytradeClient
            self._client = TastytradeClient()
        return self._client

    def start(self):
        if config.SWING_ENABLED:
            self.scheduler.add_job(self._swing_eval, "cron", day_of_week="mon-fri",
                hour=config.SWING_EVAL_HOUR, minute=config.SWING_EVAL_MINUTE, id="swing_eval")
            logger.info(f"Swing scheduled: {config.SWING_EVAL_HOUR}:{config.SWING_EVAL_MINUTE:02d} ET weekdays")

        if config.ZEDTE_ENABLED:
            self.scheduler.add_job(self._zedte_entry, "cron", day_of_week="mon-fri",
                hour=config.ZEDTE_ENTRY_HOUR, minute=config.ZEDTE_ENTRY_MINUTE, id="zedte_entry")
            self.scheduler.add_job(self._zedte_poll, "interval",
                seconds=config.ZEDTE_POLL_INTERVAL_SEC, id="zedte_poll")
            logger.info("0DTE scheduled: entry 9:45 AM, poll every 2 min")

        self.scheduler.add_job(self._morning_brief, "cron",
            day_of_week="mon-fri", hour=9, minute=0, id="morning_brief")
        self.scheduler.start()
        logger.info("Scheduler started")

    def stop(self):
        self.scheduler.shutdown()

    def _morning_brief(self):
        today = datetime.now(ET).strftime("%Y-%m-%d")
        today_dt = datetime.now(ET)
        open_swing = get_open_swing_positions(self.db)
        is_exp_friday = today_dt.weekday() == 4 and 15 <= today_dt.day <= 21

        lines = [f"Date: {today}",
                 f"Mode: {'PAPER' if config.TASTYTRADE_PAPER else 'LIVE'}",
                 f"Open swing: {len(open_swing)}", ""]

        if is_exp_friday:
            lines.insert(0, "EXPIRATION FRIDAY — NO DEPLOYMENTS 3:00–4:45 PM ET\n")
            notify.expiration_friday_warning(
                [f"{p['symbol']} {p['short_strike']}/{p['long_strike']} exp {p['expiration']}" for p in open_swing])

        try:
            vix = get_current_vix()
            spy = get_current_price("SPY")
            rank = compute_vix_rank(today)
            lines += [f"VIX: {vix:.1f} (rank: {rank:.0f}th pct)", f"SPY: ${spy:.2f}", ""]
            if config.ZEDTE_ENABLED:
                e = evaluate_0dte_entry(today, spy, vix)
                lines.append(f"0DTE: {'GO' if e['should_open'] else 'NO-GO'}")
                for r in e.get("go_no_go_reasons", []):
                    lines.append(f"  {r}")
        except Exception as ex:
            lines.append(f"Market data error: {ex}")

        if open_swing:
            lines.append("\nOpen swing positions:")
            for p in open_swing:
                dte = (datetime.strptime(p["expiration"], "%Y-%m-%d") - today_dt).days
                lines.append(f"  {p['symbol']} {p['short_strike']}/{p['long_strike']}P {p['expiration']} ({dte} DTE)")

        notify.send(f"Morning Brief — {today}", "\n".join(lines))

    def _swing_eval(self):
        logger.info("=== Swing evaluation ===")
        today = datetime.now(ET).strftime("%Y-%m-%d")
        try:
            client = self._client_lazy()
            acct = client.get_account()
            nlv = acct["net_liquidating_value"]
            if self.risk is None:
                self.risk = RiskManager(nlv)
            self.risk.update(nlv)

            save_snapshot(self.db, {"ts": datetime.now(timezone.utc).isoformat(),
                "net_liquidating_value": nlv, "cash": acct["cash"],
                "buying_power": acct["buying_power"],
                "open_swing_positions": len(get_open_swing_positions(self.db)),
                "open_zedte_positions": len(get_open_zedte_positions(self.db)),
                "mode": acct["mode"]})

            can, halt = self.risk.can_trade()
            if not can:
                notify.kill_switch_triggered(halt, nlv)
                return

            spy = get_current_price("SPY")
            vix = get_current_vix()

            for pos in get_open_swing_positions(self.db):
                dte = (datetime.strptime(pos["expiration"], "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
                T = max(dte / 365.0, 0.001)
                sigma = vix / 100.0
                mark = max(
                    black_scholes_put(spy, pos["short_strike"], T, 0.05, sigma * (1 + PUT_SKEW_HAIRCUT)) -
                    black_scholes_put(spy, pos["long_strike"],  T, 0.05, sigma * (1 + PUT_SKEW_HAIRCUT * 1.2)), 0)
                obj = BullPutSpread(id=pos["id"], symbol=pos["symbol"],
                    short_strike=pos["short_strike"], long_strike=pos["long_strike"],
                    expiration=pos["expiration"], dte_at_open=pos["dte_at_open"],
                    credit_received=pos["credit_received"], margin_held=pos["margin_held"],
                    num_contracts=pos["num_contracts"], opened_date=pos["opened_date"])
                ex = evaluate_exit(obj, mark, today)
                if ex["should_close"]:
                    try:
                        client.place_spread_order(symbol=pos["symbol"],
                            short_strike=pos["short_strike"], long_strike=pos["long_strike"],
                            expiration=pos["expiration"], net_credit=mark,
                            num_contracts=pos["num_contracts"], action="close")
                    except Exception as e:
                        logger.error(f"Close order failed: {e}")
                    pnl = (pos["credit_received"] - mark) * 100 * pos["num_contracts"]
                    close_swing_position(self.db, pos["id"], today, mark, ex["reason"], pnl)
                    notify.trade_closed("Swing", pnl, ex["reason"],
                        {"spread": f"{pos['short_strike']}/{pos['long_strike']}P", "exp": pos["expiration"]})

            if len(get_open_swing_positions(self.db)) < config.SWING_MAX_SPREADS:
                entry = evaluate_entry(today, spy)
                if entry["should_open"]:
                    sp = entry["spread"]
                    if acct["buying_power"] >= sp["margin_required"] * 1.1:
                        try:
                            order = client.place_spread_order(symbol="SPY",
                                short_strike=sp["short_strike"], long_strike=sp["long_strike"],
                                expiration=entry["expiration"], net_credit=sp["net_credit"],
                                num_contracts=1, action="open")
                            insert_swing_position(self.db, {"symbol": "SPY",
                                "short_strike": sp["short_strike"], "long_strike": sp["long_strike"],
                                "expiration": entry["expiration"], "dte_at_open": entry["dte"],
                                "credit_received": sp["net_credit"], "margin_held": sp["margin_required"],
                                "num_contracts": 1, "opened_date": today,
                                "order_id": order.get("order_id", ""),
                                "vix_at_open": entry["vix"], "spy_at_open": spy,
                                "mode": "PAPER" if config.TASTYTRADE_PAPER else "LIVE"})
                            notify.trade_opened("Swing", {"Spread": f"{sp['short_strike']}/{sp['long_strike']}P",
                                "Credit": f"${sp['net_credit']:.2f}", "Exp": entry["expiration"]})
                        except Exception as e:
                            logger.error(f"Swing entry failed: {e}")
        except Exception as e:
            logger.error(f"Swing eval error: {e}", exc_info=True)
            notify.send("Swing Eval Error", str(e))

    def _zedte_entry(self):
        logger.info("=== 0DTE entry ===")
        today = datetime.now(ET).strftime("%Y-%m-%d")
        try:
            spy = get_current_price("SPY")
            vix = get_current_vix()
            entry = evaluate_0dte_entry(today, spy, vix)
            if not entry["should_open"]:
                logger.info(f"0DTE NO-GO: {entry['reason']}")
                return

            client = self._client_lazy()
            acct = client.get_account()
            condor = entry["condor"]
            margin = condor["margin_required"]
            num = min(config.ZEDTE_MAX_CONDORS, max(1, int(acct["net_liquidating_value"] * config.ZEDTE_CAPITAL_PCT / margin)))

            if acct["buying_power"] < margin * num:
                logger.warning("Insufficient buying power for 0DTE")
                return

            order = client.place_iron_condor_order("SPY",
                condor["put_short"], condor["put_long"], condor["call_short"], condor["call_long"],
                today, condor["net_credit"], num)
            contingency = client.place_contingency_orders("SPY",
                condor["put_short"], condor["put_long"], condor["call_short"], condor["call_long"],
                today, condor["net_credit"], num)

            insert_zedte_position(self.db, {"symbol": "SPY",
                "put_short": condor["put_short"], "put_long": condor["put_long"],
                "call_short": condor["call_short"], "call_long": condor["call_long"],
                "expiration": today, "credit_received": condor["net_credit"],
                "margin_held": margin * num, "num_contracts": num,
                "opened_date": today, "open_time": datetime.now(ET).isoformat(),
                "order_id": order.get("order_id", ""),
                "profit_order_id": contingency.get("profit_order", {}).get("order_id", ""),
                "loss_order_id": contingency.get("loss_order", {}).get("order_id", ""),
                "vix_at_open": vix, "spy_at_open": spy,
                "mode": "PAPER" if config.TASTYTRADE_PAPER else "LIVE"})

            self._deploy_locked = True
            notify.trade_opened("0DTE Iron Condor", {
                "Puts": f"{condor['put_long']}/{condor['put_short']}P",
                "Calls": f"{condor['call_short']}/{condor['call_long']}C",
                "Credit": f"${condor['net_credit']:.2f}", "Contracts": num,
                "Contingency": "Placed at broker"})
        except Exception as e:
            logger.error(f"0DTE entry error: {e}", exc_info=True)
            notify.send("0DTE Entry Error", str(e))

    def _zedte_poll(self):
        now = datetime.now(ET)
        if not (now.replace(hour=9, minute=30) <= now <= now.replace(hour=16, minute=0)):
            return
        open_z = get_open_zedte_positions(self.db)
        if not open_z:
            self._deploy_locked = False
            return
        try:
            spy = get_current_price("SPY")
            self._last_connectivity_ok = datetime.now(timezone.utc)
            self._connectivity_alert_sent = False
        except Exception:
            offline = (datetime.now(timezone.utc) - self._last_connectivity_ok).total_seconds()
            if offline > 180 and not self._connectivity_alert_sent:
                notify.connectivity_lost([f"{p['symbol']} {p['put_short']}/{p['call_short']}" for p in open_z])
                self._connectivity_alert_sent = True
            return

        vix = get_current_vix()
        for pos in open_z:
            T = max((datetime.strptime(pos["expiration"], "%Y-%m-%d") - now.replace(tzinfo=None)).total_seconds() / (365.25 * 86400), 0.001)
            put_v  = max(black_scholes_put(spy,  pos["put_short"],  T, 0.05, vix/100) - black_scholes_put(spy,  pos["put_long"],  T, 0.05, vix/100), 0)
            call_v = max(black_scholes_call(spy, pos["call_short"], T, 0.05, vix/100) - black_scholes_call(spy, pos["call_long"], T, 0.05, vix/100), 0)
            mark = put_v + call_v
            ex = evaluate_0dte_exit(pos["credit_received"], mark, now)
            if ex["should_close"]:
                try:
                    client = self._client_lazy()
                    client.place_iron_condor_order(pos["symbol"],
                        pos["put_long"], pos["put_short"], pos["call_long"], pos["call_short"],
                        pos["expiration"], mark, pos["num_contracts"])
                    for oid in [pos.get("profit_order_id"), pos.get("loss_order_id")]:
                        if oid:
                            client.cancel_order(oid)
                except Exception as e:
                    logger.error(f"0DTE close failed: {e}")
                pnl = (pos["credit_received"] - mark) * 100 * pos["num_contracts"]
                close_zedte_position(self.db, pos["id"], now.isoformat(), mark, ex["reason"], pnl)
                notify.trade_closed("0DTE", pnl, ex["reason"],
                    {"Condor": f"{pos['put_short']}/{pos['call_short']}"})
                self._deploy_locked = False

    @property
    def deploy_locked(self):
        return self._deploy_locked

    def get_status(self):
        return {
            "deploy_locked": self._deploy_locked,
            "open_swing_positions": len(get_open_swing_positions(self.db)),
            "open_zedte_positions": len(get_open_zedte_positions(self.db)),
            "swing_max": config.SWING_MAX_SPREADS,
            "mode": "PAPER" if config.TASTYTRADE_PAPER else "LIVE",
            "swing_enabled": config.SWING_ENABLED,
            "zedte_enabled": config.ZEDTE_ENABLED,
        }
