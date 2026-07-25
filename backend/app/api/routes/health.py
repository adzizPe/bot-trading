from fastapi import APIRouter, Depends, Request
from app.auth.dependencies import require_permission
from app.auth.permissions import Permission
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    return HealthResponse(
        status="healthy",
        service=request.app.state.settings.app_name,
        version=request.app.version,
    )


@router.get("/health/full", dependencies=[Depends(
    require_permission(Permission.READ_DASHBOARD)
)])
async def full_health(request: Request) -> dict[str, object]:
    manager_status = request.app.state.mt5_manager.status()
    connected = bool(manager_status.get("connected"))
    subsystem_status = {
        "market": {"status": "HEALTHY" if connected else "DEGRADED"},
        "risk": {"status": "HEALTHY"},
        "paper": {"status": "HEALTHY"},
        "backtest": {"status": "HEALTHY"},
        "frontend": {"status": "HEALTHY", "build": "vite"},
    }
    return request.app.state.health_monitor.full(subsystem_status)
