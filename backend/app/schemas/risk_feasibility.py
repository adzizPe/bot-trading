from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeasibilityReasonResponse(StrictModel):
    code: str
    message: str


class SnapshotTimestampsResponse(StrictModel):
    captured_at: datetime
    account_at: datetime
    symbol_at: datetime
    tick_at: datetime | None
    fresh_until: datetime | None


class FeasibilityAccountResponse(StrictModel):
    currency: str
    balance: str | None
    equity: str | None
    risk_base_type: Literal["EQUITY", "BALANCE"]
    risk_base_value: str | None
    configured_risk_percent: str | None


class FeasibilityMarketResponse(StrictModel):
    entry_price: str | None
    stop_loss_price: str | None
    stop_distance: str | None
    stop_distance_points: str | None
    trade_tick_size: str | None
    trade_tick_value: str | None
    point: str | None


class FeasibilityVolumeResponse(StrictModel):
    raw_lot: str | None
    capped_lot: str | None
    normalized_lot: str | None
    volume_min: str | None
    minimum_broker_lot: str | None
    volume_max: str | None
    volume_step: str | None


class FeasibilityCalculationResponse(StrictModel):
    risk_amount: str | None
    ticks_at_risk: str | None
    risk_per_lot: str | None
    required_minimum_risk_base: str | None
    required_minimum_risk_base_type: Literal["EQUITY", "BALANCE"]
    required_minimum_equity: str | None
    required_minimum_equity_applicability: Literal[
        "APPLICABLE", "HYPOTHETICAL_NOT_APPLICABLE"
    ]
    maximum_stop_distance: str | None
    maximum_stop_distance_points: str | None
    boundary_stop_loss_price: str | None
    minimum_lot_estimated_risk_amount: str | None
    minimum_lot_estimated_risk_percent: str | None
    minimum_lot_risk_delta_amount: str | None
    minimum_lot_risk_delta_percent: str | None
    minimum_lot_label: Literal["DIAGNOSTIC_ONLY"]


class FeasibilityUnitsResponse(StrictModel):
    currency: str
    percent: Literal["%"]
    volume: Literal["lot"]
    price: str
    point: Literal["point"]
    tick_derived: str


class RiskFeasibilityResponse(StrictModel):
    source_signal_id: str
    symbol: str
    direction: str
    status: Literal["FEASIBLE", "INFEASIBLE", "UNAVAILABLE"]
    recommendation: Literal[
        "PROCEED_TO_AUTHORITATIVE_TRADE_PLAN_FLOW",
        "DO_NOT_FORCE_MINIMUM_LOT",
        "RETRY_WITH_VALID_FRESH_DATA",
    ]
    analysis_timestamp: datetime
    snapshot_timestamps: SnapshotTimestampsResponse
    account: FeasibilityAccountResponse
    market: FeasibilityMarketResponse
    volume: FeasibilityVolumeResponse
    calculation: FeasibilityCalculationResponse
    reasons: list[FeasibilityReasonResponse]
    units: FeasibilityUnitsResponse
    advisory: Literal[True]
    disclaimer: str
