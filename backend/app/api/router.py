from fastapi import APIRouter

from app.api.routes.analysis import router as analysis_router
from app.api.routes.auth import router as auth_router
from app.api.routes.backtest import router as backtest_router
from app.api.routes.demo import router as demo_router
from app.api.routes.health import router as health_router
from app.api.routes.market import router as market_router
from app.api.routes.mt5 import router as mt5_router
from app.api.routes.paper import router as paper_router
from app.api.routes.readiness import router as readiness_router
from app.api.routes.risk import router as risk_router
from app.api.routes.safety import router as safety_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(readiness_router)
api_router.include_router(auth_router)
api_router.include_router(mt5_router)
api_router.include_router(market_router)
api_router.include_router(analysis_router)
api_router.include_router(risk_router)
api_router.include_router(paper_router)
api_router.include_router(backtest_router)
api_router.include_router(demo_router)
api_router.include_router(safety_router)
