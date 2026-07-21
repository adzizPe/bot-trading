from datetime import datetime
from typing import Any

from app.risk.limiters import (
    ConsecutiveLossLimiter,
    DailyRiskLimiter,
    DrawdownLimiter,
)
from app.risk.types import RiskConfig
from app.risk.validators import SpreadRiskValidator, TradingSessionValidator


class RiskManager:
    def __init__(self) -> None:
        self.daily_limiter = DailyRiskLimiter()
        self.drawdown_limiter = DrawdownLimiter()
        self.loss_limiter = ConsecutiveLossLimiter()
        self.spread_validator = SpreadRiskValidator()
        self.session_validator = TradingSessionValidator()

    def validate_locks(
        self,
        account: dict[str, Any],
        state: dict[str, Any],
        config: RiskConfig,
        spread: Any,
        now: datetime,
    ) -> list[str]:
        reasons = self.daily_limiter.validate(state, config)
        reasons.extend(self.drawdown_limiter.validate(account, state, config))
        reasons.extend(self.loss_limiter.validate(state, config, now))
        reasons.extend(self.spread_validator.validate(spread, config))
        reasons.extend(self.session_validator.validate(now, config))
        if int(state.get("open_positions", 0)) >= config.max_open_positions:
            reasons.append("Maximum open positions reached")
        return list(dict.fromkeys(reasons))
