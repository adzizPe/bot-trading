from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from app.backtest.types import DrawdownResult, EquityPoint


class EquityCurveService:
    def build(
        self, trades: Iterable[Mapping[str, Any]], initial_balance: Any
    ) -> list[EquityPoint]:
        balance = Decimal(str(initial_balance))
        items = sorted(trades, key=lambda item: item["closed_at"])
        if not items:
            return []
        first = items[0]
        start_time = first.get("opened_at", first["closed_at"])
        curve = [EquityPoint(timestamp=start_time, equity=balance)]
        for trade in items:
            balance += Decimal(str(trade["net_pnl"]))
            curve.append(EquityPoint(timestamp=trade["closed_at"], equity=balance))
        return curve

    calculate = build


class DrawdownCalculator:
    def calculate(self, curve: Iterable[EquityPoint]) -> DrawdownResult:
        peak: Decimal | None = None
        maximum = Decimal("0")
        maximum_percent = Decimal("0")
        current = Decimal("0")
        for point in curve:
            peak = point.equity if peak is None else max(peak, point.equity)
            current = peak - point.equity
            percent = current / peak * 100 if peak > 0 else Decimal("0")
            if current > maximum:
                maximum, maximum_percent = current, percent
        return DrawdownResult(max_drawdown=maximum, max_drawdown_percent=maximum_percent, current_drawdown=current)


class BacktestStatisticsService:
    def __init__(self, drawdown: DrawdownCalculator | None = None) -> None:
        self.drawdown = drawdown or DrawdownCalculator()

    def calculate(
        self,
        trades: Iterable[Mapping[str, Any]],
        initial_balance: Any,
        equity_curve: Iterable[EquityPoint] | None = None,
    ) -> dict[str, Any]:
        items = sorted(
            trades,
            key=lambda item: (item["closed_at"], str(item.get("trade_id", ""))),
        )
        initial = Decimal(str(initial_balance))
        pnl = [Decimal(str(item["net_pnl"])) for item in items]
        wins = [value for value in pnl if value > 0]
        losses = [value for value in pnl if value < 0]
        net = sum(pnl, Decimal("0"))
        gross_profit = sum(wins, Decimal("0"))
        gross_loss = -sum(losses, Decimal("0"))
        curve = list(equity_curve or EquityCurveService().build(items, initial))
        drawdown = self.drawdown.calculate(curve)
        total = len(items)
        risk_rewards = [self._risk_reward(item) for item in items]
        risk_rewards = [value for value in risk_rewards if value is not None]
        return {
            "final_balance": initial + net,
            "net_profit": net,
            "total_return_percent": net / initial * 100 if initial else Decimal("0"),
            "total_trades": total,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": Decimal(len(wins) * 100) / total if total else Decimal("0"),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss else Decimal("0"),
            "expectancy": net / total if total else Decimal("0"),
            "average_win": sum(wins, Decimal("0")) / len(wins) if wins else Decimal("0"),
            "average_loss": sum(losses, Decimal("0")) / len(losses) if losses else Decimal("0"),
            "maximum_drawdown": drawdown.max_drawdown,
            "maximum_drawdown_percent": drawdown.max_drawdown_percent,
            "consecutive_wins": self._streak(items, positive=True),
            "consecutive_losses": self._streak(items, positive=False),
            "average_risk_reward": (
                sum(risk_rewards, Decimal("0")) / len(risk_rewards)
                if risk_rewards else Decimal("0")
            ),
            "sharpe_ratio": self._sharpe(pnl),
        }

    summarize = calculate

    @staticmethod
    def _risk_reward(item: Mapping[str, Any]) -> Decimal | None:
        required = ("entry_price", "stop_loss", "take_profit")
        if not all(name in item for name in required):
            return None
        entry = Decimal(str(item["entry_price"]))
        risk = abs(entry - Decimal(str(item["stop_loss"])))
        reward = abs(Decimal(str(item["take_profit"])) - entry)
        return reward / risk if risk else Decimal("0")

    @staticmethod
    def _sharpe(values: list[Decimal]) -> Decimal | None:
        if len(values) < 2:
            return None
        mean = sum(values, Decimal("0")) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        if variance <= 0:
            return None
        return mean / variance.sqrt() * Decimal(len(values)).sqrt()

    @staticmethod
    def _streak(items: list[Mapping[str, Any]], *, positive: bool) -> int:
        maximum = current = 0
        for item in items:
            pnl = Decimal(str(item["net_pnl"]))
            matches = pnl > 0 if positive else pnl < 0
            current = current + 1 if matches else 0
            maximum = max(maximum, current)
        return maximum
