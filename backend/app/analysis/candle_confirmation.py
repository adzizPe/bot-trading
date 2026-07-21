from typing import Any


class CandleConfirmationDetector:
    def detect(
        self,
        candle: dict[str, Any],
        atr: float,
        minimum_body_atr: float,
        minimum_close_location: float,
    ) -> str:
        if not candle.get("is_closed") or atr <= 0:
            return "NONE"
        candle_range = candle["high"] - candle["low"]
        body = abs(candle["close"] - candle["open"])
        if candle_range <= 0 or body < atr * minimum_body_atr:
            return "NONE"
        bullish_location = (candle["close"] - candle["low"]) / candle_range
        bearish_location = (candle["high"] - candle["close"]) / candle_range
        if candle["close"] > candle["open"] and bullish_location >= minimum_close_location:
            return "BULLISH"
        if candle["close"] < candle["open"] and bearish_location >= minimum_close_location:
            return "BEARISH"
        return "NONE"
