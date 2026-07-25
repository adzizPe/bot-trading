from collections.abc import Iterable, Mapping
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from app.backtest.statistics import BacktestStatisticsService, EquityCurveService
from app.backtest.types import EquityPoint


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
        entry_reasons: Iterable[Mapping[str, Any]] = (),
        rejection_reasons: Iterable[Mapping[str, Any]] = (),
        warnings: Iterable[str] = (),
        equity_points: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        items = sorted(
            trades,
            key=lambda item: (item["closed_at"], str(item.get("trade_id", ""))),
        )
        supplied_curve = list(equity_points)
        if supplied_curve:
            curve = [
                EquityPoint(
                    timestamp=point["timestamp"],
                    equity=Decimal(str(point["equity"])),
                )
                for point in supplied_curve
            ]
            plain_curve = [self._plain(dict(point)) for point in supplied_curve]
        else:
            curve = self.equity.build(items, initial_balance)
            plain_curve = [self._plain(asdict(point)) for point in curve]
        stats = self.statistics.calculate(items, initial_balance, curve)
        entry_items, entry_count = self._bounded(entry_reasons, 2000)
        rejection_items, rejection_count = self._bounded(rejection_reasons, 2000)
        output_trades = items[:10_000]
        output_curve = plain_curve[:10_000]
        warning_list = sorted(set(warnings))[:200]
        if len(items) < 30:
            warning_list.append(
                "Fewer than 30 trades; statistical conclusions may be unreliable"
            )
        return {
            "metadata": self._plain(dict(metadata or {})),
            "config_summary": self._plain(dict(metadata or {})),
            "performance": self._plain(stats),
            "statistics": self._plain(stats),
            "equity_curve": output_curve,
            "drawdown_curve": self._drawdown_curve(output_curve),
            "pl_distribution": {
                "profits": [float(item["net_pnl"]) for item in output_trades if item["net_pnl"] > 0],
                "losses": [float(item["net_pnl"]) for item in output_trades if item["net_pnl"] < 0],
                "breakeven_count": sum(item["net_pnl"] == 0 for item in items),
            },
            "trades": [self._plain(dict(trade)) for trade in output_trades],
            "entry_reasons": self._plain(entry_items),
            "rejection_reasons": self._plain(rejection_items),
            "risk_per_trade": [
                {
                    "trade_id": item.get("trade_id"),
                    "risk_amount": self._plain(item.get("risk_amount", 0)),
                }
                for item in output_trades
            ],
            "warnings": warning_list,
            "truncation": {
                "trades": {"total": len(items), "included": len(output_trades)},
                "equity_curve": {"total": len(plain_curve), "included": len(output_curve)},
                "entry_reasons": {"total": entry_count, "included": len(entry_items)},
                "rejection_reasons": {
                    "total": rejection_count, "included": len(rejection_items)
                },
            },
            "disclaimer": (
                "Past performance is not indicative of future results. "
                "Backtest results are simulated and may differ from live execution."
            ),
        }

    build = generate

    @staticmethod
    def _bounded(
        values: Iterable[Mapping[str, Any]], limit: int
    ) -> tuple[list[Mapping[str, Any]], int]:
        result: list[Mapping[str, Any]] = []
        count = 0
        for value in values:
            count += 1
            if len(result) < limit:
                result.append(value)
        return result, count

    @staticmethod
    def _drawdown_curve(curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
        peak = 0.0
        result: list[dict[str, Any]] = []
        for point in curve:
            equity = float(point["equity"])
            peak = max(peak, equity)
            drawdown = max(peak - equity, 0.0)
            result.append({
                "timestamp": point["timestamp"],
                "drawdown": drawdown,
                "drawdown_percent": drawdown / peak * 100 if peak else 0.0,
            })
        return result

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
