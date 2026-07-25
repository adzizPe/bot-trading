from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from app.risk.exceptions import RiskError
from app.risk_feasibility.builder import CandidateRiskContextBuilder
from app.risk_feasibility.engine import RiskFeasibilityEngine
from app.risk_feasibility.gateway import SnapshotReadError
from app.risk_feasibility.mapper import RiskFeasibilityResultMapper
from app.risk_feasibility.types import (
    FeasibilityCalculation,
    ReasonCode,
    unavailable_result,
)
from app.risk_feasibility.validator import FeasibilityInputValidator


class SignalReader(Protocol):
    async def get_by_id(self, signal_id: str) -> dict[str, Any] | None: ...


class SettingsReader(Protocol):
    async def get_active(self) -> dict[str, Any] | None: ...


class SnapshotReader(Protocol):
    async def read(self, symbol: str) -> Any: ...


class FeasibilitySignalNotFoundError(Exception):
    pass


class RiskFeasibilityService:
    def __init__(
        self,
        signals: SignalReader,
        settings: SettingsReader,
        snapshots: SnapshotReader,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._signals = signals
        self._settings = settings
        self._snapshots = snapshots
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._builder = CandidateRiskContextBuilder()
        self._validator = FeasibilityInputValidator()
        self._engine = RiskFeasibilityEngine()
        self._mapper = RiskFeasibilityResultMapper()

    async def analyze(self, signal_id: str) -> dict[str, Any]:
        signal = await self._signals.get_by_id(signal_id)
        if signal is None:
            raise FeasibilitySignalNotFoundError
        try:
            stored = await self._settings.get_active()
        except Exception:
            stored = None
        if stored is None:
            return unavailable_result(
                signal_id=signal_id,
                symbol=str(signal.get("symbol", "")),
                direction=str(signal.get("direction", "")),
                now=self._clock(),
                code=ReasonCode.INPUT_INVALID,
            )
        try:
            snapshot = await self._snapshots.read(str(signal["symbol"]))
        except (SnapshotReadError, KeyError):
            return unavailable_result(
                signal_id=signal_id,
                symbol=str(signal.get("symbol", "")),
                direction=str(signal.get("direction", "")),
                now=self._clock(),
                code=ReasonCode.SNAPSHOT_UNAVAILABLE,
            )
        try:
            raw = self._builder.build(signal, stored, snapshot)
        except KeyError:
            return unavailable_result(
                signal_id=signal_id,
                symbol=str(signal.get("symbol", "")),
                direction=str(signal.get("direction", "")),
                now=self._clock(),
                code=ReasonCode.SNAPSHOT_UNAVAILABLE,
            )
        except (RiskError, TypeError, ValueError):
            return unavailable_result(
                signal_id=signal_id,
                symbol=str(signal.get("symbol", "")),
                direction=str(signal.get("direction", "")),
                now=self._clock(),
                code=ReasonCode.INPUT_INVALID,
            )
        outcome = self._validator.validate(raw)
        calculation = (
            self._engine.calculate(outcome.value)
            if outcome.value is not None
            else FeasibilityCalculation.unavailable(outcome.reasons)
        )
        return self._mapper.map(raw, calculation)
