from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from app.risk_feasibility.types import AtomicRiskSnapshot, SnapshotTimestamps


class RiskSnapshotSource(Protocol):
    async def risk_snapshot(self, requested_symbol: str | None = None) -> dict[str, Any]: ...


class SnapshotReadError(Exception):
    """Sanitized boundary for upstream snapshot failures."""


class ReadOnlyRiskSnapshotGateway:
    """Exposes only snapshot reads; trading capabilities are intentionally absent."""

    def __init__(
        self,
        source: RiskSnapshotSource,
        clock: Callable[[], datetime],
    ) -> None:
        self._source = source
        self._clock = clock

    async def read(self, symbol: str) -> AtomicRiskSnapshot:
        captured_at = _utc(self._clock())
        try:
            payload = await self._source.risk_snapshot(symbol)
        except Exception as error:
            raise SnapshotReadError("Risk snapshot is unavailable") from error
        tick_at = _tick_time(payload.get("tick", {}))
        fresh_until = None if tick_at is None else tick_at.replace(
            microsecond=tick_at.microsecond
        )
        if fresh_until is not None:
            from datetime import timedelta

            fresh_until += timedelta(seconds=60)
        return AtomicRiskSnapshot(
            payload=payload,
            timestamps=SnapshotTimestamps(
                captured_at=captured_at,
                account_at=captured_at,
                symbol_at=captured_at,
                tick_at=tick_at,
                fresh_until=fresh_until,
            ),
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _tick_time(tick: dict[str, Any]) -> datetime | None:
    raw_milliseconds = tick.get("time_msc")
    raw_seconds = tick.get("time")
    try:
        seconds = (
            float(raw_milliseconds) / 1000
            if raw_milliseconds not in {None, 0, ""}
            else float(raw_seconds)
        )
        if seconds <= 0:
            return None
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
