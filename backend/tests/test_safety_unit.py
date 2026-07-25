from datetime import datetime, time, timedelta, timezone

import pytest

from app.safety.circuit import CircuitBreaker
from app.safety.guardians import (
    ConnectionGuardian,
    DailyLossGuardian,
    DrawdownGuardian,
    DuplicateOrderGuardian,
    NewsGuardian,
    SpreadGuardian,
    TradingSessionGuardian,
    WeekendGuardian,
)
from app.safety.types import SafetyContext

NOW = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)


def context(**changes: object) -> SafetyContext:
    values: dict[str, object] = {
        "action": "OPEN_ORDER",
        "now": NOW,
        "connection": {
            "connected": True,
            "demo_verified": True,
            "terminal_trade_allowed": True,
            "terminal_api_disabled": False,
        },
        "spread_points": 20.0,
        "max_spread_points": 30.0,
        "risk": {"state": {
            "starting_balance": 10_000.0,
            "realized_loss": 100.0,
            "peak_equity": 10_000.0,
            "floating_drawdown": 100.0,
        }},
        "risk_settings": {
            "max_daily_loss_percent": 3.0,
            "max_daily_drawdown_percent": 5.0,
        },
    }
    values.update(changes)
    return SafetyContext(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("spread", "limit", "allowed"),
    [(0, 1, True), (1, 1, True), (1.01, 1, False), (20, 30, True),
     (31, 30, False), (-1, 30, False), (float("inf"), 30, False),
     (None, 30, False)],
)
def test_spread_guardian_cases(spread: float | None, limit: float, allowed: bool) -> None:
    assert SpreadGuardian().evaluate(
        context(spread_points=spread, max_spread_points=limit)
    ).allowed is allowed


@pytest.mark.parametrize(
    ("field", "value"),
    [("connected", False), ("demo_verified", False),
     ("terminal_trade_allowed", False), ("terminal_api_disabled", True)],
)
def test_connection_guardian_blocks_each_failure(field: str, value: bool) -> None:
    connection = dict(context().connection)
    connection[field] = value
    assert ConnectionGuardian().evaluate(context(connection=connection)).allowed is False


@pytest.mark.parametrize("extra", [{}, {"latency": 1}, {"state": "connected"}, {"symbol": "XAUUSD"}])
def test_connection_guardian_allows_healthy_with_extra_fields(extra: dict[str, object]) -> None:
    connection = {**context().connection, **extra}
    assert ConnectionGuardian().evaluate(context(connection=connection)).allowed is True


@pytest.mark.parametrize(
    ("loss", "starting", "limit", "allowed"),
    [(0, 10000, 3, True), (299, 10000, 3, True), (300, 10000, 3, False),
     (301, 10000, 3, False), (1, 0, 3, False), (100, 10000, 0, False)],
)
def test_daily_loss_guardian_cases(
    loss: float, starting: float, limit: float, allowed: bool,
) -> None:
    risk = {"state": {"starting_balance": starting, "realized_loss": loss}}
    settings = {"max_daily_loss_percent": limit}
    assert DailyLossGuardian().evaluate(
        context(risk=risk, risk_settings=settings)
    ).allowed is allowed


@pytest.mark.parametrize(
    ("drawdown", "peak", "limit", "allowed"),
    [(0, 10000, 5, True), (499, 10000, 5, True), (500, 10000, 5, False),
     (501, 10000, 5, False), (1, 0, 5, False), (100, 10000, 0, False)],
)
def test_drawdown_guardian_cases(
    drawdown: float, peak: float, limit: float, allowed: bool,
) -> None:
    risk = {"state": {"peak_equity": peak, "floating_drawdown": drawdown}}
    settings = {"max_daily_drawdown_percent": limit}
    assert DrawdownGuardian().evaluate(
        context(risk=risk, risk_settings=settings)
    ).allowed is allowed


@pytest.mark.parametrize(
    ("day", "allowed"),
    [(0, True), (1, True), (2, True), (3, True), (4, True), (5, False), (6, False)],
)
def test_weekend_guardian_every_weekday(day: int, allowed: bool) -> None:
    now = NOW + timedelta(days=day - NOW.weekday())
    assert WeekendGuardian().evaluate(context(now=now)).allowed is allowed


