from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.risk_feasibility.types import (
    EquityApplicability,
    FeasibilityCalculation,
    RawFeasibilityInput,
    RiskBaseType,
)

DISCLAIMER = (
    "Advisory only. Risk Management and Trade Plan creation remain authoritative. "
    "No plan or order was created."
)


def decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError("Only finite Decimal values can be serialized")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


class RiskFeasibilityResultMapper:
    def map(
        self,
        raw: RawFeasibilityInput,
        calculation: FeasibilityCalculation,
    ) -> dict[str, Any]:
        value = decimal_string
        return {
            "source_signal_id": raw.source_signal_id,
            "symbol": raw.symbol,
            "direction": raw.direction,
            "status": calculation.status.value,
            "recommendation": calculation.recommendation.value,
            "analysis_timestamp": _iso(raw.analysis_timestamp),
            "snapshot_timestamps": {
                "captured_at": _iso(raw.timestamps.captured_at),
                "account_at": _iso(raw.timestamps.account_at),
                "symbol_at": _iso(raw.timestamps.symbol_at),
                "tick_at": _iso_optional(raw.timestamps.tick_at),
                "fresh_until": _iso_optional(raw.timestamps.fresh_until),
            },
            "account": {
                "currency": raw.account_currency,
                "balance": _safe_decimal(raw.balance),
                "equity": _safe_decimal(raw.equity),
                "risk_base_type": raw.risk_base_type.value,
                "risk_base_value": _safe_decimal(raw.risk_base_value),
                "configured_risk_percent": _safe_decimal(raw.risk_percent),
            },
            "market": {
                "entry_price": _safe_decimal(raw.entry_price),
                "stop_loss_price": _safe_decimal(raw.stop_loss_price),
                "stop_distance": value(calculation.stop_distance),
                "stop_distance_points": value(calculation.stop_distance_points),
                "trade_tick_size": _safe_decimal(raw.trade_tick_size),
                "trade_tick_value": _safe_decimal(raw.trade_tick_value),
                "point": _safe_decimal(raw.point),
            },
            "volume": {
                "raw_lot": value(calculation.raw_lot),
                "capped_lot": value(calculation.capped_lot),
                "normalized_lot": value(calculation.normalized_lot),
                "volume_min": _safe_decimal(raw.volume_min),
                "minimum_broker_lot": value(calculation.minimum_broker_lot),
                "volume_max": _safe_decimal(raw.volume_max),
                "volume_step": _safe_decimal(raw.volume_step),
            },
            "calculation": self._calculation(raw, calculation),
            "reasons": [
                {"code": reason.code.value, "message": reason.message}
                for reason in calculation.reasons
            ],
            "units": {
                "currency": raw.account_currency,
                "percent": "%", "volume": "lot",
                "price": f"{raw.symbol} price unit", "point": "point",
                "tick_derived": f"{raw.account_currency} per lot",
            },
            "advisory": True,
            "disclaimer": DISCLAIMER,
        }

    @staticmethod
    def _calculation(
        raw: RawFeasibilityInput,
        result: FeasibilityCalculation,
    ) -> dict[str, Any]:
        value = decimal_string
        applicability = result.required_minimum_equity_applicability
        if applicability is None:
            applicability = (
                EquityApplicability.APPLICABLE
                if raw.risk_base_type is RiskBaseType.EQUITY
                else EquityApplicability.HYPOTHETICAL_NOT_APPLICABLE
            )
        return {
            "risk_amount": value(result.risk_amount),
            "ticks_at_risk": value(result.ticks_at_risk),
            "risk_per_lot": value(result.risk_per_lot),
            "required_minimum_risk_base": value(result.required_minimum_risk_base),
            "required_minimum_risk_base_type": raw.risk_base_type.value,
            "required_minimum_equity": value(result.required_minimum_equity),
            "required_minimum_equity_applicability": applicability.value,
            "maximum_stop_distance": value(result.maximum_stop_distance),
            "maximum_stop_distance_points": value(result.maximum_stop_distance_points),
            "boundary_stop_loss_price": value(result.boundary_stop_loss_price),
            "minimum_lot_estimated_risk_amount": value(
                result.minimum_lot_estimated_risk_amount
            ),
            "minimum_lot_estimated_risk_percent": value(
                result.minimum_lot_estimated_risk_percent
            ),
            "minimum_lot_risk_delta_amount": value(
                result.minimum_lot_risk_delta_amount
            ),
            "minimum_lot_risk_delta_percent": value(
                result.minimum_lot_risk_delta_percent
            ),
            "minimum_lot_label": "DIAGNOSTIC_ONLY",
        }


def _safe_decimal(value: Any) -> str | None:
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return decimal_string(number) if number.is_finite() else None


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_optional(value: datetime | None) -> str | None:
    return None if value is None else _iso(value)
