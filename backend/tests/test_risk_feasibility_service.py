from datetime import datetime, timezone
from typing import Any

import pytest

from app.risk_feasibility.gateway import ReadOnlyRiskSnapshotGateway
from app.risk_feasibility.service import (
    FeasibilitySignalNotFoundError,
    RiskFeasibilityService,
)

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


class Signals:
    def __init__(self, signal: dict[str, Any] | None) -> None:
        self.signal = signal
        self.reads = 0

    async def get_by_id(self, signal_id: str) -> dict[str, Any] | None:
        self.reads += 1
        return self.signal


class SettingsReader:
    def __init__(self, settings: dict[str, Any] | None) -> None:
        self.settings = settings
        self.reads = 0

    async def get_active(self) -> dict[str, Any] | None:
        self.reads += 1
        return self.settings


class SnapshotSource:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def risk_snapshot(self, requested_symbol: str | None = None) -> dict[str, Any]:
        self.calls.append(str(requested_symbol))
        return self.payload


def signal(direction: str = "BUY") -> dict[str, Any]:
    return {
        "signal_id": "signal-1", "symbol": "XAUUSD", "direction": direction,
        "status": "CANDIDATE", "atr": 2.0,
    }


def settings() -> dict[str, Any]:
    return {
        "settings_id": "default", "risk_per_trade_percent": 1.0,
        "max_daily_loss_percent": 3.0, "max_daily_drawdown_percent": 5.0,
        "max_consecutive_losses": 3, "max_trades_per_day": 5,
        "max_open_positions": 1, "minimum_risk_reward": 1.5,
        "target_risk_reward": 2.0, "maximum_spread_points": 300.0,
        "cooldown_minutes_after_loss": 30, "use_equity_for_risk": True,
        "break_even_enabled": False, "trailing_stop_enabled": False,
        "stop_loss_method": "ATR", "atr_multiplier": 1.5,
        "session_enabled": True, "session_start_hour_utc": 0,
        "session_end_hour_utc": 24, "session_weekdays": [0, 1, 2, 3, 4],
        "updated_at": NOW,
    }


def snapshot(*, equity: float = 10_000.0, symbol: str = "XAUUSD") -> dict[str, Any]:
    timestamp = int(NOW.timestamp())
    return {
        "account": {
            "balance": 10_000.0, "equity": equity, "currency": "USD",
        },
        "symbol": {
            "name": symbol, "digits": 2, "point": 0.01,
            "trade_tick_size": 0.01, "trade_tick_value": 1.0,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "trade_stops_level": 10, "trade_freeze_level": 0,
            "trade_contract_size": 100.0,
        },
        "tick": {
            "bid": 3000.0, "ask": 3000.2,
            "time": timestamp, "time_msc": timestamp * 1000,
        },
    }


def service_for(
    candidate: dict[str, Any] | None,
    stored: dict[str, Any] | None,
    source: SnapshotSource,
) -> RiskFeasibilityService:
    return RiskFeasibilityService(
        Signals(candidate), SettingsReader(stored),
        ReadOnlyRiskSnapshotGateway(source, lambda: NOW), lambda: NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("direction", "entry"), [("BUY", "3000.2"), ("SELL", "3000")])
async def test_service_uses_authoritative_side_and_only_snapshot_capability(
    direction: str, entry: str
) -> None:
    source = SnapshotSource(snapshot())
    analyzer = service_for(signal(direction), settings(), source)
    result = await analyzer.analyze("signal-1")
    assert result["status"] == "FEASIBLE"
    assert result["market"]["entry_price"] == entry
    assert result["account"]["risk_base_value"] == "10000"
    assert result["recommendation"] == "PROCEED_TO_AUTHORITATIVE_TRADE_PLAN_FLOW"
    assert source.calls == ["XAUUSD"]
    assert not hasattr(analyzer, "create_trade_plan")
    assert not hasattr(source, "order_send")


@pytest.mark.asyncio
async def test_service_repetition_is_deterministic_and_has_zero_write_capability() -> None:
    source = SnapshotSource(snapshot(equity=10.0))
    analyzer = service_for(signal(), settings(), source)
    first = await analyzer.analyze("signal-1")
    second = await analyzer.analyze("signal-1")
    assert first == second
    assert first["status"] == "INFEASIBLE"
    assert first["recommendation"] == "DO_NOT_FORCE_MINIMUM_LOT"
    assert source.calls == ["XAUUSD", "XAUUSD"]
    assert "trade_plan_id" not in str(first)


@pytest.mark.asyncio
async def test_missing_settings_is_unavailable_without_snapshot_read() -> None:
    source = SnapshotSource(snapshot())
    analyzer = service_for(signal(), None, source)
    result = await analyzer.analyze("signal-1")
    assert result["status"] == "UNAVAILABLE"
    assert result["reasons"][0]["code"] == "INPUT_INVALID"
    assert source.calls == []


@pytest.mark.asyncio
async def test_missing_signal_is_404_domain_error_without_other_reads() -> None:
    source = SnapshotSource(snapshot())
    analyzer = service_for(None, settings(), source)
    with pytest.raises(FeasibilitySignalNotFoundError):
        await analyzer.analyze("missing")
    assert source.calls == []


@pytest.mark.asyncio
async def test_symbol_mismatch_is_unavailable() -> None:
    source = SnapshotSource(snapshot(symbol="GOLD"))
    result = await service_for(signal(), settings(), source).analyze("signal-1")
    assert result["status"] == "UNAVAILABLE"
    assert result["reasons"][0]["code"] == "SYMBOL_MISMATCH"


@pytest.mark.asyncio
async def test_upstream_exception_is_sanitized_unavailable() -> None:
    class FailingSource:
        async def risk_snapshot(
            self, requested_symbol: str | None = None
        ) -> dict[str, Any]:
            raise RuntimeError("Authorization: bearer secret login=123")

    analyzer = RiskFeasibilityService(
        Signals(signal()), SettingsReader(settings()),
        ReadOnlyRiskSnapshotGateway(FailingSource(), lambda: NOW), lambda: NOW,
    )
    result = await analyzer.analyze("signal-1")
    assert result["status"] == "UNAVAILABLE"
    assert result["reasons"][0]["code"] == "SNAPSHOT_UNAVAILABLE"
    assert "secret" not in str(result)
    assert "login=123" not in str(result)