@pytest.mark.parametrize(
    ("sessions", "hour", "allowed"),
    [(('LONDON',), 8, True), (('LONDON',), 12, True), (('LONDON',), 18, False),
     (('NEW_YORK',), 13, True), (('NEW_YORK',), 18, True), (('NEW_YORK',), 2, False),
     (('ASIA',), 0, True), (('ASIA',), 5, True), (('ASIA',), 12, False),
     (('LONDON', 'NEW_YORK'), 19, True), (('UNKNOWN',), 12, False),
     (('ASIA', 'LONDON', 'NEW_YORK'), 12, True)],
)
def test_session_presets(
    sessions: tuple[str, ...], hour: int, allowed: bool,
) -> None:
    guardian = TradingSessionGuardian(active_sessions=sessions)
    now = NOW.replace(hour=hour)
    assert guardian.evaluate(context(now=now)).allowed is allowed


@pytest.mark.parametrize(
    ("hour", "allowed"),
    [(21, False), (22, True), (23, True), (0, True), (1, True), (2, False)],
)
def test_custom_overnight_session(hour: int, allowed: bool) -> None:
    guardian = TradingSessionGuardian(
        active_sessions=("CUSTOM",), custom_timezone="UTC",
        custom_start=time(22), custom_end=time(2),
    )
    assert guardian.evaluate(context(now=NOW.replace(hour=hour))).allowed is allowed


@pytest.mark.parametrize(
    ("impact", "offset", "required", "allowed"),
    [("HIGH", 0, False, False), ("HIGH", 29, False, False),
     ("HIGH", 31, False, True), ("LOW", 0, False, True),
     ("MEDIUM", 0, False, True), ("HIGH", -31, False, True)],
)
def test_news_guardian_windows(
    impact: str, offset: int, required: bool, allowed: bool,
) -> None:
    event = {"title": "CPI", "impact": impact, "scheduled_at": NOW + timedelta(minutes=offset)}
    guardian = NewsGuardian(required=required)
    assert guardian.evaluate(context(news_events=(event,))).allowed is allowed


@pytest.mark.parametrize(
    ("updated_offset", "allowed"),
    [(0, True), (-59, True), (-61, False), (None, False)],
)
def test_required_news_feed_freshness(
    updated_offset: int | None, allowed: bool,
) -> None:
    updated = NOW + timedelta(minutes=updated_offset) if updated_offset is not None else None
    guardian = NewsGuardian(required=True, stale_after_minutes=60)
    assert guardian.evaluate(context(news_feed_updated_at=updated)).allowed is allowed


@pytest.mark.parametrize("duplicate", [False, True])
def test_duplicate_order_guardian(duplicate: bool) -> None:
    result = DuplicateOrderGuardian().evaluate(
        context(duplicate=duplicate, trade_plan_id="plan-one")
    )
    assert result.allowed is (not duplicate)


@pytest.mark.parametrize("threshold", [1, 2, 3, 4, 5])
def test_circuit_opens_at_configured_threshold(threshold: int) -> None:
    now = [NOW]
    circuit = CircuitBreaker(threshold=threshold, clock=lambda: now[0])
    for _ in range(threshold - 1):
        assert circuit.record_error() is False
        assert circuit.is_open() is False
    assert circuit.record_error() is True
    assert circuit.is_open() is True


def test_circuit_auto_recovers_after_lock_duration() -> None:
    now = [NOW]
    circuit = CircuitBreaker(threshold=1, lock_minutes=30, clock=lambda: now[0])
    circuit.record_error()
    now[0] += timedelta(minutes=31)
    assert circuit.is_open() is False


def test_circuit_prunes_errors_outside_window() -> None:
    now = [NOW]
    circuit = CircuitBreaker(threshold=2, window_minutes=30, clock=lambda: now[0])
    circuit.record_error()
    now[0] += timedelta(minutes=31)
    assert circuit.record_error() is False


def test_circuit_manual_reset() -> None:
    circuit = CircuitBreaker(threshold=1, clock=lambda: NOW)
    circuit.record_error()
    circuit.reset()
    assert circuit.status()["state"] == "CLOSED"
