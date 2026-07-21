from typing import Literal

from pydantic import BaseModel


class MT5StatusResponse(BaseModel):
    state: str
    connected: bool
    demo_verified: bool
    configured: bool
    symbol: str
    last_error: str | None


class MT5AccountResponse(BaseModel):
    login: int
    is_demo: Literal[True]
    trade_mode: Literal["demo"]
    server: str | None
    company: str | None
    currency: str | None
    leverage: int | None
    balance: float | None
    equity: float | None
    margin: float | None
    margin_free: float | None
    margin_level: float | None


class MT5TerminalResponse(BaseModel):
    connected: bool
    name: str | None
    company: str | None
    build: int | None
    trade_allowed: bool | None
    tradeapi_disabled: bool | None
    dlls_allowed: bool | None
    ping_last: int | None


class MT5SymbolResponse(BaseModel):
    name: str
    description: str | None
    digits: int | None
    point: float | None
    trade_tick_size: float | None
    trade_tick_value: float | None
    volume_min: float | None
    volume_max: float | None
    volume_step: float | None
    spread: int | None
    trade_stops_level: int | None
    trade_freeze_level: int | None
    trade_contract_size: float | None
    visible: bool | None
    select: bool | None
