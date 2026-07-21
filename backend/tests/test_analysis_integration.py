import pytest

from app.analysis.candle_confirmation import CandleConfirmationDetector
from app.analysis.engine import StrategyEngine
from app.analysis.indicators import IndicatorService
from app.analysis.market_structure import MarketStructureDetector
from app.analysis.repository import SignalRepository
from app.analysis.scoring import SignalScoringService
from app.analysis.service import AnalysisService
from app.analysis.support_resistance import SupportResistanceDetector
from app.analysis.validator import SignalValidator
from app.config.settings import get_settings
from app.database.session import SessionFactory
from app.market_data.service import MarketDataService
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multi_timeframe_analysis_with_demo_account() -> None:
    settings = get_settings()
    if not (settings.mt5_login and settings.mt5_password and settings.mt5_server):
        pytest.skip("MT5 demo credentials are not configured")
    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    market_data = MarketDataService(manager, settings)
    service = AnalysisService(
        market_data,
        settings,
        SignalRepository(SessionFactory),
        IndicatorService(MarketStructureDetector(), SupportResistanceDetector()),
        CandleConfirmationDetector(),
        SignalValidator(),
        StrategyEngine(SignalScoringService()),
    )
    try:
        status = await manager.connect()
        assert status["demo_verified"] is True
        result = await service.get_multi_timeframe(None)
        assert result["trend"]["timeframe"] == "H1"
        assert result["setup"]["timeframe"] == "M15"
        assert result["confirmation"]["timeframe"] == "M5"
        assert all(
            result[name]["data_valid"]
            for name in ("trend", "setup", "confirmation")
        )
    finally:
        await manager.disconnect()


@pytest.mark.integration
def test_analysis_api_with_demo_account() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = get_settings()
    if not (settings.mt5_login and settings.mt5_password and settings.mt5_server):
        pytest.skip("MT5 demo credentials are not configured")
    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    with TestClient(create_app(settings, manager)) as api:
        connected = api.post("/api/v1/mt5/connect")
        assert connected.status_code == 200
        assert connected.json()["demo_verified"] is True
        indicators = api.get("/api/v1/analysis/indicators?timeframe=M15")
        assert indicators.status_code == 200
        assert indicators.json()["data_valid"] is True
        multi = api.get("/api/v1/analysis/multi-timeframe")
        assert multi.status_code == 200
        generated = api.post("/api/v1/analysis/signal", json={})
        assert generated.status_code == 200
        assert generated.json()["direction"] in {"BUY", "SELL", "HOLD"}
        assert api.get("/api/v1/analysis/latest-signal").status_code == 200
        assert "/api/v1/analysis/signal" in api.get("/openapi.json").json()["paths"]
        assert api.post("/api/v1/mt5/disconnect").status_code == 200
