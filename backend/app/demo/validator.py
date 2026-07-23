import math
from datetime import datetime, timezone
from typing import Any

from app.demo.exceptions import DemoValidationError
from app.demo.types import ExecutionPlan


class DemoIntentValidator:
    """Validates immutable risk-plan identity and freshness before reservation."""

    @staticmethod
    def validate(
        plan: dict[str, Any], signal: dict[str, Any], ttl_seconds: int,
        maximum_spread_points: float,
    ) -> ExecutionPlan:
        execution = ExecutionPlan.from_record(plan)
        if signal.get("status") != "CANDIDATE":
            raise DemoValidationError("Only a CANDIDATE signal can be executed")
        if str(signal.get("signal_id")) != execution.signal_id:
            raise DemoValidationError("Trade plan signal identity does not match")
        if str(signal.get("direction", "")).upper() != execution.direction:
            raise DemoValidationError("Trade plan and signal directions do not match")
        risk_percent = float(plan.get("risk_percent", 0))
        risk_amount = float(plan.get("risk_amount", 0))
        spread = float(plan.get("spread_points", float("inf")))
        if not 0 < risk_percent <= 100 or risk_amount <= 0:
            raise DemoValidationError("Trade plan risk values are invalid")
        if (
            not math.isfinite(spread) or spread < 0
            or spread > maximum_spread_points
        ):
            raise DemoValidationError("Trade plan spread exceeds configured maximum")
        created = signal.get("created_at")
        if not isinstance(created, datetime):
            raise DemoValidationError("Signal creation time is unavailable")
        aware = created.replace(tzinfo=created.tzinfo or timezone.utc)
        age = (datetime.now(timezone.utc) - aware).total_seconds()
        if age < 0 or age > ttl_seconds:
            raise DemoValidationError("Persisted signal has expired")
        return execution


class OrderRequestValidator(DemoIntentValidator):
    """Public Milestone 9 request validator facade."""


def validate_confirmation(text: str) -> None:
    if text != "EXECUTE DEMO ORDER":
        raise DemoValidationError("Invalid demo execution confirmation")