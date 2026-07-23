import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.analysis.repository import SignalRepository
from app.config.settings import Settings
from app.mt5.exceptions import MT5Error
from app.mt5.manager import MT5ConnectionManager
from app.risk.calculators import (
    PositionSizeCalculator,
    StopLossCalculator,
    TakeProfitCalculator,
)
from app.risk.exceptions import RiskError, RiskNotFoundError
from app.risk.manager import RiskManager
from app.risk.repository import RiskRepository
from app.risk.types import RiskConfig, SymbolSpecification
from app.risk.validators import RiskRewardValidator


class TradePlanService:
    def __init__(
        self,
        manager: MT5ConnectionManager,
        settings: Settings,
        signals: SignalRepository,
        repository: RiskRepository,
    ) -> None:
        self._manager = manager
        self._settings = settings
        self._signals = signals
        self._repository = repository
        self._risk_manager = RiskManager()
        self._position = PositionSizeCalculator()
        self._stop = StopLossCalculator()
        self._target = TakeProfitCalculator()
        self._reward = RiskRewardValidator()

    async def get_settings(self) -> dict[str, Any]:
        return await self._repository.get_or_create_settings(self._defaults())

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_settings()
        merged = self._settings_values(current)
        merged.update(changes)
        config = self._config(merged)
        values = self._config_values(config)
        values["updated_at"] = datetime.now(timezone.utc)
        return await self._repository.update_settings(values)

    async def status(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        stored = await self.get_settings()
        config = self._config(stored)
        try:
            snapshot = await self._manager.risk_snapshot()
            account = self._account(snapshot)
            state = await self._daily_state(current, account)
            spread = self._spread(snapshot)
            reasons = self._risk_manager.validate_locks(
                account, state, config, spread, current
            )
            state = await self._repository.set_risk_lock(current.date(), reasons) or state
            return {
                "date": current.date(), "account_available": True,
                "demo_verified": True, "risk_locked": bool(reasons),
                "risk_lock_reasons": reasons, "state": state,
            }
        except (MT5Error, RiskError, ValueError) as error:
            return {
                "date": current.date(), "account_available": False,
                "demo_verified": False, "risk_locked": True,
                "risk_lock_reasons": [str(error)], "state": None,
            }

    async def create_trade_plan(
        self,
        signal_id: str,
        overrides: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        signal = await self._signals.get_by_id(signal_id)
        if signal is None:
            raise RiskNotFoundError("Signal was not found")
        current = now or datetime.now(timezone.utc)
        stored = await self.get_settings()
        override_values = dict(overrides or {})
        stop_reference = override_values.pop("stop_reference_price", None)
        target_price = override_values.pop("target_price", None)
        merged = self._settings_values(stored)
        merged.update(override_values)
        config = self._config(merged)
        signal_rejections = self._signal_rejections(signal)
        if signal_rejections:
            return await self._save_rejected(
                signal, config, current, signal_rejections
            )
        snapshot: dict[str, Any] | None = None
        try:
            snapshot = await self._manager.risk_snapshot(signal["symbol"])
            account = self._account(snapshot)
            spec = self._specification(snapshot)
            spread = self._spread(snapshot)
            state = await self._daily_state(current, account)
            lock_reasons = self._risk_manager.validate_locks(
                account, state, config, spread, current
            )
            await self._repository.set_risk_lock(current.date(), lock_reasons)
            if lock_reasons:
                return await self._save_rejected(
                    signal, config, current, lock_reasons, snapshot
                )
            entry = self._entry(snapshot, signal["direction"])
            stop = self._stop.calculate(
                signal["direction"], entry, signal["atr"], config, spec,
                stop_reference,
            )
            target = self._target.calculate(
                signal["direction"], entry, stop["stop_loss"], config, spec,
                target_price,
            )
            reward_reasons = self._reward.validate(
                signal["direction"], entry, stop["stop_loss"],
                target["take_profit"], config,
            )
            if reward_reasons:
                return await self._save_rejected(
                    signal, config, current, reward_reasons, snapshot,
                    entry=entry, stop=stop, target=target,
                )
            position = self._position.calculate(
                account["balance"], account["equity"],
                stop["stop_distance"], config, spec,
            )
            plan = self._approved_plan(
                signal, config, current, snapshot, account, spec,
                spread, entry, stop, target, position,
            )
            return await self._repository.save_trade_plan(plan)
        except (MT5Error, RiskError, ValueError, TypeError) as error:
            return await self._save_rejected(
                signal, config, current, [str(error)], snapshot
            )

    async def list_trade_plans(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self._repository.list_trade_plans(limit, offset)

    async def get_trade_plan(self, trade_plan_id: str) -> dict[str, Any]:
        plan = await self._repository.get_trade_plan(trade_plan_id)
        if plan is None:
            raise RiskNotFoundError("Trade plan was not found")
        return plan

    async def execution_context(self, trade_plan_id: str) -> dict[str, Any]:
        """Return persisted plan/signal plus a fresh fail-closed risk decision."""
        plan = await self.get_trade_plan(trade_plan_id)
        signal = await self._signals.get_by_id(str(plan["signal_id"]))
        if signal is None:
            raise RiskNotFoundError("Trade plan signal was not found")
        return {
            "plan": plan,
            "signal": signal,
            "risk": await self.status(),
            "settings": await self.get_settings(),
        }

    async def _daily_state(
        self, now: datetime, account: dict[str, float]
    ) -> dict[str, Any]:
        state = await self._repository.get_or_create_daily_state(
            now.date(), account["balance"], account["equity"]
        )
        return await self._repository.update_equity_state(
            now.date(), account["equity"]
        ) or state

    def _defaults(self) -> dict[str, Any]:
        values = {
            "risk_per_trade_percent": self._settings.risk_per_trade_percent,
            "max_daily_loss_percent": self._settings.risk_max_daily_loss_percent,
            "max_daily_drawdown_percent": self._settings.risk_max_daily_drawdown_percent,
            "max_consecutive_losses": self._settings.risk_max_consecutive_losses,
            "max_trades_per_day": self._settings.risk_max_trades_per_day,
            "max_open_positions": self._settings.risk_max_open_positions,
            "minimum_risk_reward": self._settings.risk_minimum_risk_reward,
            "target_risk_reward": self._settings.risk_target_risk_reward,
            "maximum_spread_points": self._settings.risk_maximum_spread_points,
            "cooldown_minutes_after_loss": self._settings.risk_cooldown_minutes_after_loss,
            "use_equity_for_risk": self._settings.risk_use_equity_for_risk,
            "break_even_enabled": self._settings.risk_break_even_enabled,
            "trailing_stop_enabled": self._settings.risk_trailing_stop_enabled,
            "stop_loss_method": self._settings.risk_stop_loss_method,
            "atr_multiplier": self._settings.risk_atr_multiplier,
            "session_enabled": self._settings.risk_session_enabled,
            "session_start_hour_utc": self._settings.risk_session_start_hour_utc,
            "session_end_hour_utc": self._settings.risk_session_end_hour_utc,
            "session_weekdays": self._settings.risk_session_weekdays,
            "updated_at": datetime.now(timezone.utc),
        }
        self._config(values)
        return values

    @staticmethod
    def _settings_values(values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in values.items()
            if key not in {"settings_id", "updated_at"}
        }

    @staticmethod
    def _config(values: dict[str, Any]) -> RiskConfig:
        prepared = TradePlanService._settings_values(values)
        prepared["session_weekdays"] = tuple(prepared["session_weekdays"])
        return RiskConfig(**prepared)

    @staticmethod
    def _config_values(config: RiskConfig) -> dict[str, Any]:
        return {
            "risk_per_trade_percent": float(config.risk_per_trade_percent),
            "max_daily_loss_percent": float(config.max_daily_loss_percent),
            "max_daily_drawdown_percent": float(config.max_daily_drawdown_percent),
            "max_consecutive_losses": config.max_consecutive_losses,
            "max_trades_per_day": config.max_trades_per_day,
            "max_open_positions": config.max_open_positions,
            "minimum_risk_reward": float(config.minimum_risk_reward),
            "target_risk_reward": float(config.target_risk_reward),
            "maximum_spread_points": float(config.maximum_spread_points),
            "cooldown_minutes_after_loss": config.cooldown_minutes_after_loss,
            "use_equity_for_risk": config.use_equity_for_risk,
            "break_even_enabled": config.break_even_enabled,
            "trailing_stop_enabled": config.trailing_stop_enabled,
            "stop_loss_method": config.stop_loss_method,
            "atr_multiplier": float(config.atr_multiplier),
            "session_enabled": config.session_enabled,
            "session_start_hour_utc": config.session_start_hour_utc,
            "session_end_hour_utc": config.session_end_hour_utc,
            "session_weekdays": list(config.session_weekdays),
        }

    @staticmethod
    def _signal_rejections(signal: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if signal["direction"] not in {"BUY", "SELL"}:
            reasons.append("Signal direction must be BUY or SELL")
        if signal["status"] != "CANDIDATE":
            reasons.append("Signal status must be CANDIDATE")
        return reasons

    @staticmethod
    def _account(snapshot: dict[str, Any]) -> dict[str, float]:
        account = snapshot["account"]
        balance = float(account["balance"])
        equity = float(account["equity"])
        if not all(math.isfinite(value) and value > 0 for value in (balance, equity)):
            raise ValueError("Account balance or equity is invalid")
        return {"balance": balance, "equity": equity}

    @staticmethod
    def _specification(snapshot: dict[str, Any]) -> SymbolSpecification:
        symbol = snapshot["symbol"]
        return SymbolSpecification(
            digits=int(symbol["digits"]), point=symbol["point"],
            trade_tick_size=symbol["trade_tick_size"],
            trade_tick_value=symbol["trade_tick_value"],
            volume_min=symbol["volume_min"], volume_max=symbol["volume_max"],
            volume_step=symbol["volume_step"],
            trade_stops_level=symbol["trade_stops_level"] or 0,
            trade_freeze_level=symbol["trade_freeze_level"] or 0,
            contract_size=symbol.get("trade_contract_size"),
        )

    @staticmethod
    def _spread(snapshot: dict[str, Any]) -> float:
        point = Decimal(str(snapshot["symbol"]["point"]))
        bid = Decimal(str(snapshot["tick"]["bid"]))
        ask = Decimal(str(snapshot["tick"]["ask"]))
        if point <= 0 or bid <= 0 or ask < bid:
            raise ValueError("Tick or point specification is invalid")
        return float((ask - bid) / point)

    @staticmethod
    def _entry(snapshot: dict[str, Any], direction: str) -> float:
        field = "ask" if direction == "BUY" else "bid"
        entry = float(snapshot["tick"][field])
        if not math.isfinite(entry) or entry <= 0:
            raise ValueError("Entry price is invalid")
        return entry

    @staticmethod
    def _approved_plan(
        signal: dict[str, Any], config: RiskConfig, now: datetime,
        snapshot: dict[str, Any], account: dict[str, float],
        spec: SymbolSpecification, spread: float, entry: float,
        stop: dict[str, Any], target: dict[str, Any], position: dict[str, Any],
    ) -> dict[str, Any]:
        details = {
            "formula": {
                "risk_amount": "risk_base * risk_percent / 100",
                "risk_per_lot": "(stop_distance / tick_size) * tick_value",
                "raw_lot": "risk_amount / risk_per_lot",
                "normalized_lot": "floor(min(raw_lot, volume_max) / volume_step) * volume_step",
            },
            "position_size": position["calculation_details"],
            "stop_loss": stop["calculation_details"],
            "take_profit": target["calculation_details"],
            "symbol_specification": {
                "digits": spec.digits, "point": float(spec.point),
                "trade_tick_size": float(spec.trade_tick_size),
                "trade_tick_value": float(spec.trade_tick_value),
                "volume_min": float(spec.volume_min),
                "volume_max": float(spec.volume_max),
                "volume_step": float(spec.volume_step),
                "trade_stops_level": float(spec.trade_stops_level),
                "trade_freeze_level": float(spec.trade_freeze_level),
                "contract_size": float(spec.contract_size) if spec.contract_size else None,
            },
            "source": "MT5 demo read-only snapshot",
        }
        return {
            "trade_plan_id": str(uuid4()), "signal_id": signal["signal_id"],
            "symbol": snapshot["symbol"]["name"], "direction": signal["direction"],
            "entry_price": entry, "stop_loss": stop["stop_loss"],
            "take_profit": target["take_profit"],
            "stop_distance_price": stop["stop_distance"],
            "stop_distance_points": stop["stop_distance"] / float(spec.point),
            "risk_percent": float(config.risk_per_trade_percent),
            "risk_amount": position["risk_amount"],
            "position_size_lots": position["lot_size"],
            "risk_reward": target["risk_reward"], "spread_points": spread,
            "balance": account["balance"], "equity": account["equity"],
            "calculation_details": details,
            "validation_reasons": [
                "Demo account verified", "Candidate signal accepted",
                "Risk locks passed", "Broker symbol specification validated",
                "Stop loss, take profit, risk-reward, and lot size validated",
            ],
            "rejection_reasons": [], "status": "APPROVED", "created_at": now,
        }

    async def _save_rejected(
        self,
        signal: dict[str, Any],
        config: RiskConfig,
        now: datetime,
        reasons: list[str],
        snapshot: dict[str, Any] | None = None,
        *,
        entry: float = 0.0,
        stop: dict[str, Any] | None = None,
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        balance = equity = spread = 0.0
        symbol = signal["symbol"]
        if snapshot is not None:
            symbol = str(snapshot["symbol"]["name"])
            try:
                account = self._account(snapshot)
                balance, equity = account["balance"], account["equity"]
                spread = self._spread(snapshot)
                if entry == 0:
                    entry = self._entry(snapshot, signal["direction"])
            except (ValueError, TypeError, KeyError):
                pass
        stop_loss = float(stop["stop_loss"]) if stop else 0.0
        stop_distance = float(stop["stop_distance"]) if stop else 0.0
        take_profit = float(target["take_profit"]) if target else 0.0
        risk_reward = float(target["risk_reward"]) if target else 0.0
        plan = {
            "trade_plan_id": str(uuid4()), "signal_id": signal["signal_id"],
            "symbol": symbol, "direction": signal.get("direction", "HOLD"),
            "entry_price": entry, "stop_loss": stop_loss,
            "take_profit": take_profit,
            "stop_distance_price": stop_distance,
            "stop_distance_points": 0.0,
            "risk_percent": float(config.risk_per_trade_percent),
            "risk_amount": 0.0, "position_size_lots": 0.0,
            "risk_reward": risk_reward, "spread_points": spread,
            "balance": balance, "equity": equity,
            "calculation_details": {
                "formula": "Calculation stopped because a safety validation failed",
                "source": "MT5 demo read-only snapshot" if snapshot else "signal validation",
            },
            "validation_reasons": [],
            "rejection_reasons": list(dict.fromkeys(reasons)),
            "status": "REJECTED", "created_at": now,
        }
        return await self._repository.save_trade_plan(plan)
