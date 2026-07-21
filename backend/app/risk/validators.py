from datetime import datetime, timezone
from typing import Any

from app.risk.exceptions import RiskCalculationError
from app.risk.types import RiskConfig, to_decimal


class RiskRewardValidator:
    def validate(
        self,
        direction: str,
        entry: Any,
        stop_loss: Any,
        take_profit: Any,
        config: RiskConfig,
    ) -> list[str]:
        side = str(direction).upper()
        if side not in {"BUY", "SELL"}:
            return ["Direction must be BUY or SELL"]
        entry_value = to_decimal(entry, "entry")
        stop_value = to_decimal(stop_loss, "stop_loss")
        target_value = to_decimal(take_profit, "take_profit")
        reasons: list[str] = []
        stop_valid = stop_value < entry_value if side == "BUY" else stop_value > entry_value
        target_valid = target_value > entry_value if side == "BUY" else target_value < entry_value
        if not stop_valid:
            reasons.append(f"{side} stop loss is on the wrong side of entry")
        if not target_valid:
            reasons.append(f"{side} take profit is on the wrong side of entry")
        if reasons:
            return reasons
        ratio = abs(target_value - entry_value) / abs(entry_value - stop_value)
        if ratio < config.minimum_risk_reward:
            reasons.append("Risk-reward ratio is below configured minimum")
        return reasons


class SpreadRiskValidator:
    def validate(self, spread_points: Any, config: RiskConfig) -> list[str]:
        try:
            spread = to_decimal(spread_points, "spread_points")
        except ValueError as exc:
            raise RiskCalculationError(str(exc)) from exc
        if spread < 0:
            raise RiskCalculationError("spread_points cannot be negative")
        if spread > config.maximum_spread_points:
            return ["Spread exceeds configured maximum"]
        return []


class TradingSessionValidator:
    def validate(self, now: datetime, config: RiskConfig) -> list[str]:
        if not config.session_enabled:
            return []
        if not isinstance(now, datetime):
            raise RiskCalculationError("now must be a datetime")
        if now.tzinfo is None or now.utcoffset() is None:
            raise RiskCalculationError("now must be timezone-aware")
        utc_now = now.astimezone(timezone.utc)
        if utc_now.weekday() not in config.session_weekdays:
            return ["Trading is disabled for the current weekday"]
        hour = utc_now.hour
        start = config.session_start_hour_utc
        end = config.session_end_hour_utc
        in_session = start <= hour < end if start < end else hour >= start or hour < end
        if not in_session:
            return ["Current time is outside the trading session"]
        return []
