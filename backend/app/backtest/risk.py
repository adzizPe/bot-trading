from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.backtest.exceptions import BacktestRiskRejected
from app.backtest.types import BacktestConfig, BacktestState, deterministic_id, utc_datetime
from app.risk.calculators import PositionSizeCalculator
from app.risk.types import RiskConfig, SymbolSpecification


class BacktestStateManager:
    """Own deterministic account/risk state without persistence or live services."""

    def __init__(self, initial_balance: Any = "10000") -> None:
        balance = Decimal(str(initial_balance))
        self.state = BacktestState(
            balance=balance, equity=balance, peak_equity=balance,
            day_start_balance=balance,
        )
        self._processed: set[str] = set()

    def begin_day(self, when: datetime) -> None:
        day = utc_datetime(when).date()
        if self.state.current_day == day:
            return
        self.state.current_day = day
        self.state.day_start_balance = self.state.balance
        self.state.daily_pnl = Decimal("0")
        self.state.trades_today = 0

    def seen(self, identifier: str) -> bool:
        return identifier in self._processed

    def claim(self, identifier: str) -> bool:
        if identifier in self._processed:
            return False
        self._processed.add(identifier)
        return True

    def record_close(self, pnl: Any, when: datetime) -> None:
        self.begin_day(when)
        value = Decimal(str(pnl))
        self.state.balance += value
        self.state.equity = self.state.balance
        self.state.daily_pnl += value
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.consecutive_losses = self.state.consecutive_losses + 1 if value < 0 else 0
        self.state.peak_equity = max(self.state.peak_equity, self.state.equity)

    def mark_to_market(self, floating_pnl: Any) -> Decimal:
        self.state.equity = self.state.balance + Decimal(str(floating_pnl))
        self.state.peak_equity = max(self.state.peak_equity, self.state.equity)
        return self.state.equity

    def snapshot(self) -> dict[str, Any]:
        return dict(vars(self.state))


class BacktestRiskManager:
    """Apply daily limits and signal deduplication entirely in memory."""

    def __init__(
        self,
        config: BacktestConfig | None = None,
        state_manager: BacktestStateManager | None = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self.state_manager = state_manager or BacktestStateManager(
            self.config.initial_balance
        )

    @property
    def state(self) -> BacktestState:
        return self.state_manager.state

    def rejection_reasons(self, signal_id: str, when: datetime) -> list[str]:
        self.state_manager.begin_day(when)
        state, config = self.state, self.config
        reasons: list[str] = []
        if self.state_manager.seen(signal_id):
            reasons.append("Signal already processed")
        loss_limit = state.day_start_balance * config.max_daily_loss_percent / 100
        if -min(state.daily_pnl, Decimal("0")) >= loss_limit:
            reasons.append("Maximum daily loss reached")
        drawdown = state.peak_equity - state.equity
        if drawdown >= state.peak_equity * config.max_daily_drawdown_percent / 100:
            reasons.append("Maximum daily drawdown reached")
        if state.trades_today >= config.max_trades_per_day:
            reasons.append("Maximum trades per day reached")
        if state.consecutive_losses >= config.max_consecutive_losses:
            reasons.append("Maximum consecutive losses reached")
        if state.open_positions >= config.max_open_positions:
            reasons.append("Maximum open positions reached")
        if state.cooldown_until is not None and utc_datetime(when) < state.cooldown_until:
            reasons.append("Loss cooldown is active")
        return reasons

    validate = rejection_reasons

    def approve(self, signal_id: str, when: datetime) -> None:
        reasons = self.rejection_reasons(signal_id, when)
        if reasons:
            raise BacktestRiskRejected("; ".join(reasons))
        self.state_manager.claim(signal_id)
        self.state.trades_today += 1
        self.state.open_positions += 1

    def create_trade_plan(
        self,
        signal: dict[str, Any],
        *,
        entry_price: Any,
        atr: Any,
        decision_time: datetime,
    ) -> dict[str, Any]:
        signal_id = str(signal["signal_id"])
        direction = str(getattr(signal["direction"], "value", signal["direction"])).upper()
        entry = Decimal(str(entry_price))
        distance = Decimal(str(atr)) * self.config.stop_atr_multiplier
        if direction == "BUY":
            stop, target = entry - distance, entry + distance * self.config.target_risk_reward
        elif direction == "SELL":
            stop, target = entry + distance, entry - distance * self.config.target_risk_reward
        else:
            raise BacktestRiskRejected("direction must be BUY or SELL")
        if min(entry, stop, target, distance) <= 0:
            raise BacktestRiskRejected("trade plan prices must be positive")
        risk_config = RiskConfig(
            maximum_spread_points=self.config.spread_points,
            risk_per_trade_percent=self.config.risk_per_trade_percent,
            session_enabled=False,
        )
        specification = SymbolSpecification(
            digits=max(0, -self.config.point.as_tuple().exponent),
            point=self.config.point,
            trade_tick_size=self.config.tick_size,
            trade_tick_value=self.config.tick_value,
            volume_min=self.config.volume_min,
            volume_max=self.config.volume_max,
            volume_step=self.config.volume_step,
            trade_stops_level=Decimal("0"),
            trade_freeze_level=Decimal("0"),
        )
        sizing = PositionSizeCalculator().calculate(
            self.state.balance, self.state.equity, distance,
            risk_config, specification,
        )
        self.approve(signal_id, decision_time)
        return {
            "trade_plan_id": deterministic_id("plan", signal_id, decision_time),
            "signal_id": signal_id,
            "symbol": signal.get("symbol", "XAUUSD"),
            "direction": direction,
            "decision_time": utc_datetime(decision_time),
            "entry_reference_price": entry,
            "stop_loss": stop,
            "take_profit": target,
            "volume": Decimal(str(sizing["lot_size"])),
            "risk_amount": Decimal(str(sizing["risk_amount"])),
        }

    def record_close(self, trade: dict[str, Any]) -> None:
        when = utc_datetime(trade["closed_at"], "closed_at")
        pnl = Decimal(str(trade["net_pnl"]))
        self.state_manager.record_close(pnl, when)
        if pnl < 0 and self.config.cooldown_minutes_after_loss:
            self.state.cooldown_until = when + timedelta(
                minutes=self.config.cooldown_minutes_after_loss
            )
