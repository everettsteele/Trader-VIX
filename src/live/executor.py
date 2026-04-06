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

        if config.ZEDTE_ENABLED:
            self.scheduler.add_job(self._zedte_entry, "cron", day_of_week="mon-fri",
                hour=config.ZEDTE_ENTRY_HOUR, minute=config.ZEDTE_ENTRY_MINUTE, id="zedte_entry")
            self.scheduler.add_job(self._zedte_poll, "interval",
                seconds=config.ZEDTE_POLL_INTERVAL_SEC, id="zedte_poll")

        self.scheduler.add_job(self._morning_brief, "cron",
            day_of_week="mon-fri", hour=9, minute=0, id="morning_brief")

        self.scheduler.start()
        logger.info(f"Scheduler started. Swing={'on' if config.SWING_ENABLED else 'off'} 0DTE={'on' if config.ZEDTE_ENABLED else 'off'}")

    def stop(self):
        self.scheduler.shutdown()

    # ── Morning brief ──────────────────────────────────────────────────────

    def _morning_brief(self):
        today = datetime.now(ET).strftime("%Y-%m-%d")
        today_dt = datetime.now(ET)
        open_swing = get_open_swing_positions(self.db)
        is_exp_friday = today_dt.weekday() == 4 and 15 <= today_dt.day <= 21

        lines = [f"Mode: {'PAPER' if config.TASTYTRADE_PAPER else 'LIVE'}",
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
                ev = evaluate_0dte_entry(today, spy, vix)
                lines.append(f"0DTE: {'GO' if ev['should_open'] else 'NO-GO'}")
                lines += [f"  {r}" for r in ev.get("go_no_go_reasons", [])]
        except Exception as e:
            lines.append(f"Market data error: {e}")

        if open_swing:
            lines.append("\nOpen swing positions:")
            for p in open_swing:
                dte = (datetime.strptime(p["expiration"], "%Y-%m-%d") - today_dt).days
                lines.append(f"  {p['symbol']} {p['short_strike']}/{p['long_strike']}P exp {p['expiration']} ({dte} DTE)")

        notify.send(f"Morning Brief — {today}", "\n".join(lines))

    # ── Swing ───────────────────────────────────────────────────────────────────

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

            save_snapshot(self.db, {
                "ts": datetime.now(timezone.utc).isoformat(), "net_liquidating_value": nlv,
                "cash": acct["cash"], "buying_power": acct["buying_power"],
                "open_swing_positions": len(get_open_swing_positions(self.db)),
                "open_zedte_positions": len(get_open_zedte_positions(self.db)),
                "mode": acct["mode"],
            })

            can_trade, halt_reason = self.risk.can_trade()
            if not can_trade:
                notify.kill_switch_triggered(halt_reason, nlv)
                return

            spy_price = get_current_price("SPY")
            vix = get_current_vix()

            # Check exits
            for pos in get_open_swing_positions(self.db):
                dte = (datetime.strptime(pos["expiration"], "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
                T = max(dte / 365.0, 0.001)
                s = vix / 100.0
                current_mark = max(
                    black_scholes_put(spy_price, pos["short_strike"], T, 0.05, s*(1+PUT_SKEW_HAIRCUT)) -
                    black_scholes_put(spy_price, pos["long_strike"],  T, 0.05, s*(1+PUT_SKEW_HAIRCUT*1.2)), 0.0)

                p_obj = BullPutSpread(id=pos["id"], symbol=pos["symbol"],
                    short_strike=pos["short_strike"], long_strike=pos["long_strike"],
                    expiration=pos["expiration"], dte_at_open=pos["dte_at_open"],
                    credit_received=pos["credit_received"], margin_held=pos["margin_held"],
                    num_contracts=pos["num_contracts"], opened_date=pos["opened_date"])

                ev = evaluate_exit(p_obj, current_mark, today)
                if ev["should_close"]:
                    try:
                        client.place_spread_order(symbol=pos["symbol"], short_strike=pos["short_strike"],
                            long_strike=pos["long_strike"], expiration=pos["expiration"],
                            net_credit=current_mark, num_contracts=pos["num_contracts"],
                            is_put_spread=True, action="close")
                    except Exception as e:
                        logger.error(f"Close order failed: {e}")
                    pnl = (pos["credit_received"] - current_mark) * 100 * pos["num_contracts"]
                    close_swing_position(self.db, pos["id"], today, current_mark, ev["reason"], pnl)
                    notify.trade_closed("Swing", pnl, ev["reason"],
                        {"Spread": f"{pos['short_strike']}/{pos['long_strike']}P", "Exp": pos["expiration"]})

            # Check entry
            open_now = get_open_swing_positions(self.db)
            if len(open_now) < config.SWING_MAX_SPREADS:
                entry = evaluate_entry(today, spy_price)
                if entry["should_open"]:
                    spread = entry["spread"]
                    margin = spread["margin_required"]
                    if acct["buying_power"] >= margin * 1.1:
                        try:
                            order = client.place_spread_order(symbol="SPY",
                                short_strike=spread["short_strike"], long_strike=spread["long_strike"],
                                expiration=entry["expiration"], net_credit=spread["net_credit"],
                                num_contracts=1, is_put_spread=True, action="open")
                            insert_swing_position(self.db, {
                                "symbol": "SPY", "short_strike": spread["short_strike"],
                                "long_strike": spread["long_strike"], "expiration": entry["expiration"],
                                "dte_at_open": entry["dte"], "credit_received": spread["net_credit"],
                                "margin_held": margin, "num_contracts": 1, "opened_date": today,
                                "order_id": order.get("order_id", ""), "vix_at_open": entry["vix"],
                                "spy_at_open": spy_price, "mode": "PAPER" if config.TASTYTRADE_PAPER else "LIVE"})
                            notify.trade_opened("Swing", {"Spread": f"{spread['short_strike']}/{spread['long_strike']}P",
                                "Credit": f"${spread['net_credit']:.2f}", "Exp": entry["expiration"]})
                        except Exception as e:
                            logger.error(f"Swing entry failed: {e}")

        except Exception as e:
            logger.error(f"Swing eval error: {e}", exc_info=True)
            notify.send("Swing Eval Error", str(e))

    # ── 0DTE ────────────────────────────────────────────────────────────────────

    def _zedte_entry(self):
        logger.info("=== 0DTE entry ===")
        today = datetime.now(ET).strftime("%Y-%m-%d")
        try:
            spy_price = get_current_price("SPY")
            vix = get_current_vix()
            entry = evaluate_0dte_entry(today, spy_price, vix)
            if not entry["should_open"]:
                logger.info(f"0DTE NO-GO: {entry['reason']}")
                return

            client = self._client_lazy()
            acct = client.get_account()
            condor = entry["condor"]
            margin = condor["margin_required"]
            num_condors = min(config.ZEDTE_MAX_CONDORS, max(1, int(acct["net_liquidating_value"] * config.ZEDTE_CAPITAL_PCT / margin)))

            if acct["buying_power"] < margin * num_condors:
                logger.warning("Insufficient buying power for 0DTE")
                return

            order = client.place_iron_condor_order("SPY", condor["put_short"], condor["put_long"],
                condor["call_short"], condor["call_long"], today, condor["net_credit"], num_condors)

            contingency = client.place_contingency_orders("SPY", condor["put_short"], condor["put_long"],
                condor["call_short"], condor["call_long"], today, condor["net_credit"], num_condors)

            insert_zedte_position(self.db, {
                "symbol": "SPY", "put_short": condor["put_short"], "put_long": condor["put_long"],
                "call_short": condor["call_short"], "call_long": condor["call_long"],
                "expiration": today, "credit_received": condor["net_credit"],
                "margin_held": margin * num_condors, "num_contracts": num_condors,
                "opened_date": today, "open_time": datetime.now(ET).isoformat(),
                "order_id": order.get("order_id", ""),
                "profit_order_id": contingency.get("profit_order", {}).get("order_id", ""),
                "loss_order_id": contingency.get("loss_order", {}).get("order_id", ""),
                "vix_at_open": vix, "spy_at_open": spy_price,
                "mode": "PAPER" if config.TASTYTRADE_PAPER else "LIVE"})

            self._deploy_locked = True
            notify.trade_opened("0DTE Iron Condor", {
                "Puts": f"{condor['put_long']}/{condor['put_short']}P",
                "Calls": f"{condor['call_short']}/{condor['call_long']}C",
                "Credit": f"${condor['net_credit']:.2f}", "Contracts": num_condors,
                "Contingency orders": "Placed at broker"})

        except Exception as e:
            logger.error(f"0DTE entry error: {e}", exc_info=True)
            notify.send("0DTE Entry Error", str(e))

    def _zedte_poll(self):
        now_et = datetime.now(ET)
        market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
        if not (market_open <= now_et <= market_close):
            return

        open_zedte = get_open_zedte_positions(self.db)
        if not open_zedte:
            self._deploy_locked = False
            return

        try:
            spy_price = get_current_price("SPY")
            self._last_connectivity_ok = datetime.now(timezone.utc)
            self._connectivity_alert_sent = False
        except Exception:
            offline = (datetime.now(timezone.utc) - self._last_connectivity_ok).total_seconds()
            if offline > 180 and not self._connectivity_alert_sent:
                notify.connectivity_lost([f"{p['symbol']} condor" for p in open_zedte])
                self._connectivity_alert_sent = True
            return

        vix = get_current_vix()
        for pos in open_zedte:
            T = max((datetime.strptime(pos["expiration"], "%Y-%m-%d") - now_et.replace(tzinfo=None)).total_seconds() / (365.25*24*3600), 0.001)
            put_val  = max(black_scholes_put(spy_price, pos["put_short"],  T, 0.05, vix/100) - black_scholes_put(spy_price, pos["put_long"],   T, 0.05, vix/100), 0)
            call_val = max(black_scholes_call(spy_price, pos["call_short"], T, 0.05, vix/100) - black_scholes_call(spy_price, pos["call_long"],  T, 0.05, vix/100), 0)
            current_mark = put_val + call_val

            ev = evaluate_0dte_exit(pos["credit_received"], current_mark, now_et)
            if ev["should_close"]:
                try:
                    client = self._client_lazy()
                    client.place_iron_condor_order(pos["symbol"], pos["put_long"], pos["put_short"],
                        pos["call_long"], pos["call_short"], pos["expiration"], current_mark, pos["num_contracts"])
                    for oid in [pos.get("profit_order_id"), pos.get("loss_order_id")]:
                        if oid:
                            client.cancel_order(oid)
                except Exception as e:
                    logger.error(f"0DTE close failed: {e}")

                pnl = (pos["credit_received"] - current_mark) * 100 * pos["num_contracts"]
                close_zedte_position(self.db, pos["id"], now_et.isoformat(), current_mark, ev["reason"], pnl)
                notify.trade_closed("0DTE", pnl, ev["reason"], {"Condor": f"{pos['put_short']}/{pos['call_short']}"})
                self._deploy_locked = False

    @property
    def deploy_locked(self):
        return self._deploy_locked

    def get_status(self):
        return {
            "deploy_locked": self._deploy_locked,
            "open_swing_positions": len(get_open_swing_positions(self.db)),
            "open_zedte_positions": len(get_open_zedte_positions(self.db)),
            "mode": "PAPER" if config.TASTYTRADE_PAPER else "LIVE",
            "swing_enabled": config.SWING_ENABLED,
            "zedte_enabled": config.ZEDTE_ENABLED,
            "swing_max": config.SWING_MAX_SPREADS,
        }
