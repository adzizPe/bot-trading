from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_mt5_manager
from app.mt5.exceptions import (
    MT5ConfigurationError,
    MT5Error,
    MT5RealAccountRejected,
    MT5SymbolNotFound,
)
from app.mt5.manager import MT5ConnectionManager
from app.schemas.mt5 import (
    MT5AccountResponse,
    MT5StatusResponse,
    MT5SymbolResponse,
    MT5TerminalResponse,
)

router = APIRouter(prefix="/mt5", tags=["mt5"])
ManagerDependency = Annotated[MT5ConnectionManager, Depends(get_mt5_manager)]


def _http_error(error: MT5Error) -> HTTPException:
    if isinstance(error, MT5ConfigurationError):
        code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, MT5RealAccountRejected):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(error, MT5SymbolNotFound):
        code = status.HTTP_404_NOT_FOUND
    else:
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(status_code=code, detail=str(error))


async def _run(operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    try:
        return await operation()
    except MT5Error as error:
        raise _http_error(error) from error


@router.get("/status", response_model=MT5StatusResponse)
async def mt5_status(manager: ManagerDependency) -> dict[str, Any]:
    return manager.status()


@router.post("/connect", response_model=MT5StatusResponse)
async def mt5_connect(manager: ManagerDependency) -> dict[str, Any]:
    return await _run(manager.connect)


@router.post("/disconnect", response_model=MT5StatusResponse)
async def mt5_disconnect(manager: ManagerDependency) -> dict[str, Any]:
    return await _run(manager.disconnect)


@router.get("/account", response_model=MT5AccountResponse)
async def mt5_account(manager: ManagerDependency) -> dict[str, Any]:
    return await _run(manager.account_info)


@router.get("/terminal", response_model=MT5TerminalResponse)
async def mt5_terminal(manager: ManagerDependency) -> dict[str, Any]:
    return await _run(manager.terminal_info)


@router.get("/symbol", response_model=MT5SymbolResponse)
async def mt5_symbol(manager: ManagerDependency) -> dict[str, Any]:
    return await _run(manager.symbol_info)
