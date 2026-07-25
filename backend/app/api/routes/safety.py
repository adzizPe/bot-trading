from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import (
    get_safety_manager,
    get_safety_repository,
)
from app.auth.dependencies import require_permission
from app.auth.permissions import Permission
from app.safety.manager import SafetyManager
from app.safety.repository import SafetyRepository
from app.schemas.safety import SafetyEmergencyRequest, SafetyResetRequest

router = APIRouter(prefix="/safety", tags=["safety"], dependencies=[
    Depends(require_permission(Permission.READ_DASHBOARD))
])
Manager = Annotated[SafetyManager, Depends(get_safety_manager)]
Repository = Annotated[SafetyRepository, Depends(get_safety_repository)]


@router.get("/status")
async def safety_status(manager: Manager) -> dict[str, Any]:
    return manager.status()


@router.post("/emergency-stop", dependencies=[Depends(
    require_permission(Permission.EMERGENCY_STOP)
)])
async def safety_emergency_stop(
    manager: Manager, payload: SafetyEmergencyRequest,
) -> dict[str, Any]:
    await manager.emergency.activate(payload.reason)
    return manager.status()


@router.post("/emergency-reset", dependencies=[Depends(
    require_permission(Permission.SAFETY_RESET)
)])
async def safety_emergency_reset(
    manager: Manager, _payload: SafetyResetRequest,
) -> dict[str, Any]:
    await manager.emergency.reset()
    return manager.status()


@router.post("/circuit-reset", dependencies=[Depends(
    require_permission(Permission.SAFETY_RESET)
)])
async def safety_circuit_reset(
    manager: Manager, _payload: SafetyResetRequest,
) -> dict[str, Any]:
    await manager.reset_circuit()
    return manager.status()["circuit_breaker"]


@router.get("/events")
async def safety_events(
    repository: Repository,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return await repository.list_events(limit)
