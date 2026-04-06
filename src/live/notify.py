"""
Trader-VIX — Notifications
Resend email alerts for trades, halts, and errors.
"""
import logging
import config

logger = logging.getLogger(__name__)


def send(subject: str, body: str):
    if not config.RESEND_API_KEY:
        logger.debug(f"[suppressed — no RESEND_API_KEY] {subject}")
        return
    try:
        import resend
        resend.api_key = config.RESEND_API_KEY
        resend.Emails.send({"from": config.FROM_EMAIL, "to": config.NOTIFY_EMAIL,
                            "subject": f"[Trader-VIX] {subject}", "text": body})
    except Exception as e:
        logger.warning(f"Notification failed ({subject}): {e}")


def trade_opened(strategy, details):
    send(f"Position Opened — {strategy}", "\n".join(f"{k}: {v}" for k, v in details.items()))


def trade_closed(strategy, pnl, reason, details):
    sign = "+" if pnl >= 0 else ""
    lines = [f"P&L: {sign}${pnl:.2f}", f"Reason: {reason}"] + [f"{k}: {v}" for k, v in details.items()]
    send(f"Position Closed — {strategy} ({sign}${pnl:.2f})", "\n".join(lines))


def kill_switch_triggered(reason, portfolio_value):
    send("KILL SWITCH TRIGGERED",
         f"KILL SWITCH TRIGGERED\n\nReason: {reason}\nPortfolio: ${portfolio_value:,.2f}\n\nAll new positions halted.")


def connectivity_lost(open_positions):
    body = f"CONNECTIVITY LOST — {len(open_positions)} OPEN POSITION(S)\n\nMANUAL ACTION REQUIRED:\n"
    body += "\n".join(f"  - {p}" for p in open_positions)
    body += "\n\nContingency orders should be live at the broker."
    send("CONNECTIVITY LOST — MANUAL ACTION REQUIRED", body)


def expiration_friday_warning(open_positions):
    body = f"EXPIRATION FRIDAY — {len(open_positions)} open position(s)\n\nNo deployments 3:00–4:45 PM ET today.\n"
    body += "\n".join(f"  - {p}" for p in open_positions)
    send("Expiration Friday — Deploy Lock Active", body)
