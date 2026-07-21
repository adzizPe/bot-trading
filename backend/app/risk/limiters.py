from datetime import datetime, timezone
from typing import Any

from app.risk.types import RiskConfig, to_decimal


def _state_decimal(state: dict[str, Any], key: str, default: str = "0"):
    return to_decimal(state.get(key, default), key)


class DailyRiskLimiter:
    def validate(self, state: dict[str, Any], config: RiskConfig) -> list[str]:
        reasons: list[str] = []
        starting_balance = _state_decimal(state, "starting_balance")
        realized_loss = _state_decimal(state, "realized_loss")
        maximum_loss = starting_balance * config.max_daily_loss_percent / 100
        if starting_balance > 0 and realized_loss >= maximum_loss:
            reasons.append("Maximum daily loss reached")
        if int(state.get("trades_count", 0)) >= config.max_trades_per_day:
            reasons.append("Maximum trades per day reached")
        return reasons


class DrawdownLimiter:
    def validate(
        self, account: dict[str, Any], state: dict[str, Any], config: RiskConfig
    ) -> list[str]:
        peak_equity = _state_decimal(state, "peak_equity")
        current_equity = to_decimal(account.get("equity", 0), "equity")
        floating_drawdown = _state_decimal(state, "floating_drawdown")
        observed_drawdown = max(peak_equity - current_equity, floating_drawdown, 0)
        maximum_drawdown = peak_equity * config.max_daily_drawdown_percent / 100
        if peak_equity > 0 and observed_drawdown >= maximum_drawdown:
            return ["Maximum daily drawdown reached"]
        return []


class ConsecutiveLossLimiter:
    def validate(
        self,
        state: dict[str, Any],
        config: RiskConfig,
        now: datetime,
    ) -> list[str]:
        reasons: list[str] = []
        if int(state.get("consecutive_losses", 0)) >= config.max_consecutive_losses:
            reasons.append("Maximum consecutive losses reached")
        cooldown_until = state.get("cooldown_until")
        if cooldown_until is not None:
            if not isinstance(cooldown_until, datetime):
                raise ValueError("cooldown_until must be a datetime or None")
            if now.tzinfo is None or cooldown_until.tzinfo is None:
                raise ValueError("Cooldown datetimes must be timezone-aware")
            if now.astimezone(timezone.utc) < cooldown_until.astimezone(timezone.utc):
                reasons.append("Loss cooldown is active")
        return reasons
