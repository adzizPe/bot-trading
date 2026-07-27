from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.auth.dependencies import require_permission
from app.auth.permissions import Permission
from app.schemas.analysis import SignalResponse
from app.testing_mode.service import SyntheticSignalService

router = APIRouter(prefix="/testing", tags=["development-testing"], dependencies=[
    Depends(require_permission(Permission.ANALYSIS_GENERATE))
])


class SyntheticSignalRequest(BaseModel):
    direction: Literal["BUY", "SELL"]
    model_config = ConfigDict(extra="forbid")


def get_testing_signal_service(request: Request) -> SyntheticSignalService:
    return request.app.state.testing_signal_service


@router.post(
    "/signals", response_model=SignalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_synthetic_signal(
    payload: SyntheticSignalRequest,
    response: Response,
    service: Annotated[SyntheticSignalService, Depends(get_testing_signal_service)],
) -> SignalResponse:
    response.headers["Cache-Control"] = "no-store"
    return SignalResponse(**await service.create(payload.direction))
