from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.operations.readiness import BackendReadinessResponse, ReadinessStatus

router = APIRouter(tags=["operational-readiness"])


@router.get(
    "/health/readiness",
    response_model=BackendReadinessResponse,
    responses={429: {"description": "Rate limited"}, 503: {"description": "Not ready"}},
)
async def backend_readiness(request: Request) -> JSONResponse:
    if not await request.app.state.readiness_rate_limiter.allow():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Readiness request rate exceeded",
            headers={"Cache-Control": "no-store"},
        )
    lease = request.app.state.database_runtime_lease
    response = await request.app.state.readiness_evaluator.evaluate(
        observations=request.app.state.readiness_observations,
        runtime_lease_acquired=lease.is_acquired,
        database_probe=request.app.state.readiness_database_probe,
        version=request.app.version,
        expected_release_id=request.app.state.expected_release_id,
    )
    response_status = (
        status.HTTP_200_OK
        if response.status is ReadinessStatus.READY
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(
        status_code=response_status,
        content=response.model_dump(mode="json"),
        headers={
            "Cache-Control": "no-store",
            "X-Backend-Readiness": "authoritative",
        },
    )
