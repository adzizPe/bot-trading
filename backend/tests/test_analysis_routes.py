from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def indicator(timeframe: str) -> dict[str, Any]:
    return {
        "symbol": "XAUUSD",
        "timeframe": timeframe,
        "candle_time": NOW,
        "ema_fast": 3001.0,
        "ema_slow": 3000.0,
        "rsi": 50.0,
        "atr": 2.0,
        "market_structure": "BULLISH",
        "support_levels": [2990.0],
        "resistance_levels": [3010.0],
        "data_valid": True,
    }


def signal() -> dict[str, Any]:
    return {
        "signal_id": "test-signal",
        "symbol": "XAUUSD",
        "direction": "BUY",
        "strategy_name": "EMA_RSI_ATR_MTF_V1",
        "trend_timeframe": "H1",
        "setup_timeframe": "M15",
        "confirmation_timeframe": "M5",
        "timeframe": "H1/M15/M5",
        "entry_reference_price": 3000.0,
        "atr": 2.0,
        "confidence_score": 100.0,
        "score_factors": [],
        "reasons": ["Rules passed"],
        "rejection_reasons": [],
        "candle_time": NOW,
        "created_at": NOW,
        "status": "CANDIDATE",
    }

class FakeAnalysisService:
    async def get_indicators(
        self, symbol: str | None, timeframe: str, count: int | None = None
    ) -> dict[str, Any]:
        return indicator(timeframe)

    async def get_multi_timeframe(
        self, symbol: str | None, overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "symbol": "XAUUSD",
            "cutoff": NOW,
            "trend": indicator("H1"),
            "setup": indicator("M15"),
            "confirmation": indicator("M5"),
            "confirmation_candle": "BULLISH",
        }

    async def generate_signal(
        self,
        symbol: str | None,
        strategy: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return signal()

    async def latest_signal(self, symbol: str | None = None) -> dict[str, Any]:
        return signal()


def test_analysis_endpoints_and_openapi_contract() -> None:
    settings = make_settings()
    manager = MT5ConnectionManager(FakeMT5Client(), settings)
    app = create_app(settings, manager, FakeAnalysisService())  # type: ignore[arg-type]
    with TestClient(app) as api:
        assert api.get("/api/v1/analysis/indicators?timeframe=M15").status_code == 200
        assert api.get("/api/v1/analysis/multi-timeframe").status_code == 200
        generated = api.post("/api/v1/analysis/signal", json={})
        assert generated.status_code == 200
        assert generated.json()["direction"] == "BUY"
        assert api.get("/api/v1/analysis/latest-signal").status_code == 200
        paths = api.get("/openapi.json").json()["paths"]
        assert "/api/v1/analysis/signal" in paths
