from typing import Any


class SupportResistanceDetector:
    def detect(
        self,
        candles: list[dict[str, Any]],
        lookback: int,
        window: int,
        max_levels: int,
    ) -> tuple[list[float], list[float]]:
        sample = candles[-lookback:]
        supports: list[float] = []
        resistances: list[float] = []
        for index in range(window, len(sample) - window):
            neighbours = sample[index - window:index] + sample[index + 1:index + window + 1]
            candle = sample[index]
            if candle["low"] <= min(item["low"] for item in neighbours):
                supports.append(float(candle["low"]))
            if candle["high"] >= max(item["high"] for item in neighbours):
                resistances.append(float(candle["high"]))
        return self._unique(supports, max_levels), self._unique(resistances, max_levels)

    @staticmethod
    def _unique(levels: list[float], maximum: int) -> list[float]:
        unique: list[float] = []
        for level in reversed(levels):
            if not any(abs(level - existing) <= max(abs(level), 1.0) * 1e-6 for existing in unique):
                unique.append(level)
            if len(unique) == maximum:
                break
        return sorted(unique)
