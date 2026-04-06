"""
Trader-VIX — Risk Manager
Portfolio-level kill switches shared by all strategies.
"""
import logging
import config

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.peak_value = initial_capital
        self.session_start_value = initial_capital
        self.trading_halted = False
        self.halt_reason = ""

    def update(self, portfolio_value: float):
        self.peak_value = max(self.peak_value, portfolio_value)
        drawdown = (portfolio_value - self.peak_value) / self.peak_value
        if drawdown <= -config.MAX_PORTFOLIO_DRAWDOWN:
            self.trading_halted = True
            self.halt_reason = (f"Portfolio drawdown {drawdown:.1%} exceeds "
                                f"{-config.MAX_PORTFOLIO_DRAWDOWN:.1%} limit. All new positions halted.")
            logger.critical(f"KILL SWITCH: {self.halt_reason}")
            return
        daily = (portfolio_value - self.session_start_value) / self.session_start_value
        if daily <= -config.DAILY_LOSS_LIMIT:
            self.trading_halted = True
            self.halt_reason = f"Daily loss {daily:.1%} exceeds {-config.DAILY_LOSS_LIMIT:.1%} limit."
            logger.warning(f"DAILY HALT: {self.halt_reason}")

    def reset_daily(self, current_value: float):
        self.session_start_value = current_value
        if self.trading_halted and "Daily loss" in self.halt_reason:
            self.trading_halted = False
            self.halt_reason = ""

    def can_trade(self) -> tuple[bool, str]:
        return not self.trading_halted, self.halt_reason
