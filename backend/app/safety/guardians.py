import math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.safety.types import GuardianResult, SafetyContext


class EmergencyStopGuardian:
    name = "EmergencyStopGuardian"

    def evaluate(self, _: SafetyContext, *, active: bool = False) -> GuardianResult:
        return GuardianResult(self.name, not active, "Emergency stop is active" if active else None)


class ConnectionGuardian:
    name = "ConnectionGuardian"

    def evaluate(self, context: SafetyContext) -> GuardianResult:
        value = context.connection
        checks = {
            "connected": bool(value.get("connected")),
            "demo_verified": bool(value.get("demo_verified")),
            "terminal_trade_allowed": bool(value.get("terminal_trade_allowed")),
            "terminal_api_enabled": not bool(value.get("terminal_api_disabled")),
        }
        allowed = all(checks.values())
        return GuardianResult(
            self.name, allowed,
            None if allowed else "MT5 connection or terminal trading is unavailable",
            checks,
        )


class SpreadGuardian:
    name = "SpreadGuardian"

    def evaluate(self, context: SafetyContext) -> GuardianResult:
        spread = context.spread_points
        allowed = (
            spread is not None and math.isfinite(spread) and spread >= 0
            and spread <= context.max_spread_points
        )
        return GuardianResult(
            self.name, allowed, None if allowed else "Spread exceeds the safety limit",
            {"spread_points": spread, "max_spread_points": context.max_spread_points},
        )


class DailyLossGuardian:
    name = "DailyLossGuardian"

    def evaluate(self, context: SafetyContext) -> GuardianResult:
        state = context.risk.get("state") or {}
        starting = float(state.get("starting_balance") or 0)
        loss = float(state.get("realized_loss") or 0)
        limit = float(context.risk_settings.get("max_daily_loss_percent") or 0)
        percent = loss / starting * 100 if starting > 0 else math.inf
        allowed = starting > 0 and limit > 0 and percent < limit
        return GuardianResult(
            self.name, allowed, None if allowed else "Daily loss limit reached",
            {"daily_loss_percent": percent, "limit_percent": limit},
        )


class DrawdownGuardian:
    name = "DrawdownGuardian"

    def evaluate(self, context: SafetyContext) -> GuardianResult:
        state = context.risk.get("state") or {}
        peak = float(state.get("peak_equity") or 0)
        drawdown = float(state.get("floating_drawdown") or 0)
        limit = float(context.risk_settings.get("max_daily_drawdown_percent") or 0)
        percent = drawdown / peak * 100 if peak > 0 else math.inf
        allowed = peak > 0 and limit > 0 and percent < limit
        return GuardianResult(
            self.name, allowed, None if allowed else "Drawdown limit reached",
            {"drawdown_percent": percent, "limit_percent": limit},
        )


class WeekendGuardian:
    name = "WeekendGuardian"

    def evaluate(self, context: SafetyContext) -> GuardianResult:
        allowed = context.now.weekday() < 5
        return GuardianResult(
            self.name, allowed, None if allowed else "Weekend trading is blocked",
            {"weekday": context.now.weekday()},
        )


class TradingSessionGuardian:
    name = "TradingSessionGuardian"
    PRESETS = {
        "LONDON": ("Europe/London", time(8), time(17)),
        "NEW_YORK": ("America/New_York", time(8), time(17)),
        "ASIA": ("Asia/Tokyo", time(9), time(17)),
    }

    def __init__(
        self, active_sessions: tuple[str, ...] = ("LONDON", "NEW_YORK", "ASIA"),
        custom_timezone: str = "UTC", custom_start: time = time(0),
        custom_end: time = time(23, 59), custom_weekdays: tuple[int, ...] = tuple(range(5)),
    ) -> None:
        self.active_sessions = tuple(name.upper() for name in active_sessions)
        self.custom = (custom_timezone, custom_start, custom_end, custom_weekdays)

    @staticmethod
    def _inside(now: datetime, zone: str, start: time, end: time, weekdays: tuple[int, ...]) -> bool:
        local = now.astimezone(ZoneInfo(zone))
        if local.weekday() not in weekdays:
            return False
        current = local.timetz().replace(tzinfo=None)
        return start <= current < end if start < end else current >= start or current < end

    def evaluate(self, context: SafetyContext) -> GuardianResult:
        matched: list[str] = []
        for name in self.active_sessions:
            if name == "CUSTOM":
                zone, start, end, weekdays = self.custom
            elif name in self.PRESETS:
                zone, start, end = self.PRESETS[name]
                weekdays = tuple(range(5))
            else:
                continue
            if self._inside(context.now, zone, start, end, weekdays):
                matched.append(name)
        allowed = bool(matched)
        return GuardianResult(
            self.name, allowed, None if allowed else "Current time is outside active sessions",
            {"active_sessions": list(self.active_sessions), "matched_sessions": matched},
        )


class NewsGuardian:
    name = "NewsGuardian"

    def __init__(
        self, blackout_before_minutes: int = 30,
        blackout_after_minutes: int = 30,
        required: bool = False,
        stale_after_minutes: int = 60,
    ) -> None:
        self.before = timedelta(minutes=blackout_before_minutes)
        self.after = timedelta(minutes=blackout_after_minutes)
        self.required = required
        self.stale_after = timedelta(minutes=stale_after_minutes)

    def evaluate(self, context: SafetyContext) -> GuardianResult:
        updated = context.news_feed_updated_at
        if self.required and (
            updated is None or context.now - updated > self.stale_after
        ):
            return GuardianResult(self.name, False, "News feed is stale or unavailable")
        blocking: list[str] = []
        for event in context.news_events:
            event_at = event.get("scheduled_at")
            if not isinstance(event_at, datetime):
                continue
            if str(event.get("impact", "")).upper() != "HIGH":
                continue
            if event_at - self.before <= context.now <= event_at + self.after:
                blocking.append(str(event.get("title", "High-impact event")))
        allowed = not blocking
        return GuardianResult(
            self.name, allowed, None if allowed else "High-impact news blackout is active",
            {"blocking_events": blocking},
        )


class DuplicateOrderGuardian:
    name = "DuplicateOrderGuardian"

    def evaluate(self, context: SafetyContext) -> GuardianResult:
        return GuardianResult(
            self.name, not context.duplicate,
            "Trade plan was already submitted" if context.duplicate else None,
            {"trade_plan_id": context.trade_plan_id},
        )
