from typing import Any


class SignalScoringService:
    WEIGHTS = {
        "trend_alignment": 25,
        "market_structure": 15,
        "setup_alignment": 15,
        "rsi_filter": 10,
        "candle_confirmation": 15,
        "spread_filter": 10,
        "data_quality": 10,
    }

    def score(self, factors: dict[str, bool]) -> tuple[float, list[dict[str, Any]]]:
        breakdown = []
        total = 0
        for name, weight in self.WEIGHTS.items():
            passed = bool(factors.get(name, False))
            points = weight if passed else 0
            total += points
            breakdown.append(
                {"factor": name, "passed": passed, "weight": weight, "points": points}
            )
        return float(max(0, min(100, total))), breakdown
