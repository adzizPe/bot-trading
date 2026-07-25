from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class FeasibilityStatus(str, Enum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNAVAILABLE = "UNAVAILABLE"


class Recommendation(str, Enum):
    PROCEED = "PROCEED_TO_AUTHORITATIVE_TRADE_PLAN_FLOW"
    DO_NOT_FORCE = "DO_NOT_FORCE_MINIMUM_LOT"
    RETRY = "RETRY_WITH_VALID_FRESH_DATA"


class RiskBaseType(str, Enum):
    EQUITY = "EQUITY"
    BALANCE = "BALANCE"


class EquityApplicability(str, Enum):
    APPLICABLE = "APPLICABLE"
    HYPOTHETICAL_NOT_APPLICABLE = "HYPOTHETICAL_NOT_APPLICABLE"


class ReasonCode(str, Enum):
    RISK_BASE_NOT_POSITIVE = "RISK_BASE_NOT_POSITIVE"
    NORMALIZED_LOT_BELOW_BROKER_MINIMUM = "NORMALIZED_LOT_BELOW_BROKER_MINIMUM"
    STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM = "STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM"
    INPUT_INVALID = "INPUT_INVALID"
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    BROKER_VOLUME_GRID_INVALID = "BROKER_VOLUME_GRID_INVALID"


_REASON_MESSAGES = {
    ReasonCode.RISK_BASE_NOT_POSITIVE: "The selected risk base is not positive.",
    ReasonCode.NORMALIZED_LOT_BELOW_BROKER_MINIMUM: (
        "Floor-normalized volume is below the executable broker minimum."
    ),
    ReasonCode.STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM: (
        "The current stop distance is wider than the advisory maximum for the configured risk."
    ),
    ReasonCode.INPUT_INVALID: "Required feasibility input is invalid or incomplete.",
    ReasonCode.SNAPSHOT_UNAVAILABLE: "A required account or market snapshot is unavailable.",
    ReasonCode.SNAPSHOT_STALE: "The market snapshot is stale or has an invalid timestamp.",
    ReasonCode.SYMBOL_MISMATCH: "The signal and broker snapshot symbols do not match.",
    ReasonCode.BROKER_VOLUME_GRID_INVALID: "The broker volume grid is not executable.",
}
_REASON_PRIORITY = tuple(ReasonCode)


@dataclass(frozen=True)
class Reason:
    code: ReasonCode
    message: str


def reasons_for(*codes: ReasonCode) -> tuple[Reason, ...]:
    selected = set(codes)
    return tuple(
        Reason(code, _REASON_MESSAGES[code])
        for code in _REASON_PRIORITY
        if code in selected
    )[:8]


@dataclass(frozen=True)
class SnapshotTimestamps:
    captured_at: datetime
    account_at: datetime
    symbol_at: datetime
    tick_at: datetime | None
    fresh_until: datetime | None


@dataclass(frozen=True)
class AtomicRiskSnapshot:
    payload: dict[str, Any]
    timestamps: SnapshotTimestamps


@dataclass(frozen=True, kw_only=True)
class RawFeasibilityInput:
    source_signal_id: str
    symbol: str
    resolved_symbol: str
    direction: str
    analysis_timestamp: datetime
    timestamps: SnapshotTimestamps
    account_currency: str
    balance: Any
    equity: Any
    risk_base_type: RiskBaseType
    risk_base_value: Any
    risk_percent: Any
    entry_price: Any
    stop_loss_price: Any
    trade_tick_size: Any
    trade_tick_value: Any
    point: Any
    volume_min: Any
    volume_max: Any
    volume_step: Any


@dataclass(frozen=True, kw_only=True)
class ValidFeasibilityInput:
    source_signal_id: str
    symbol: str
    resolved_symbol: str
    direction: str
    analysis_timestamp: datetime
    timestamps: SnapshotTimestamps
    account_currency: str
    balance: Decimal
    equity: Decimal
    risk_base_type: RiskBaseType
    risk_base_value: Decimal
    risk_percent: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    trade_tick_size: Decimal
    trade_tick_value: Decimal
    point: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal


@dataclass(frozen=True, kw_only=True)
class FeasibilityCalculation:
    status: FeasibilityStatus
    recommendation: Recommendation
    reasons: tuple[Reason, ...]
    risk_amount: Decimal | None = None
    stop_distance: Decimal | None = None
    stop_distance_points: Decimal | None = None
    ticks_at_risk: Decimal | None = None
    risk_per_lot: Decimal | None = None
    raw_lot: Decimal | None = None
    capped_lot: Decimal | None = None
    normalized_lot: Decimal | None = None
    minimum_broker_lot: Decimal | None = None
    required_minimum_risk_base: Decimal | None = None
    required_minimum_equity: Decimal | None = None
    required_minimum_equity_applicability: EquityApplicability | None = None
    maximum_stop_distance: Decimal | None = None
    maximum_stop_distance_points: Decimal | None = None
    boundary_stop_loss_price: Decimal | None = None
    minimum_lot_estimated_risk_amount: Decimal | None = None
    minimum_lot_estimated_risk_percent: Decimal | None = None
    minimum_lot_risk_delta_amount: Decimal | None = None
    minimum_lot_risk_delta_percent: Decimal | None = None

    @classmethod
    def unavailable(cls, reasons: tuple[Reason, ...]) -> "FeasibilityCalculation":
        return cls(
            status=FeasibilityStatus.UNAVAILABLE,
            recommendation=Recommendation.RETRY,
            reasons=reasons,
        )


@dataclass(frozen=True)
class ValidationOutcome:
    value: ValidFeasibilityInput | None
    reasons: tuple[Reason, ...]


def unavailable_result(
    *,
    signal_id: str,
    symbol: str,
    direction: str,
    now: datetime,
    code: ReasonCode,
) -> dict[str, Any]:
    timestamp = now.isoformat().replace("+00:00", "Z")
    reason = reasons_for(code)[0]
    base_type = RiskBaseType.EQUITY.value
    return {
        "source_signal_id": signal_id, "symbol": symbol, "direction": direction,
        "status": FeasibilityStatus.UNAVAILABLE.value,
        "recommendation": Recommendation.RETRY.value,
        "analysis_timestamp": timestamp,
        "snapshot_timestamps": {
            "captured_at": timestamp, "account_at": timestamp,
            "symbol_at": timestamp, "tick_at": None, "fresh_until": None,
        },
        "account": {
            "currency": "", "balance": None, "equity": None,
            "risk_base_type": base_type, "risk_base_value": None,
            "configured_risk_percent": None,
        },
        "market": {
            "entry_price": None, "stop_loss_price": None, "stop_distance": None,
            "stop_distance_points": None, "trade_tick_size": None,
            "trade_tick_value": None, "point": None,
        },
        "volume": {
            "raw_lot": None, "capped_lot": None, "normalized_lot": None,
            "volume_min": None, "minimum_broker_lot": None,
            "volume_max": None, "volume_step": None,
        },
        "calculation": _empty_calculation(base_type),
        "reasons": [{"code": reason.code.value, "message": reason.message}],
        "units": {
            "currency": "", "percent": "%", "volume": "lot",
            "price": f"{symbol} price unit", "point": "point",
            "tick_derived": "account currency per lot",
        },
        "advisory": True,
        "disclaimer": (
            "Advisory only. Risk Management and Trade Plan creation remain authoritative. "
            "No plan or order was created."
        ),
    }


def _empty_calculation(base_type: str) -> dict[str, Any]:
    values = {
        "risk_amount": None, "ticks_at_risk": None, "risk_per_lot": None,
        "required_minimum_risk_base": None,
        "required_minimum_risk_base_type": base_type,
        "required_minimum_equity": None,
        "required_minimum_equity_applicability": EquityApplicability.APPLICABLE.value,
        "maximum_stop_distance": None, "maximum_stop_distance_points": None,
        "boundary_stop_loss_price": None,
        "minimum_lot_estimated_risk_amount": None,
        "minimum_lot_estimated_risk_percent": None,
        "minimum_lot_risk_delta_amount": None,
        "minimum_lot_risk_delta_percent": None,
        "minimum_lot_label": "DIAGNOSTIC_ONLY",
    }
    return values
