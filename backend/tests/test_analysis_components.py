from datetime import datetime, timezone

import pytest

from app.analysis.candle_confirmation import CandleConfirmationDetector
from app.analysis.engine import StrategyEngine
from app.analysis.exceptions import InsufficientDataError, InvalidCandleError
from app.analysis.indicators import ATRIndicator, EMAIndicator, RSIIndicator
from app.analysis.market_structure import MarketStructureDetector
from app.analysis.scoring import SignalScoringService
from app.analysis.validator import SignalValidator
from tests.fakes import FakeMT5Client

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def candle(
    open_price: float, high: float, low: float, close: float, closed: bool = True
) -> dict[str, object]:
    return {
        "timestamp": NOW,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": 1,
        "spread": 1,
        "real_volume": 0,
        "is_closed": closed,
    }


def test_ema_known_vector() -> None:
    assert EMAIndicator.calculate([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_rsi_known_wilder_vector() -> None:
    closes = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
    ]
    assert RSIIndicator.calculate(closes, 14)[-1] == pytest.approx(70.4641, abs=0.001)


def test_atr_known_vector() -> None:
    candles = [candle(9, 10, 8, 9), candle(10, 12, 9, 11), candle(11, 14, 10, 13)]
    assert ATRIndicator.calculate(candles, 3)[-1] == pytest.approx(3.0)


def test_indicator_insufficient_data() -> None:
    with pytest.raises(InsufficientDataError):
        EMAIndicator.calculate([1, 2], 3)


def test_unclosed_candle_is_rejected() -> None:
    with pytest.raises(InvalidCandleError):
        SignalValidator().validate_candles([candle(1, 2, 1, 2, False)], "M5", 1)

def structure_candles(highs: list[float], lows: list[float]) -> list[dict[str, object]]:
    return [candle(low, high, low, high) for high, low in zip(highs, lows, strict=True)]


def test_bullish_market_structure() -> None:
    data = structure_candles([10, 11, 12, 13], [5, 6, 7, 8])
    assert MarketStructureDetector().detect(data, 4)["structure"] == "BULLISH"


def test_bearish_market_structure() -> None:
    data = structure_candles([13, 12, 11, 10], [8, 7, 6, 5])
    assert MarketStructureDetector().detect(data, 4)["structure"] == "BEARISH"


def test_neutral_market_structure() -> None:
    data = structure_candles([10, 12, 11, 12], [5, 4, 5, 4])
    assert MarketStructureDetector().detect(data, 4)["structure"] == "NEUTRAL"


def test_bullish_confirmation_candle() -> None:
    result = CandleConfirmationDetector().detect(candle(10, 12, 9, 11.8), 2, 0.2, 0.65)
    assert result == "BULLISH"


def test_bearish_confirmation_candle() -> None:
    result = CandleConfirmationDetector().detect(candle(11.8, 12, 9, 9.2), 2, 0.2, 0.65)
    assert result == "BEARISH"


class Config:
    max_spread_points = 100.0
    rsi_overbought = 70.0
    rsi_oversold = 30.0
    strategy_name = "EMA_RSI_ATR_MTF_V1"


def indicator(fast: float, slow: float, structure: str, rsi: float = 50) -> dict[str, object]:
    return {
        "ema_fast": fast,
        "ema_slow": slow,
        "ema_fast_previous": fast - 0.1,
        "ema_slow_previous": slow,
        "rsi": rsi,
        "atr": 2.0,
        "close": 3000.0,
        "candle_time": NOW,
        "market_structure": structure,
        "data_valid": True,
    }

def test_valid_buy_signal() -> None:
    indicators = {
        "H1": indicator(3, 2, "BULLISH"),
        "M15": indicator(3, 2, "BULLISH"),
        "M5": indicator(3, 2, "BULLISH"),
    }
    signal = StrategyEngine(SignalScoringService()).analyze(
        "XAUUSD", indicators, "BULLISH", 20, Config()
    )
    assert signal["direction"] == "BUY"
    assert signal["status"] == "CANDIDATE"


def test_valid_sell_signal() -> None:
    indicators = {
        "H1": indicator(2, 3, "BEARISH"),
        "M15": indicator(2, 3, "BEARISH"),
        "M5": indicator(2, 3, "BEARISH"),
    }
    signal = StrategyEngine(SignalScoringService()).analyze(
        "XAUUSD", indicators, "BEARISH", 20, Config()
    )
    assert signal["direction"] == "SELL"
    assert signal["status"] == "CANDIDATE"


def test_hold_when_conditions_are_incomplete() -> None:
    indicators = {
        "H1": indicator(3, 2, "NEUTRAL"),
        "M15": indicator(2, 3, "NEUTRAL"),
        "M5": indicator(2, 3, "NEUTRAL"),
    }
    signal = StrategyEngine(SignalScoringService()).analyze(
        "XAUUSD", indicators, "NONE", 20, Config()
    )
    assert signal["direction"] == "HOLD"


def test_spread_too_high_prevents_candidate() -> None:
    indicators = {
        "H1": indicator(3, 2, "BULLISH"),
        "M15": indicator(3, 2, "BULLISH"),
        "M5": indicator(3, 2, "BULLISH"),
    }
    signal = StrategyEngine(SignalScoringService()).analyze(
        "XAUUSD", indicators, "BULLISH", 101, Config(),
        ["Spread exceeds configured maximum"],
    )
    assert signal["direction"] == "HOLD"
    assert signal["status"] == "REJECTED"


def test_confidence_score_is_bounded() -> None:
    score, factors = SignalScoringService().score({"trend_alignment": True})
    assert 0 <= score <= 100
    assert factors


def test_fake_mt5_has_no_order_execution_method() -> None:
    assert not hasattr(FakeMT5Client(), "order_send")
