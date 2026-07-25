from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.risk_feasibility.types import (
    RawFeasibilityInput,
    ReasonCode,
    ValidFeasibilityInput,
    ValidationOutcome,
    reasons_for,
)


_POSITIVE = (
    "risk_percent", "entry_price", "stop_loss_price", "trade_tick_size",
    "trade_tick_value", "point", "volume_min", "volume_max", "volume_step",
)
_NUMERIC = ("balance", "equity", "risk_base_value", *_POSITIVE)


class FeasibilityInputValidator:
    def validate(self, raw: RawFeasibilityInput) -> ValidationOutcome:
        reasons: list[ReasonCode] = []
        converted: dict[str, Decimal] = {}
        for name in _NUMERIC:
            try:
                converted[name] = decimal_from(getattr(raw, name))
            except (InvalidOperation, TypeError, ValueError):
                reasons.append(ReasonCode.INPUT_INVALID)
        if not reasons:
            if any(converted[name] <= 0 for name in _POSITIVE):
                reasons.append(ReasonCode.INPUT_INVALID)
            if converted["volume_max"] < converted["volume_min"]:
                reasons.append(ReasonCode.BROKER_VOLUME_GRID_INVALID)
        if raw.direction not in {"BUY", "SELL"}:
            reasons.append(ReasonCode.INPUT_INVALID)
        if raw.symbol != raw.resolved_symbol:
            reasons.append(ReasonCode.SYMBOL_MISMATCH)
        reasons.extend(self._timestamp_reasons(raw))
        if not reasons and not self._valid_geometry(raw.direction, converted):
            reasons.append(ReasonCode.INPUT_INVALID)
        if reasons:
            return ValidationOutcome(None, reasons_for(*reasons))
        values = {
            field.name: getattr(raw, field.name)
            for field in fields(ValidFeasibilityInput)
            if field.name not in converted
        }
        values.update(converted)
        return ValidationOutcome(ValidFeasibilityInput(**values), ())

    @staticmethod
    def _valid_geometry(direction: str, values: dict[str, Decimal]) -> bool:
        entry = values["entry_price"]
        stop = values["stop_loss_price"]
        return (direction == "BUY" and stop < entry) or (
            direction == "SELL" and stop > entry
        )

    @staticmethod
    def _timestamp_reasons(raw: RawFeasibilityInput) -> list[ReasonCode]:
        timestamps = raw.timestamps
        required = (
            timestamps.captured_at,
            timestamps.account_at,
            timestamps.symbol_at,
            timestamps.tick_at,
            timestamps.fresh_until,
        )
        if any(value is None for value in required):
            return [ReasonCode.SNAPSHOT_UNAVAILABLE]
        current = _utc(raw.analysis_timestamp)
        tick_at = _utc(timestamps.tick_at)  # type: ignore[arg-type]
        fresh_until = _utc(timestamps.fresh_until)  # type: ignore[arg-type]
        if tick_at > current or current > fresh_until:
            return [ReasonCode.SNAPSHOT_STALE]
        return []


def decimal_from(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise TypeError("Boolean is not numeric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Number must be finite")
    return result


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
