from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

SafetyAction = Literal["OPEN_ORDER", "CLOSE_POSITION", "MODIFY_STOP", "CANCEL_PENDING"]


@dataclass(frozen=True)
class GuardianResult:
    name: str
    allowed: bool
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SafetyContext:
    action: SafetyAction
    now: datetime
    connection: dict[str, Any] = field(default_factory=dict)
    spread_points: float | None = None
    max_spread_points: float = 0.0
    risk: dict[str, Any] = field(default_factory=dict)
    risk_settings: dict[str, Any] = field(default_factory=dict)
    trade_plan_id: str | None = None
    duplicate: bool = False
    news_events: tuple[dict[str, Any], ...] = ()
    news_feed_updated_at: datetime | None = None


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    guardian: str | None
    reason: str | None
    results: tuple[GuardianResult, ...]
