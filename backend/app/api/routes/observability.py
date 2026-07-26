from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import require_permission
from app.auth.permissions import Permission
from app.observability.models import AlertRecord, SystemMetricsSnapshot

router = APIRouter(tags=["observability"])
protected = APIRouter(
    prefix="/monitoring",
    dependencies=[Depends(require_permission(Permission.READ_DASHBOARD))],
)


class LivenessResponse(BaseModel):
    status: str
    service: str
    version: str
    observed_at: datetime
    model_config = ConfigDict(extra="forbid", frozen=True)


@router.get(
    "/health/liveness",
    response_model=LivenessResponse,
    dependencies=[Depends(require_permission(Permission.READ_DASHBOARD))],
)
async def liveness(request: Request, response: Response) -> LivenessResponse:
    response.headers["Cache-Control"] = "no-store"
    return LivenessResponse(
        status="ALIVE",
        service="backend",
        version=request.app.version,
        observed_at=datetime.now(timezone.utc),
    )


@protected.get("/metrics", response_model=SystemMetricsSnapshot)
async def system_metrics(request: Request, response: Response) -> SystemMetricsSnapshot:
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.observability_service.metrics()


@protected.get("/metrics/prometheus", response_class=PlainTextResponse)
async def prometheus_metrics(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        await request.app.state.observability_service.prometheus(),
        media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store"},
    )


@protected.get("/alerts", response_model=list[AlertRecord])
async def monitoring_alerts(request: Request, response: Response) -> list[AlertRecord]:
    response.headers["Cache-Control"] = "no-store"
    return list(await request.app.state.observability_service.alerts())


router.include_router(protected)
