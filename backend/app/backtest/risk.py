from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.backtest.exceptions import BacktestRiskRejected
from app.backtest.types import BacktestConfig, BacktestState, deterministic_id, utc_datetime
from app.risk.calculators import (
    PositionSizeCalculator,
    StopLossCalculator,
    TakeProfitCalculator,
)
from app.risk.exceptions import RiskError
from app.risk.manager import RiskManager as SharedRiskManager
from app.risk.types import RiskConfig, SymbolSpecification
from app.risk.validators import RiskRewardValidator


class BacktestStateManager:
    """Own deterministic account/risk state without persistence or live services."""

    def __init__(self, initial_balance: Any = "10000") -> None:
        balance = Decimal(str(initial_balance))
        self.state = BacktestState(
            balance=balance,
            equity=balance,
            peak_equity=balance,
            day_start_balance=balance,
            day_peak_equity=balance,
        )
        self._processed: set[str] = set()

    def begin_day(self, when: datetime) -> None:
        day = utc_datetime(when).date()
        if self.state.current_day == day:
            return
        self.state.current_day = day
        self.state.day_start_balance = self.state.balance
        self.state.day_peak_equity = self.state.equity
        self.state.daily_pnl = Decimal("0")
        self.state.daily_realized_loss = Decimal("0")
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
        if value < 0:
            self.state.daily_realized_loss += abs(value)
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.consecutive_losses = self.state.consecutive_losses + 1 if value < 0 else 0
        self.state.peak_equity = max(self.state.peak_equity, self.state.equity)
        self.state.day_peak_equity = max(self.state.day_peak_equity, self.state.equity)

    def mark_to_market(self, floating_pnl: Any) -> Decimal:
        self.state.equity = self.state.balance + Decimal(str(floating_pnl))
        self.state.peak_equity = max(self.state.peak_equity, self.state.equity)
        self.state.day_peak_equity = max(
            self.state.day_peak_equity, self.state.equity
        )
        return self.state.equity

    def snapshot(self) -> dict[str, Any]:
        return dict(vars(self.state))


class BacktestRiskManager:
    """Apply the shared paper-risk rules to deterministic in-memory state."""

    def __init__(
        self,
        config: BacktestConfig | None = None,
        state_manager: BacktestStateManager | None = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self.state_manager = state_manager or BacktestStateManager(
            self.config.initial_balance
        )
        self._config = RiskConfig(
            risk_per_trade_percent=self.config.risk_per_trade_percent,
            max_daily_loss_percent=self.config.max_daily_loss_percent,
            max_daily_drawdown_percent=self.config.max_daily_drawdown_percent,
            max_consecutive_losses=self.config.max_consecutive_losses,
            max_trades_per_day=self.config.max_trades_per_day,
            max_open_positions=self.config.max_open_positions,
            minimum_risk_reward=self.config.minimum_risk_reward,
            maximum_spread_points=self.config.maximum_spread_points,
            cooldown_minutes_after_loss=self.config.cooldown_minutes_after_loss,
            use_equity_for_risk=self.config.use_equity_for_risk,
            stop_loss_method=self.config.stop_loss_method,
            atr_multiplier=self.config.stop_atr_multiplier,
            target_risk_reward=self.config.target_risk_reward,
            session_enabled=False,
        )
        self._specification = SymbolSpecification(
            digits=max(0, -self.config.point.as_tuple().exponent),
            point=self.config.point,
            trade_tick_size=self.config.tick_size,
            trade_tick_value=self.config.tick_value,
            volume_min=self.config.volume_min,
            volume_max=self.config.volume_max,
            volume_step=self.config.volume_step,
            trade_stops_level=self.config.trade_stops_level,
            trade_freeze_level=self.config.trade_freeze_level,
        )
        self._risk = SharedRiskManager()
        self._position = PositionSizeCalculator()
        self._stop = StopLossCalculator()
        self._target = TakeProfitCalculator()
        self._reward = RiskRewardValidator()

    @property
    def state(self) -> BacktestState:
        return self.state_manager.state

    def rejection_reasons(
        self,
        signal_id: str,
        when: datetime,
        spread_points: Any | None = None,
    ) -> list[str]:
        current = utc_datetime(when)
        self.state_manager.begin_day(current)
        state = self.state
        reasons = ["Signal already processed"] if self.state_manager.seen(signal_id) else []
        account = {"balance": state.balance, "equity": state.equity}
        risk_state = {
            "starting_balance": state.day_start_balance,
            "peak_equity": state.day_peak_equity,
            "realized_loss": state.daily_realized_loss,
            "floating_drawdown": max(
                state.day_peak_equity - state.equity, Decimal("0")
            ),
            "consecutive_losses": state.consecutive_losses,
            "trades_count": state.trades_today,
            "open_positions": state.open_positions,
            "cooldown_until": state.cooldown_until,
        }
        spread = self.config.spread_points if spread_points is None else spread_points
        reasons.extend(
            self._risk.validate_locks(
                account, risk_state, self._config, spread, current
            )
        )
        return list(dict.fromkeys(reasons))

    validate = rejection_reasons

    def approve(
        self,
        signal_id: str,
        when: datetime,
        spread_points: Any | None = None,
    ) -> None:
        reasons = self.rejection_reasons(signal_id, when, spread_points)
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
        spread_points: Any | None = None,
    ) -> dict[str, Any]:
        try:
            signal_id = str(signal["signal_id"])
            direction = str(
                getattr(signal["direction"], "value", signal["direction"])
            ).upper()
            entry = Decimal(str(entry_price))
            reasons = self.rejection_reasons(
                signal_id, decision_time, spread_points
            )
            if reasons:
                raise BacktestRiskRejected("; ".join(reasons))
            stop = self._stop.calculate(
                direction,
                entry,
                atr,
                self._config,
                self._specification,
                signal.get("stop_reference_price"),
            )
            target = self._target.calculate(
                direction,
                entry,
                stop["stop_loss"],
                self._config,
                self._specification,
                signal.get("target_price"),
            )
            reward_reasons = self._reward.validate(
                direction,
                entry,
                stop["stop_loss"],
                target["take_profit"],
                self._config,
            )
            if reward_reasons:
                raise BacktestRiskRejected("; ".join(reward_reasons))
            sizing = self._position.calculate(
                self.state.balance,
                self.state.equity,
                stop["stop_distance"],
                self._config,
                self._specification,
            )
            self.approve(signal_id, decision_time, spread_points)
        except BacktestRiskRejected:
            raise
        except (RiskError, KeyError, TypeError, ValueError) as error:
            raise BacktestRiskRejected(str(error)) from error
        return {
            "trade_plan_id": deterministic_id("plan", signal_id, decision_time),
            "signal_id": signal_id,
            "symbol": signal.get("symbol", "XAUUSD"),
            "direction": direction,
            "decision_time": utc_datetime(decision_time),
            "entry_reference_price": entry,
            "executable_entry_price": entry,
            "stop_loss": Decimal(str(stop["stop_loss"])),
            "take_profit": Decimal(str(target["take_profit"])),
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
