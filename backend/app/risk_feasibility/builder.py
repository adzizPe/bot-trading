from dataclasses import dataclass
from typing import Any

from app.risk.calculators import StopLossCalculator
from app.risk.types import RiskConfig, to_decimal
from app.risk_feasibility.types import (
    AtomicRiskSnapshot,
    RawFeasibilityInput,
    RiskBaseType,
)


class CandidateRiskContextBuilder:
    def __init__(self, stop_calculator: StopLossCalculator | None = None) -> None:
        self._stop = stop_calculator or StopLossCalculator()

    def build(
        self,
        signal: dict[str, Any],
        settings: dict[str, Any],
        snapshot: AtomicRiskSnapshot,
    ) -> RawFeasibilityInput:
        payload = snapshot.payload
        account = payload["account"]
        symbol = payload["symbol"]
        tick = payload["tick"]
        direction = str(signal.get("direction", ""))
        if signal.get("status") != "CANDIDATE":
            raise ValueError("Signal is not a candidate")
        entry = tick["ask" if direction == "BUY" else "bid"]
        spec = _specification(symbol)
        config = _config(settings)
        stop = self._stop.calculate(
            direction, entry, signal["atr"], config, spec
        )
        base_type = (
            RiskBaseType.EQUITY
            if settings["use_equity_for_risk"]
            else RiskBaseType.BALANCE
        )
        base = account["equity"] if base_type is RiskBaseType.EQUITY else account["balance"]
        return RawFeasibilityInput(
            source_signal_id=str(signal["signal_id"]),
            symbol=str(signal["symbol"]),
            resolved_symbol=str(symbol["name"]),
            direction=direction,
            analysis_timestamp=snapshot.timestamps.captured_at,
            timestamps=snapshot.timestamps,
            account_currency=str(account["currency"]),
            balance=account["balance"], equity=account["equity"],
            risk_base_type=base_type, risk_base_value=base,
            risk_percent=settings["risk_per_trade_percent"],
            entry_price=entry, stop_loss_price=stop["stop_loss"],
            trade_tick_size=symbol["trade_tick_size"],
            trade_tick_value=symbol["trade_tick_value"], point=symbol["point"],
            volume_min=symbol["volume_min"], volume_max=symbol["volume_max"],
            volume_step=symbol["volume_step"],
        )


@dataclass(frozen=True)
class _StopSpecification:
    point: Any
    trade_tick_size: Any
    trade_stops_level: Any
    trade_freeze_level: Any


def _specification(symbol: dict[str, Any]) -> _StopSpecification:
    return _StopSpecification(
        point=to_decimal(symbol["point"], "point"),
        trade_tick_size=to_decimal(symbol["trade_tick_size"], "trade_tick_size"),
        trade_stops_level=to_decimal(
            symbol.get("trade_stops_level") or 0, "trade_stops_level"
        ),
        trade_freeze_level=to_decimal(
            symbol.get("trade_freeze_level") or 0, "trade_freeze_level"
        ),
    )


def _config(values: dict[str, Any]) -> RiskConfig:
    prepared = {
        key: value for key, value in values.items()
        if key not in {"settings_id", "updated_at"}
    }
    prepared["session_weekdays"] = tuple(prepared["session_weekdays"])
    return RiskConfig(**prepared)
