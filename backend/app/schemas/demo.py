from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DemoSettingsUpdate(StrictRequest):
    magic: int | None = Field(default=None, gt=0, le=2_147_483_647)
    comment: str | None = Field(default=None, min_length=1, max_length=31)
    deviation_points: int | None = Field(default=None, ge=0, le=1000)
    maximum_spread_points: float | None = Field(default=None, gt=0)
    intent_ttl_seconds: int | None = Field(default=None, ge=1, le=3600)
    emergency_close_positions: StrictBool | None = None
    trailing_stop_enabled: StrictBool | None = None
    trailing_distance_points: float | None = Field(default=None, ge=0)


class DemoSettingsResponse(BaseModel):
    settings_id: str
    magic: int
    comment: str
    deviation_points: int
    maximum_spread_points: float
    intent_ttl_seconds: int
    execution_mode: Literal["MANUAL_DEMO"]
    emergency_close_positions: bool
    trailing_stop_enabled: bool
    trailing_distance_points: float
    updated_at: datetime


class DemoExecuteRequest(StrictRequest):
    trade_plan_id: str = Field(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9-]+$")
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    confirmation_text: Literal["EXECUTE DEMO ORDER"]


class DemoMoveStopRequest(StrictRequest):
    stop_loss: float = Field(gt=0)


class DemoTrailingRequest(StrictRequest):
    pass


class DemoEmergencyStopRequest(StrictRequest):
    close_positions: StrictBool = False


class DemoOrderResponse(BaseModel):
    execution_request_id: str
    idempotency_key: str
    trade_plan_id: str
    signal_id: str
    symbol: str
    direction: Literal["BUY", "SELL"]
    requested_volume: float | None
    requested_price: float | None
    requested_sl: float
    requested_tp: float
    actual_order_ticket: int | None
    actual_deal_ticket: int | None
    actual_position_ticket: int | None
    retcode: int | None
    retcode_message: str | None
    broker_comment: str | None
    sanitized_request: dict[str, Any]
    sanitized_response: dict[str, Any] | None
    status: Literal["RESERVED", "ACCEPTED", "REJECTED", "UNKNOWN"]
    reconciliation_required: bool
    created_at: datetime
    executed_at: datetime | None
    outcome: str | None = None


class DemoBrokerOrderResponse(BaseModel):
    order_id: str
    execution_request_id: str
    trade_plan_id: str
    broker_order_ticket: int | None
    broker_deal_ticket: int | None
    symbol: str
    direction: Literal["BUY", "SELL"]
    volume: float
    requested_price: float
    fill_price: float | None
    stop_loss: float
    take_profit: float
    status: str
    outcome: str
    broker_retcode: int | None
    reconciliation_required: bool
    created_at: datetime
    updated_at: datetime


class DemoDealResponse(BaseModel):
    trade_id: str
    broker_deal_ticket: int
    broker_position_ticket: int | None
    position_id: str | None
    symbol: str
    direction: Literal["BUY", "SELL"]
    volume: float
    price: float
    profit: float
    commission: float
    swap: float
    magic: int
    executed_at: datetime
    created_at: datetime


class DemoPositionResponse(BaseModel):
    position_id: str
    broker_position_ticket: int
    order_id: str | None
    trade_plan_id: str | None
    symbol: str
    direction: Literal["BUY", "SELL"]
    volume: float
    entry_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    magic: int
    status: str
    missing_since: datetime | None
    opened_at: datetime
    closed_at: datetime | None
    updated_at: datetime


class DemoOperationResponse(BaseModel):
    position_id: str | None = None
    outcome: str
    retcode: int | None = None
    retcode_name: str | None = None
    retcode_message: str | None = None
    broker_comment: str | None = None
    reconciliation_required: bool = False
    order: int | None = None
    deal: int | None = None
    volume: float | None = None
    price: float | None = None
    requested_price: float | None = None
    symbol: str | None = None
    comment: str | None = None
    attempts: int | None = None
    stop_loss: float | None = None


class DemoReconciliationResponse(BaseModel):
    run_id: str
    status: str
    adopted_count: int
    updated_count: int
    missing_count: int
    trades_count: int
    started_at: datetime
    completed_at: datetime | None


class DemoStatusResponse(BaseModel):
    enabled: bool
    engine: dict[str, Any]
    broker: dict[str, Any]


class DemoEmergencyStopResponse(BaseModel):
    engine: dict[str, Any]
    close_positions_requested: bool
    close_positions_effective: bool
    results: list[dict[str, Any]]
