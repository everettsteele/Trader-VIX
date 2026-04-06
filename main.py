"""
Trader-VIX — Entry Point
Starts the trading executor (APScheduler) and Flask dashboard together.
Runs on Railway. Port from environment (Railway sets PORT automatically).
"""
import logging
import os
import sys

import config

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 50)
    logger.info("Trader-VIX starting")
    logger.info(f"Mode: {'PAPER' if config.TASTYTRADE_PAPER else 'LIVE'}")
    logger.info(f"Swing: {'enabled' if config.SWING_ENABLED else 'disabled'}")
    logger.info(f"0DTE:  {'enabled' if config.ZEDTE_ENABLED else 'disabled'}")
    logger.info(f"Carry: {'enabled' if config.CARRY_ENABLED else 'disabled'}")
    logger.info(f"Port:  {config.PORT}")
    logger.info("=" * 50)

    from src.live.executor import TradingExecutor
    executor = TradingExecutor()

    if config.TASTYTRADE_USERNAME and config.TASTYTRADE_PASSWORD:
        executor.start()
        logger.info("Scheduler started")
    else:
        logger.warning(
            "TASTYTRADE_USERNAME or TASTYTRADE_PASSWORD not set — "
            "scheduler not started. Dashboard only mode."
        )

    from src.dashboard.app import app
    import src.dashboard.app as dashboard_module
    dashboard_module.executor = executor

    app.run(
        host="0.0.0.0",
        port=config.PORT,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
