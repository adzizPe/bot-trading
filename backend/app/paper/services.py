from datetime import datetime, timezone

from typing import Any


from app.config.settings import Settings

from app.paper.exceptions import PaperConflictError, PaperNotFoundError

from app.paper.repository import PaperRepository

from app.paper.types import PaperConfig



class PaperAccountService:

    def __init__(self, repository: PaperRepository, settings: Settings) -> None:

        self._repository = repository

        self._settings = settings


    async def get_settings(self) -> dict[str, Any]:

        return await self._repository.get_or_create_settings(self._defaults())


    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:

        current = await self.get_settings()

        values = {k: v for k, v in current.items() if k not in {"settings_id", "updated_at"}}

        values.update(changes)

        config = PaperConfig(**values)

        stored = self._config_values(config)

        stored["updated_at"] = datetime.now(timezone.utc)

        return await self._repository.update_settings(stored)


    async def config(self) -> PaperConfig:

        values = await self.get_settings()

        return PaperConfig(**{

            key: value for key, value in values.items()

            if key not in {"settings_id", "updated_at"}

        })


    async def account(self) -> dict[str, Any]:

        config = await self.config()

        return await self._repository.get_or_create_account(float(config.initial_balance))


    async def reset(self) -> dict[str, Any]:

        config = await self.config()

        return await self._repository.reset_account(float(config.initial_balance))


    def _defaults(self) -> dict[str, Any]:

        config = PaperConfig(

            initial_balance=self._settings.paper_initial_balance,

            slippage_points=self._settings.paper_slippage_points,

            commission_per_lot=self._settings.paper_commission_per_lot,

            swap_long_per_lot=self._settings.paper_swap_long_per_lot,

            swap_short_per_lot=self._settings.paper_swap_short_per_lot,

            update_interval_seconds=self._settings.paper_update_interval_seconds,

            auto_trade_enabled=self._settings.paper_auto_trade_enabled,

            maximum_open_positions=self._settings.paper_maximum_open_positions,

            allow_manual_trade_plan=self._settings.paper_allow_manual_trade_plan,

            close_positions_on_stop=self._settings.paper_close_positions_on_stop,

            emergency_close_positions=self._settings.paper_emergency_close_positions,

            break_even_enabled=self._settings.paper_break_even_enabled,

            break_even_trigger_r=self._settings.paper_break_even_trigger_r,

            trailing_stop_enabled=self._settings.paper_trailing_stop_enabled,

            trailing_stop_method=self._settings.paper_trailing_stop_method,

            trailing_distance_points=self._settings.paper_trailing_distance_points,

            trailing_atr_multiplier=self._settings.paper_trailing_atr_multiplier,
        )

        values = self._config_values(config)

        values["updated_at"] = datetime.now(timezone.utc)

        return values


    @staticmethod

    def _config_values(config: PaperConfig) -> dict[str, Any]:

        return {

            "initial_balance": float(config.initial_balance),

            "slippage_points": float(config.slippage_points),

            "commission_per_lot": float(config.commission_per_lot),

            "swap_long_per_lot": float(config.swap_long_per_lot),

            "swap_short_per_lot": float(config.swap_short_per_lot),

            "update_interval_seconds": float(config.update_interval_seconds),

            "auto_trade_enabled": config.auto_trade_enabled,

            "maximum_open_positions": config.maximum_open_positions,

            "allow_manual_trade_plan": config.allow_manual_trade_plan,

            "close_positions_on_stop": config.close_positions_on_stop,

            "emergency_close_positions": config.emergency_close_positions,

            "break_even_enabled": config.break_even_enabled,

            "break_even_trigger_r": float(config.break_even_trigger_r),

            "trailing_stop_enabled": config.trailing_stop_enabled,

            "trailing_stop_method": config.trailing_stop_method,

            "trailing_distance_points": float(config.trailing_distance_points),

            "trailing_atr_multiplier": float(config.trailing_atr_multiplier),

        }



class PaperOrderService:

    def __init__(self, repository: PaperRepository) -> None:

        self._repository = repository


    async def approved_plan(self, trade_plan_id: str) -> dict[str, Any]:

        plan = await self._repository.get_trade_plan(trade_plan_id)

        if plan is None:

            raise PaperNotFoundError("Trade plan was not found")

        if plan["status"] != "APPROVED":

            raise PaperConflictError("Only APPROVED trade plans can be used")

        if plan["direction"] not in {"BUY", "SELL"}:

            raise PaperConflictError("Trade plan direction must be BUY or SELL")

        if await self._repository.plan_or_signal_used(

            plan["trade_plan_id"], plan["signal_id"]

        ):

            raise PaperConflictError("Trade plan or signal was already used")
        return plan


    async def open_count(self) -> int:

        return await self._repository.count_open_positions()


    async def persist(

        self, plan: dict[str, Any], fill: dict[str, Any], spread: float

    ) -> dict[str, Any]:

        return await self._repository.open_position(plan, fill, spread)



class PaperPositionService:

    def __init__(self, repository: PaperRepository) -> None:

        self._repository = repository


    async def get(self, position_id: str) -> dict[str, Any]:

        position = await self._repository.get_position(position_id)

        if position is None:

            raise PaperNotFoundError("Paper position was not found")
        return position


    async def list_positions(

        self, status: str | None = None, limit: int = 100

    ) -> list[dict[str, Any]]:

        return await self._repository.list_positions(status, limit)


    async def trades(self, limit: int = 200) -> list[dict[str, Any]]:

        return await self._repository.list_trades(limit)


    async def equity_curve(self, limit: int = 1000) -> list[dict[str, Any]]:

        return await self._repository.equity_curve(limit)



class PaperTradingStatisticsService:
    def __init__(

        self, repository: PaperRepository, account_service: PaperAccountService

    ) -> None:

        self._repository = repository

        self._accounts = account_service


    async def statistics(self) -> dict[str, Any]:

        trades = list(reversed(await self._repository.list_trades(100_000)))

        account = await self._accounts.account()

        values = [float(trade["net_profit_loss"]) for trade in trades]

        wins = [value for value in values if value > 0]

        losses = [value for value in values if value < 0]

        gross_profit = sum(wins)

        gross_loss = abs(sum(losses))

        consecutive_wins = consecutive_losses = current_wins = current_losses = 0

        for value in values:

            if value > 0:

                current_wins += 1

                current_losses = 0

            elif value < 0:

                current_losses += 1

                current_wins = 0

            consecutive_wins = max(consecutive_wins, current_wins)

            consecutive_losses = max(consecutive_losses, current_losses)

        curve = await self._repository.equity_curve(100_000)

        maximum_drawdown = max(

            (float(item["drawdown"]) for item in curve), default=0.0
        )

        count = len(values)

        return {

            "total_trades": count, "winning_trades": len(wins),

            "losing_trades": len(losses),

            "win_rate": (len(wins) / count * 100) if count else 0.0,

            "gross_profit": gross_profit, "gross_loss": gross_loss,

            "net_profit": sum(values),

            "profit_factor": (gross_profit / gross_loss) if gross_loss else 0.0,

            "average_win": (gross_profit / len(wins)) if wins else 0.0,

            "average_loss": (sum(losses) / len(losses)) if losses else 0.0,

            "expectancy": (sum(values) / count) if count else 0.0,

            "maximum_drawdown": maximum_drawdown,

            "consecutive_wins": consecutive_wins,

            "consecutive_losses": consecutive_losses,

            "current_balance": account["balance"],

            "current_equity": account["equity"],

        }

