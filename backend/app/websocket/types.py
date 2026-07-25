import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from fastapi import WebSocket

from app.auth.permissions import Permission
from app.auth.principal import Principal


class WebSocketTopic(str, Enum):
    MARKET = "market"
    ANALYSIS = "analysis"
    SIGNALS = "signals"
    PAPER = "paper"
    BACKTEST = "backtest"
    LOGS = "logs"
    HEALTH = "health"


TOPIC_PERMISSIONS: dict[WebSocketTopic, Permission] = {
    WebSocketTopic.MARKET: Permission.READ_MARKET,
    WebSocketTopic.ANALYSIS: Permission.READ_SIGNALS,
    WebSocketTopic.SIGNALS: Permission.READ_SIGNALS,
    WebSocketTopic.PAPER: Permission.READ_DASHBOARD,
    WebSocketTopic.BACKTEST: Permission.READ_STATISTICS,
    WebSocketTopic.LOGS: Permission.READ_DASHBOARD,
    WebSocketTopic.HEALTH: Permission.READ_DASHBOARD,
}


@dataclass(slots=True)
class OutboundMessage:
    payload: dict[str, Any]
    enqueued_at: float
    topic: WebSocketTopic | None = None


@dataclass(slots=True)
class CachedMessage:
    payload: dict[str, Any]
    captured_at: datetime


@dataclass(slots=True)
class HubConnection:
    connection_id: str
    websocket: WebSocket
    principal: Principal
    token: str
    source_ip: str
    topics: set[WebSocketTopic]
    queue: asyncio.Queue[OutboundMessage]
    connected_at: datetime
    last_activity_at: float
    last_pong_at: float
    raw_market: bool = False
    symbol: str | None = None
    last_ping_at: float = 0.0
    dropped_messages: int = 0
    close_code: int = 1000
    close_reason: str = "Connection closed"
    close_event: asyncio.Event = field(default_factory=asyncio.Event)
