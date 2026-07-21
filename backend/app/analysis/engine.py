from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.analysis.scoring import SignalScoringService


class StrategyEngine:
    FACTOR_LABELS = {
        "trend_alignment": "H1 EMA trend is aligned",
        "market_structure": "H1 market structure is aligned",
        "setup_alignment": "M15 EMA setup is aligned",
        "rsi_filter": "M15 RSI filter passed",
        "candle_confirmation": "M5 confirmation candle passed",
        "spread_filter": "Spread filter passed",
        "data_quality": "Closed candle data is complete and valid",
    }

    def __init__(self, scoring: SignalScoringService) -> None:
        self._scoring = scoring

    def analyze(
        self,
        symbol: str,
        indicators: dict[str, dict[str, Any]],
        confirmation: str,
        spread_points: float,
        config: Any,
        hard_rejections: list[str] | None = None,
    ) -> dict[str, Any]:
        h1, m15 = indicators["H1"], indicators["M15"]
        buy_setup = self._setup_aligned(m15, "BUY")
        sell_setup = self._setup_aligned(m15, "SELL")
        common = {
            "spread_filter": spread_points <= config.max_spread_points,
            "data_quality": all(item["data_valid"] for item in indicators.values()),
        }
        buy_factors = {
            "trend_alignment": h1["ema_fast"] > h1["ema_slow"],
            "market_structure": h1["market_structure"] == "BULLISH",
            "setup_alignment": buy_setup,
            "rsi_filter": m15["rsi"] <= config.rsi_overbought,
            "candle_confirmation": confirmation == "BULLISH",
            **common,
        }
        sell_factors = {
            "trend_alignment": h1["ema_fast"] < h1["ema_slow"],
            "market_structure": h1["market_structure"] == "BEARISH",
            "setup_alignment": sell_setup,
            "rsi_filter": m15["rsi"] >= config.rsi_oversold,
            "candle_confirmation": confirmation == "BEARISH",
            **common,
        }
        return self._build_signal(
            symbol, indicators, buy_factors, sell_factors,
            config, hard_rejections or [],
        )

    def _build_signal(
        self,
        symbol: str,
        indicators: dict[str, dict[str, Any]],
        buy_factors: dict[str, bool],
        sell_factors: dict[str, bool],
        config: Any,
        hard_rejections: list[str],
    ) -> dict[str, Any]:
        buy_score, buy_breakdown = self._scoring.score(buy_factors)
        sell_score, sell_breakdown = self._scoring.score(sell_factors)
        if not hard_rejections and all(buy_factors.values()):
            direction, status = "BUY", "CANDIDATE"
            factors, score, breakdown = buy_factors, buy_score, buy_breakdown
        elif not hard_rejections and all(sell_factors.values()):
            direction, status = "SELL", "CANDIDATE"
            factors, score, breakdown = sell_factors, sell_score, sell_breakdown
        else:
            direction = "HOLD"
            status = "REJECTED" if hard_rejections else "HOLD"
            if buy_score >= sell_score:
                factors, score, breakdown = buy_factors, buy_score, buy_breakdown
            else:
                factors, score, breakdown = sell_factors, sell_score, sell_breakdown
        reasons = [self.FACTOR_LABELS[name] for name, passed in factors.items() if passed]
        rejection_reasons = list(hard_rejections)
        rejection_reasons.extend(
            f"Rule not satisfied: {self.FACTOR_LABELS[name]}"
            for name, passed in factors.items()
            if not passed
        )
        m5 = indicators["M5"]
        return {
            "signal_id": str(uuid4()),
            "symbol": symbol,
            "direction": direction,
            "strategy_name": config.strategy_name,
            "trend_timeframe": "H1",
            "setup_timeframe": "M15",
            "confirmation_timeframe": "M5",
            "timeframe": "H1/M15/M5",
            "entry_reference_price": m5["close"],
            "atr": indicators["M15"]["atr"],
            "confidence_score": score,
            "score_factors": breakdown,
            "reasons": reasons,
            "rejection_reasons": rejection_reasons,
            "candle_time": m5["candle_time"],
            "created_at": datetime.now(timezone.utc),
            "status": status,
        }

    @staticmethod
    def _setup_aligned(indicator: dict[str, Any], direction: str) -> bool:
        fast, slow = indicator["ema_fast"], indicator["ema_slow"]
        previous_fast = indicator["ema_fast_previous"]
        previous_slow = indicator["ema_slow_previous"]
        if direction == "BUY":
            return fast > slow or (
                previous_fast is not None
                and previous_slow is not None
                and previous_fast <= previous_slow
                and fast > slow
            )
        return fast < slow or (
            previous_fast is not None
            and previous_slow is not None
            and previous_fast >= previous_slow
            and fast < slow
        )
