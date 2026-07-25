from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.risk_feasibility.engine import RiskFeasibilityEngine
from app.risk_feasibility.mapper import RiskFeasibilityResultMapper, decimal_string
from app.risk_feasibility.types import (
    FeasibilityCalculation,
    FeasibilityStatus,
    RawFeasibilityInput,
    ReasonCode,
    RiskBaseType,
    SnapshotTimestamps,
)
from app.risk_feasibility.validator import FeasibilityInputValidator

NOW = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)


def raw_input(**changes: object) -> RawFeasibilityInput:
    timestamps = SnapshotTimestamps(
        captured_at=NOW, account_at=NOW, symbol_at=NOW,
        tick_at=NOW, fresh_until=NOW + timedelta(seconds=60),
    )
    values = {
        "source_signal_id": "signal-1", "symbol": "XAUUSD",
        "resolved_symbol": "XAUUSD", "direction": "BUY",
        "analysis_timestamp": NOW, "timestamps": timestamps,
        "account_currency": "USD", "balance": "10000", "equity": "10000",
        "risk_base_type": RiskBaseType.EQUITY, "risk_base_value": "10000",
        "risk_percent": "1", "entry_price": "3000",
        "stop_loss_price": "2999", "trade_tick_size": "0.01",
        "trade_tick_value": "1", "point": "0.01", "volume_min": "0.01",
        "volume_max": "100", "volume_step": "0.01",
    }
    values.update(changes)
    return RawFeasibilityInput(**values)


def calculate(raw: RawFeasibilityInput) -> FeasibilityCalculation:
    outcome = FeasibilityInputValidator().validate(raw)
    assert outcome.value is not None
    return RiskFeasibilityEngine().calculate(outcome.value)


def test_exact_decimal_pipeline_and_feasible_boundary() -> None:
    result = calculate(raw_input())
    assert result.status is FeasibilityStatus.FEASIBLE
    assert result.risk_amount == Decimal("100")
    assert result.risk_per_lot == Decimal("100")
    assert result.raw_lot == Decimal("1")
    assert result.normalized_lot == Decimal("1")
    assert result.minimum_broker_lot == Decimal("0.01")
    assert result.boundary_stop_loss_price == Decimal("2900")


def test_between_step_floors_without_clamping_up() -> None:
    result = calculate(raw_input(risk_base_value="15", balance="15", equity="15"))
    assert result.raw_lot == Decimal("0.0015")
    assert result.normalized_lot == Decimal("0.00")
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert [reason.code for reason in result.reasons] == [
        ReasonCode.NORMALIZED_LOT_BELOW_BROKER_MINIMUM,
        ReasonCode.STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM,
    ]


def test_non_positive_risk_base_is_infeasible_with_null_division_diagnostics() -> None:
    result = calculate(raw_input(risk_base_value="0", balance="0", equity="0"))
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert result.reasons[0].code is ReasonCode.RISK_BASE_NOT_POSITIVE
    assert result.risk_amount is None
    assert result.minimum_lot_estimated_risk_percent is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"trade_tick_size": "NaN"}, ReasonCode.INPUT_INVALID),
        ({"direction": "HOLD"}, ReasonCode.INPUT_INVALID),
        ({"stop_loss_price": "3001"}, ReasonCode.INPUT_INVALID),
        ({"resolved_symbol": "GOLD"}, ReasonCode.SYMBOL_MISMATCH),
        ({"volume_max": "0.001"}, ReasonCode.BROKER_VOLUME_GRID_INVALID),
    ],
)
def test_invalid_inputs_fail_closed(change: dict[str, object], reason: ReasonCode) -> None:
    outcome = FeasibilityInputValidator().validate(raw_input(**change))
    assert outcome.value is None
    assert reason in [item.code for item in outcome.reasons]


def test_stale_snapshot_is_unavailable_precedence() -> None:
    stale = replace(
        raw_input(risk_base_value="0"),
        timestamps=SnapshotTimestamps(NOW, NOW, NOW, NOW, NOW),
        analysis_timestamp=NOW + timedelta(seconds=61),
    )
    outcome = FeasibilityInputValidator().validate(stale)
    assert outcome.value is None
    calculation = FeasibilityCalculation.unavailable(outcome.reasons)
    assert calculation.status is FeasibilityStatus.UNAVAILABLE
    assert calculation.reasons[0].code is ReasonCode.SNAPSHOT_STALE


def test_balance_mode_and_lossless_mapping() -> None:
    raw = raw_input(
        risk_base_type=RiskBaseType.BALANCE,
        risk_base_value="10000",
    )
    result = calculate(raw)
    payload = RiskFeasibilityResultMapper().map(raw, result)
    assert payload["account"]["risk_base_type"] == "BALANCE"
    assert payload["calculation"]["required_minimum_equity"] is None
    assert payload["calculation"]["required_minimum_equity_applicability"] == (
        "HYPOTHETICAL_NOT_APPLICABLE"
    )
    assert payload["volume"]["normalized_lot"] == "1"
    assert Decimal(decimal_string(Decimal("0.0100")) or "") == Decimal("0.0100")
    assert "trade_plan_id" not in payload


def test_effective_minimum_uses_ceiling_grid_and_invalid_grid_fails_closed() -> None:
    result = calculate(raw_input(volume_min="0.015", volume_step="0.01"))
    assert result.minimum_broker_lot == Decimal("0.02")
    unavailable = calculate(
        raw_input(volume_min="0.015", volume_step="0.01", volume_max="0.019")
    )
    assert unavailable.status is FeasibilityStatus.UNAVAILABLE
    assert unavailable.reasons[0].code is ReasonCode.BROKER_VOLUME_GRID_INVALID
