from typing import Any


class MarketStructureDetector:
    def detect(self, candles: list[dict[str, Any]], lookback: int) -> dict[str, str]:
        sample = candles[-max(4, lookback):]
        midpoint = len(sample) // 2
        first, second = sample[:midpoint], sample[midpoint:]
        first_high = max(candle["high"] for candle in first)
        second_high = max(candle["high"] for candle in second)
        first_low = min(candle["low"] for candle in first)
        second_low = min(candle["low"] for candle in second)
        high_pattern = "HH" if second_high > first_high else "LH" if second_high < first_high else "EH"
        low_pattern = "HL" if second_low > first_low else "LL" if second_low < first_low else "EL"
        if high_pattern == "HH" and low_pattern == "HL":
            structure = "BULLISH"
        elif high_pattern == "LH" and low_pattern == "LL":
            structure = "BEARISH"
        else:
            structure = "NEUTRAL"
        return {"structure": structure, "high_pattern": high_pattern, "low_pattern": low_pattern}
