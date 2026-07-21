from datetime import datetime, timezone
from typing import Any

from app.paper.exceptions import PaperStateError
from app.paper.manager import PaperTradeManager
from app.paper.repository import PaperRepository
from app.paper.scheduler import PaperTradingScheduler
from app.paper.services import PaperAccountService
from app.paper.types import CloseReason, EngineStatus


class PaperTradingStateManager:
    def __init__(self, repository: PaperRepository) -> None:
        self._repository = repository

    async def initialize(self) -> dict[str, Any]:
        state = await self._repository.get_or_create_engine_state()
        if state["status"] in {
            EngineStatus.STARTING.value,
            EngineStatus.RUNNING.value,
            EngineStatus.RISK_LOCKED.value,
        }:
            return await self.set(EngineStatus.STOPPED)
        return state

    async def get(self) -> dict[str, Any]:
        return await self._repository.get_or_create_engine_state()

    async def set(
        self, status: EngineStatus, *, error: str | None = None,
        started_at: datetime | None = None, cycle_at: datetime | None = None,
    ) -> dict[str, Any]:
        return await self._repository.set_engine_state(
            status.value, error=error, started_at=started_at, cycle_at=cycle_at
        )


class PaperTradingEngine:
    def __init__(
        self,
        state: PaperTradingStateManager,
        accounts: PaperAccountService,
        trade_manager: PaperTradeManager,
    ) -> None:
        self._state = state
        self._accounts = accounts
        self._trades = trade_manager
        self._interval = 1.0
        self._initialized = False
        self._scheduler = PaperTradingScheduler(
            self._run_scheduled_cycle, lambda: self._interval
        )

    @property
    def trade_manager(self) -> PaperTradeManager:
        return self._trades

    async def status(self) -> dict[str, Any]:
        await self._ensure_initialized()
        state = await self._state.get()
        return {**state, "scheduler_running": self._scheduler.running}

    async def start(self) -> dict[str, Any]:
        await self._ensure_initialized()
        state = await self._state.get()
        if state["status"] == EngineStatus.RUNNING.value and self._scheduler.running:
            return await self.status()
        await self._accounts.account()
        config = await self._accounts.config()
        self._interval = float(config.update_interval_seconds)
        await self._state.set(EngineStatus.STARTING)
        now = datetime.now(timezone.utc)
        await self._state.set(EngineStatus.RUNNING, started_at=now)
        self._scheduler.start()
        return await self.status()

    async def pause(self) -> dict[str, Any]:
        await self._ensure_initialized()
        await self._scheduler.stop()
        await self._state.set(EngineStatus.PAUSED)
        return await self.status()

    async def stop(self) -> dict[str, Any]:
        await self._ensure_initialized()
        await self._scheduler.stop()
        config = await self._accounts.config()
        if config.close_positions_on_stop:
            await self._trades.close_all(CloseReason.MANUAL.value)
        await self._state.set(EngineStatus.STOPPED)
        return await self.status()

    async def emergency_stop(self) -> dict[str, Any]:
        await self._ensure_initialized()
        await self._scheduler.stop()
        config = await self._accounts.config()
        if config.emergency_close_positions:
            await self._trades.close_all(CloseReason.EMERGENCY_STOP.value)
        await self._state.set(EngineStatus.EMERGENCY_STOPPED)
        return await self.status()

    async def open(self, trade_plan_id: str) -> dict[str, Any]:
        state = await self.status()
        return await self._trades.open_from_plan(
            trade_plan_id, state["status"], manual=True
        )

    async def close(self, position_id: str) -> dict[str, Any]:
        return await self._trades.close_position(
            position_id, CloseReason.MANUAL.value
        )

    async def run_once(self) -> dict[str, Any]:
        state = await self.status()
        if state["status"] not in {
            EngineStatus.RUNNING.value, EngineStatus.RISK_LOCKED.value,
        }:
            raise PaperStateError("Paper engine must be RUNNING")
        result = await self._trades.run_cycle(state["status"])
        next_status = (
            EngineStatus.RISK_LOCKED if result["risk_locked"]
            else EngineStatus.RUNNING
        )
        await self._state.set(
            next_status, cycle_at=datetime.now(timezone.utc)
        )
        return result

    async def shutdown(self) -> None:
        await self._scheduler.stop()
        if self._initialized:
            await self._state.set(EngineStatus.STOPPED)

    async def _run_scheduled_cycle(self) -> None:
        try:
            await self.run_once()
        except Exception as error:
            await self._state.set(EngineStatus.ERROR, error=str(error))
            raise

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self._state.initialize()
            self._initialized = True
