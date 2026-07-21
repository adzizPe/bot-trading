from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class MarketTickResponse(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread_points: float
    spread_price: float
    timestamp: datetime
    connection_status: Literal["connected"]


class MarketSpreadResponse(BaseModel):
    symbol: str
    bid: float
    ask: float
    spread_points: float
    spread_price: float
    timestamp: datetime


class CandleResponse(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int
    is_closed: Literal[True]


class TimeframesResponse(BaseModel):
    timeframes: list[str]
