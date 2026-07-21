from collections.abc import Iterable, Mapping
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from app.backtest.statistics import BacktestStatisticsService, EquityCurveService


class BacktestReportService:
    """Build a deterministic, serializable report from pure domain results."""

    def __init__(
        self,
        statistics: BacktestStatisticsService | None = None,
        equity: EquityCurveService | None = None,
    ) -> None:
        self.statistics = statistics or BacktestStatisticsService()
        self.equity = equity or EquityCurveService()

    def generate(
        self,
        trades: Iterable[Mapping[str, Any]],
        initial_balance: Any,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        items = sorted(
            trades,
            key=lambda item: (item["closed_at"], str(item.get("trade_id", ""))),
        )
        curve = self.equity.build(items, initial_balance)
        stats = self.statistics.calculate(items, initial_balance, curve)
        return {
            "metadata": dict(metadata or {}),
            "statistics": self._plain(stats),
            "equity_curve": [self._plain(asdict(point)) for point in curve],
            "trades": [self._plain(dict(trade)) for trade in items],
        }

    build = generate

    @classmethod
    def _plain(cls, value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {key: cls._plain(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._plain(item) for item in value]
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value
