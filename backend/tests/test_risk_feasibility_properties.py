import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.risk import (
    PositionSizeCalculator,
    RiskCalculationError,
    RiskConfig,
    SymbolSpecification,
)
from app.risk import calculators as risk_calculators
from app.risk_feasibility.engine import RiskFeasibilityEngine
from app.risk_feasibility.gateway import ReadOnlyRiskSnapshotGateway
from app.risk_feasibility.mapper import RiskFeasibilityResultMapper, decimal_string
from app.risk_feasibility.service import RiskFeasibilityService
from app.risk_feasibility.types import (
    AtomicRiskSnapshot,
    EquityApplicability,
    FeasibilityCalculation,
    FeasibilityStatus,
    ReasonCode,
    Recommendation,
    RiskBaseType,
)
from app.risk_feasibility.validator import FeasibilityInputValidator
from tests.test_risk_feasibility import NOW, raw_input
from tests.test_risk_feasibility_service import settings as stored_settings
from tests.test_risk_feasibility_service import signal as stored_signal
from tests.test_risk_feasibility_service import snapshot as stored_snapshot

POSITIVE = st.integers(min_value=1, max_value=1_000_000)
PROPERTY_SETTINGS = settings(max_examples=100, deadline=None)


def _calculate(**changes: object) -> FeasibilityCalculation:
    outcome = FeasibilityInputValidator().validate(raw_input(**changes))
    assert outcome.value is not None
    return RiskFeasibilityEngine().calculate(outcome.value)


def _atomic_snapshot(payload: dict[str, Any]) -> AtomicRiskSnapshot:
    timestamps = raw_input().timestamps
    return AtomicRiskSnapshot(payload=payload, timestamps=timestamps)


class BusinessStateSpy:
    def __init__(self, context: str) -> None:
        self.context = context
        self.business_state = {
            "trade_plans": [],
            "daily_risk_states": {"loss": "0"},
            "orders": [],
        }
        self.read_calls = {"signal": 0, "settings": 0, "snapshot": 0}
        self.mutation_calls: list[str] = []

    async def get_by_id(self, signal_id: str) -> dict[str, Any]:
        self.read_calls["signal"] += 1
        return stored_signal()

    async def get_active(self) -> dict[str, Any]:
        self.read_calls["settings"] += 1
        return stored_settings()

    async def read(self, symbol: str) -> AtomicRiskSnapshot:
        self.read_calls["snapshot"] += 1
        equity = 10_000.0 if self.context == "valid" else 1.0
        resolved = "GOLD" if self.context == "unavailable" else symbol
        return _atomic_snapshot(stored_snapshot(equity=equity, symbol=resolved))

    def save_trade_plan(self, *args: object, **kwargs: object) -> None:
        self.mutation_calls.append("save_trade_plan")
        raise AssertionError("Analyzer attempted a forbidden write")

    def commit(self) -> None:
        self.mutation_calls.append("commit")
        raise AssertionError("Analyzer attempted a forbidden write")

    def flush(self) -> None:
        self.mutation_calls.append("flush")
        raise AssertionError("Analyzer attempted a forbidden write")

    def order_check(self, *args: object, **kwargs: object) -> None:
        self.mutation_calls.append("order_check")
        raise AssertionError("Analyzer attempted a forbidden broker action")

    def order_send(self, *args: object, **kwargs: object) -> None:
        self.mutation_calls.append("order_send")
        raise AssertionError("Analyzer attempted a forbidden broker action")


class FixedReaderSpy:
    def __init__(
        self,
        candidate: dict[str, Any],
        stored: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        self.candidate = candidate
        self.stored = stored
        self.payload = payload
        self.calls = {"signal": 0, "settings": 0, "snapshot": 0}

    async def get_by_id(self, signal_id: str) -> dict[str, Any]:
        self.calls["signal"] += 1
        return self.candidate

    async def get_active(self) -> dict[str, Any]:
        self.calls["settings"] += 1
        return self.stored

    async def read(self, symbol: str) -> AtomicRiskSnapshot:
        self.calls["snapshot"] += 1
        return _atomic_snapshot(self.payload)


def _risk_config(risk_percent: Decimal) -> RiskConfig:
    return RiskConfig(
        risk_per_trade_percent=risk_percent,
        maximum_spread_points=Decimal("20"),
    )


def _symbol_specification(
    *, volume_min: Decimal, volume_max: Decimal, volume_step: Decimal
) -> SymbolSpecification:
    return SymbolSpecification(
        digits=2,
        point=Decimal("1"),
        trade_tick_size=Decimal("1"),
        trade_tick_value=Decimal("1"),
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
        trade_stops_level=Decimal("0"),
        trade_freeze_level=Decimal("0"),
    )


@PROPERTY_SETTINGS
@given(
    context=st.sampled_from(["valid", "infeasible", "unavailable"]),
    repetitions=st.integers(min_value=1, max_value=5),
    nonce=POSITIVE,
)
def test_property_1_analysis_is_business_state_non_interfering(
    context: str, repetitions: int, nonce: int
) -> None:
    """Feature: risk-feasibility-analyzer, Property 1: Analysis is business-state non-interfering."""
    spy = BusinessStateSpy(context)
    before = deepcopy(spy.business_state)
    analyzer = RiskFeasibilityService(spy, spy, spy, lambda: NOW)

    results = [
        asyncio.run(analyzer.analyze(f"signal-{nonce}"))
        for _ in range(repetitions)
    ]

    expected_status = {
        "valid": "FEASIBLE",
        "infeasible": "INFEASIBLE",
        "unavailable": "UNAVAILABLE",
    }[context]
    assert all(result["status"] == expected_status for result in results)
    assert all("trade_plan_id" not in result for result in results)
    assert spy.business_state == before
    assert spy.mutation_calls == []
    assert spy.read_calls == {
        "signal": repetitions,
        "settings": repetitions,
        "snapshot": repetitions,
    }


@PROPERTY_SETTINGS
@given(
    balance=st.integers(min_value=100, max_value=1_000_000),
    equity=st.integers(min_value=100, max_value=1_000_000),
    risk_percent=st.integers(min_value=1, max_value=100),
    use_equity=st.booleans(),
    direction=st.sampled_from(["BUY", "SELL"]),
    bid=st.integers(min_value=100, max_value=100_000),
    spread=st.integers(min_value=1, max_value=100),
)
def test_property_2_authoritative_risk_base_and_market_source_selection(
    balance: int,
    equity: int,
    risk_percent: int,
    use_equity: bool,
    direction: str,
    bid: int,
    spread: int,
) -> None:
    """Feature: risk-feasibility-analyzer, Property 2: Authoritative risk-base and market-source selection."""
    if balance == equity:
        equity += 1
    candidate = stored_signal(direction)
    configured = stored_settings()
    configured["risk_per_trade_percent"] = risk_percent
    configured["use_equity_for_risk"] = use_equity
    payload = stored_snapshot()
    payload["account"].update(balance=str(balance), equity=str(equity))
    payload["tick"].update(bid=str(bid), ask=str(bid + spread))
    reader = FixedReaderSpy(candidate, configured, payload)
    analyzer = RiskFeasibilityService(reader, reader, reader, lambda: NOW)

    result = asyncio.run(analyzer.analyze("signal-1"))

    selected = equity if use_equity else balance
    expected_entry = bid + spread if direction == "BUY" else bid
    assert result["source_signal_id"] == candidate["signal_id"]
    assert result["symbol"] == candidate["symbol"]
    assert result["direction"] == direction
    assert result["account"]["balance"] == str(balance)
    assert result["account"]["equity"] == str(equity)
    assert result["account"]["risk_base_value"] == str(selected)
    assert result["account"]["configured_risk_percent"] == str(risk_percent)
    assert result["market"]["entry_price"] == str(expected_entry)
    assert reader.calls == {"signal": 1, "settings": 1, "snapshot": 1}


@PROPERTY_SETTINGS
@given(base=POSITIVE, risk=st.integers(min_value=1, max_value=100))
def test_property_6_decimal_position_sizing_pipeline_is_exact(
    base: int, risk: int
) -> None:
    """Feature: risk-feasibility-analyzer, Property 6: Decimal position-sizing formula pipeline is exact."""
    raw = raw_input(
        balance=str(base),
        equity=str(base),
        risk_base_value=str(base),
        risk_percent=str(risk),
    )
    outcome = FeasibilityInputValidator().validate(raw)
    assert outcome.value is not None
    result = RiskFeasibilityEngine().calculate(outcome.value)
    risk_amount = Decimal(base) * Decimal(risk) / Decimal("100")
    risk_per_lot = Decimal("100")
    expected_raw = risk_amount / risk_per_lot
    assert result.risk_amount == risk_amount
    assert result.risk_per_lot == risk_per_lot
    assert result.raw_lot == expected_raw
    assert result.capped_lot == min(expected_raw, Decimal("100"))


@PROPERTY_SETTINGS
@given(base=POSITIVE)
def test_property_7_zero_origin_normalization_always_floors(base: int) -> None:
    """Feature: risk-feasibility-analyzer, Property 7: Zero-origin normalization always floors to the broker step."""
    raw = raw_input(balance=str(base), equity=str(base), risk_base_value=str(base))
    outcome = FeasibilityInputValidator().validate(raw)
    assert outcome.value is not None
    result = RiskFeasibilityEngine().calculate(outcome.value)
    assert result.capped_lot is not None and result.normalized_lot is not None
    expected = (result.capped_lot / Decimal("0.01")).to_integral_value(
        rounding=ROUND_FLOOR
    ) * Decimal("0.01")
    assert result.normalized_lot == expected
    assert result.normalized_lot <= result.capped_lot
    assert result.capped_lot - result.normalized_lot < Decimal("0.01")


@PROPERTY_SETTINGS
@given(base=st.integers(min_value=-1_000_000, max_value=0))
def test_property_4_non_positive_capital_is_infeasible_without_fallback(
    base: int,
) -> None:
    """Feature: risk-feasibility-analyzer, Property 4: Non-positive selected capital is infeasible without fallback."""
    raw = raw_input(balance=str(base), equity=str(base), risk_base_value=str(base))
    outcome = FeasibilityInputValidator().validate(raw)
    assert outcome.value is not None
    result = RiskFeasibilityEngine().calculate(outcome.value)
    assert result.status is FeasibilityStatus.INFEASIBLE
    assert result.reasons[0].code is ReasonCode.RISK_BASE_NOT_POSITIVE
    assert result.minimum_lot_estimated_risk_percent is None


@PROPERTY_SETTINGS
@given(
    value=st.decimals(
        min_value=Decimal("-1000000000"),
        max_value=Decimal("1000000000"),
        allow_nan=False,
        allow_infinity=False,
        places=8,
    )
)
def test_property_14_decimal_api_serialization_is_lossless(value: Decimal) -> None:
    """Feature: risk-feasibility-analyzer, Property 14: Decimal API serialization is lossless."""
    serialized = decimal_string(value)
    assert serialized is not None
    assert "E" not in serialized.upper()
    assert Decimal(serialized) == value


@PROPERTY_SETTINGS
@given(invalid=st.sampled_from(["NaN", "Infinity", "-Infinity", True, None]))
def test_property_3_invalid_inputs_fail_closed(invalid: object) -> None:
    """Feature: risk-feasibility-analyzer, Property 3: Invalid or untrustworthy inputs fail closed."""
    outcome = FeasibilityInputValidator().validate(
        replace(raw_input(), trade_tick_value=invalid)
    )
    assert outcome.value is None
    assert outcome.reasons[0].code is ReasonCode.INPUT_INVALID


@PROPERTY_SETTINGS
@given(
    base=st.integers(min_value=-1_000_000, max_value=0),
    unavailable=st.sampled_from(["invalid", "stale", "mismatch", "grid"]),
    nonce=POSITIVE,
)
def test_property_5_unavailable_conditions_dominate_infeasible_conditions(
    base: int, unavailable: str, nonce: int
) -> None:
    """Feature: risk-feasibility-analyzer, Property 5: Unavailable conditions dominate infeasible conditions."""
    raw = raw_input(
        balance=str(base), equity=str(base), risk_base_value=str(base)
    )
    if unavailable == "invalid":
        raw = replace(raw, trade_tick_value="NaN")
    elif unavailable == "stale":
        raw = replace(
            raw,
            analysis_timestamp=NOW + timedelta(seconds=61 + nonce % 60),
        )
    elif unavailable == "mismatch":
        raw = replace(raw, resolved_symbol=f"OTHER-{nonce}")
    else:
        raw = replace(
            raw,
            volume_min="1.1",
            volume_max="1.5",
            volume_step="1",
        )
    outcome = FeasibilityInputValidator().validate(raw)
    result = (
        FeasibilityCalculation.unavailable(outcome.reasons)
        if outcome.value is None
        else RiskFeasibilityEngine().calculate(outcome.value)
    )
    assert result.status is FeasibilityStatus.UNAVAILABLE
    assert result.recommendation is Recommendation.RETRY


@PROPERTY_SETTINGS
@given(
    accepted=st.booleans(),
    lot_units=st.integers(min_value=1, max_value=100),
    below_minimum_tenths=st.integers(min_value=1, max_value=9),
)
def test_property_8_analyzer_matches_the_unchanged_calculator_boundary(
    accepted: bool, lot_units: int, below_minimum_tenths: int
) -> None:
    """Feature: risk-feasibility-analyzer, Property 8: Analyzer matches the unchanged calculator boundary."""
    raw_lot = (
        Decimal(lot_units)
        if accepted
        else Decimal(below_minimum_tenths) / Decimal("10")
    )
    risk_base = raw_lot * Decimal("100")
    volume_min = Decimal("0.1") if accepted else Decimal("1")
    volume_step = Decimal("0.1")
    volume_max = Decimal("100")
    analyzer = _calculate(
        balance=risk_base,
        equity=risk_base,
        risk_base_value=risk_base,
        risk_percent="1",
        entry_price="101",
        stop_loss_price="100",
        trade_tick_size="1",
        trade_tick_value="1",
        point="1",
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
    )
    config = _risk_config(Decimal("1"))
    spec = _symbol_specification(
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
    )
    calculator_boundaries: list[Decimal] = []
    original_floor = risk_calculators._floor_to_step

    def floor_spy(value: Decimal, step: Decimal) -> Decimal:
        boundary = original_floor(value, step)
        calculator_boundaries.append(boundary)
        return boundary

    with patch.object(risk_calculators, "_floor_to_step", side_effect=floor_spy):
        if accepted:
            legacy = PositionSizeCalculator().calculate(
                risk_base, risk_base, Decimal("1"), config, spec
            )
        else:
            with pytest.raises(RiskCalculationError, match="below volume_min"):
                PositionSizeCalculator().calculate(
                    risk_base, risk_base, Decimal("1"), config, spec
                )
            legacy = None

    expected_risk = risk_base / Decimal("100")
    assert analyzer.risk_amount == expected_risk
    assert analyzer.ticks_at_risk == Decimal("1")
    assert analyzer.risk_per_lot == Decimal("1")
    assert analyzer.raw_lot == raw_lot
    assert analyzer.capped_lot == raw_lot
    assert analyzer.normalized_lot == calculator_boundaries[-1]
    if legacy is not None:
        details = legacy["calculation_details"]
        assert Decimal(str(legacy["risk_amount"])) == analyzer.risk_amount
        assert Decimal(str(legacy["risk_per_lot"])) == analyzer.risk_per_lot
        assert Decimal(str(details["raw_lot"])) == analyzer.raw_lot
        assert Decimal(str(details["capped_lot"])) == analyzer.capped_lot
        assert Decimal(str(details["normalized_lot"])) == analyzer.normalized_lot
    else:
        assert analyzer.status is FeasibilityStatus.INFEASIBLE
        assert analyzer.normalized_lot is not None
        assert analyzer.normalized_lot < volume_min


@PROPERTY_SETTINGS
@given(
    minimum_units=st.integers(min_value=1, max_value=500),
    step_units=st.integers(min_value=1, max_value=100),
    maximum_offset=st.integers(min_value=0, max_value=500),
    raw_units=st.integers(min_value=1, max_value=1_000),
)
def test_property_9_effective_minimum_and_classification_are_grid_correct(
    minimum_units: int,
    step_units: int,
    maximum_offset: int,
    raw_units: int,
) -> None:
    """Feature: risk-feasibility-analyzer, Property 9: Effective broker minimum and feasibility classification are grid-correct."""
    scale = Decimal("10")
    volume_min = Decimal(minimum_units) / scale
    volume_step = Decimal(step_units) / scale
    volume_max = Decimal(minimum_units + maximum_offset) / scale
    raw_lot = Decimal(raw_units) / scale
    result = _calculate(
        balance=raw_lot,
        equity=raw_lot,
        risk_base_value=raw_lot,
        risk_percent="100",
        entry_price="101",
        stop_loss_price="100",
        trade_tick_size="1",
        trade_tick_value="1",
        point="1",
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
    )
    effective = (
        volume_min / volume_step
    ).to_integral_value(rounding=ROUND_CEILING) * volume_step
    if effective > volume_max:
        assert result.status is FeasibilityStatus.UNAVAILABLE
        assert result.reasons[0].code is ReasonCode.BROKER_VOLUME_GRID_INVALID
        return
    capped = min(raw_lot, volume_max)
    normalized = (
        capped / volume_step
    ).to_integral_value(rounding=ROUND_FLOOR) * volume_step
    assert result.minimum_broker_lot == effective
    assert result.normalized_lot == normalized
    expected = (
        FeasibilityStatus.FEASIBLE
        if normalized >= effective
        else FeasibilityStatus.INFEASIBLE
    )
    assert result.status is expected


@PROPERTY_SETTINGS
@given(
    risk_base=POSITIVE,
    risk_percent=st.integers(min_value=1, max_value=100),
    ticks=st.integers(min_value=1, max_value=1_000),
    tick_value=st.integers(min_value=1, max_value=1_000),
    minimum_lot=st.integers(min_value=1, max_value=100),
    mode=st.sampled_from([RiskBaseType.EQUITY, RiskBaseType.BALANCE]),
)
def test_property_10_required_capital_threshold_respects_risk_base_mode(
    risk_base: int,
    risk_percent: int,
    ticks: int,
    tick_value: int,
    minimum_lot: int,
    mode: RiskBaseType,
) -> None:
    """Feature: risk-feasibility-analyzer, Property 10: Required capital threshold respects risk-base mode."""
    result = _calculate(
        balance=str(risk_base),
        equity=str(risk_base),
        risk_base_type=mode,
        risk_base_value=str(risk_base),
        risk_percent=str(risk_percent),
        entry_price=str(ticks + 100),
        stop_loss_price="100",
        trade_tick_size="1",
        trade_tick_value=str(tick_value),
        point="1",
        volume_min=str(minimum_lot),
        volume_max="1000",
        volume_step="1",
    )
    risk_per_lot = Decimal(ticks) * Decimal(tick_value)
    expected = (
        Decimal(minimum_lot)
        * risk_per_lot
        * Decimal("100")
        / Decimal(risk_percent)
    )
    assert result.required_minimum_risk_base == expected
    if mode is RiskBaseType.EQUITY:
        assert result.required_minimum_equity == expected
        assert (
            result.required_minimum_equity_applicability
            is EquityApplicability.APPLICABLE
        )
    else:
        assert result.required_minimum_equity is None
        assert result.required_minimum_equity_applicability is (
            EquityApplicability.HYPOTHETICAL_NOT_APPLICABLE
        )


@PROPERTY_SETTINGS
@given(
    direction=st.sampled_from(["BUY", "SELL"]),
    risk_units=st.integers(min_value=1, max_value=1_000),
    excess=st.integers(min_value=0, max_value=1_000),
)
def test_property_11_maximum_stop_diagnostics_are_directionally_correct(
    direction: str, risk_units: int, excess: int
) -> None:
    """Feature: risk-feasibility-analyzer, Property 11: Maximum-stop diagnostics are directionally correct."""
    maximum_stop = Decimal(risk_units)
    actual_distance = maximum_stop + Decimal(excess)
    entry = actual_distance + Decimal("100") if direction == "BUY" else Decimal("100")
    stop = Decimal("100") if direction == "BUY" else entry + actual_distance
    result = _calculate(
        balance=str(risk_units * 100),
        equity=str(risk_units * 100),
        risk_base_value=str(risk_units * 100),
        risk_percent="1",
        direction=direction,
        entry_price=entry,
        stop_loss_price=stop,
        trade_tick_size="1",
        trade_tick_value="1",
        point="1",
        volume_min="1",
        volume_max="1000",
        volume_step="1",
    )
    expected_boundary = (
        entry - maximum_stop if direction == "BUY" else entry + maximum_stop
    )
    reason_codes = [reason.code for reason in result.reasons]
    assert result.maximum_stop_distance == maximum_stop
    assert result.maximum_stop_distance_points == maximum_stop
    assert result.boundary_stop_loss_price == expected_boundary
    if excess:
        assert ReasonCode.STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM in reason_codes
    else:
        assert ReasonCode.STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM not in reason_codes


@PROPERTY_SETTINGS
@given(
    risk_base=POSITIVE,
    risk_percent=st.integers(min_value=1, max_value=100),
    ticks=st.integers(min_value=1, max_value=1_000),
    tick_value=st.integers(min_value=1, max_value=1_000),
    minimum_lot=st.integers(min_value=1, max_value=100),
)
def test_property_12_minimum_lot_risk_diagnostics_and_deltas_are_exact(
    risk_base: int,
    risk_percent: int,
    ticks: int,
    tick_value: int,
    minimum_lot: int,
) -> None:
    """Feature: risk-feasibility-analyzer, Property 12: Minimum-lot risk diagnostics and excess deltas are exact."""
    result = _calculate(
        balance=str(risk_base),
        equity=str(risk_base),
        risk_base_value=str(risk_base),
        risk_percent=str(risk_percent),
        entry_price=str(ticks + 100),
        stop_loss_price="100",
        trade_tick_size="1",
        trade_tick_value=str(tick_value),
        point="1",
        volume_min=str(minimum_lot),
        volume_max="1000",
        volume_step="1",
    )
    estimated_amount = (
        Decimal(minimum_lot) * Decimal(ticks) * Decimal(tick_value)
    )
    estimated_percent = estimated_amount / Decimal(risk_base) * Decimal("100")
    configured_amount = Decimal(risk_base) * Decimal(risk_percent) / Decimal("100")
    assert result.minimum_lot_estimated_risk_amount == estimated_amount
    assert result.minimum_lot_estimated_risk_percent == estimated_percent
    assert result.minimum_lot_risk_delta_amount == max(
        estimated_amount - configured_amount, Decimal("0")
    )
    assert result.minimum_lot_risk_delta_percent == max(
        estimated_percent - Decimal(risk_percent), Decimal("0")
    )


@PROPERTY_SETTINGS
@given(
    kind=st.sampled_from(
        ["feasible", "capital", "minimum", "unavailable_grid"]
    ),
    nonce=POSITIVE,
)
def test_property_13_status_reasons_and_recommendation_are_deterministic(
    kind: str, nonce: int
) -> None:
    """Feature: risk-feasibility-analyzer, Property 13: Status, reasons, and recommendation are deterministic."""
    changes: dict[str, object]
    if kind == "feasible":
        base = 100 * (nonce % 1_000 + 1)
        changes = {
            "balance": str(base),
            "equity": str(base),
            "risk_base_value": str(base),
        }
    elif kind == "capital":
        changes = {
            "balance": str(-nonce),
            "equity": str(-nonce),
            "risk_base_value": str(-nonce),
        }
    elif kind == "minimum":
        base = nonce % 99 + 1
        changes = {
            "balance": str(base),
            "equity": str(base),
            "risk_base_value": str(base),
        }
    else:
        changes = {
            "volume_min": "1.1",
            "volume_max": "1.5",
            "volume_step": "1",
        }
    raw = raw_input(**changes)
    outcome = FeasibilityInputValidator().validate(raw)
    assert outcome.value is not None
    engine = RiskFeasibilityEngine()
    first = engine.calculate(outcome.value)
    second = engine.calculate(outcome.value)
    mapper = RiskFeasibilityResultMapper()
    first_payload = mapper.map(raw, first)
    second_payload = mapper.map(raw, second)
    first_payload.pop("analysis_timestamp")
    first_payload.pop("snapshot_timestamps")
    second_payload.pop("analysis_timestamp")
    second_payload.pop("snapshot_timestamps")

    assert first == second
    assert first_payload == second_payload
    assert first.status in set(FeasibilityStatus)
    if first.status is FeasibilityStatus.INFEASIBLE:
        assert first.recommendation is Recommendation.DO_NOT_FORCE
    codes = [reason.code for reason in first.reasons]
    assert len(codes) == len(set(codes))
    assert all(code in set(ReasonCode) for code in codes)
    priorities = [list(ReasonCode).index(code) for code in codes]
    assert priorities == sorted(priorities)


class FailingSnapshotSource:
    def __init__(self, error_text: str) -> None:
        self.error_text = error_text
        self.calls = 0

    async def risk_snapshot(
        self, requested_symbol: str | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        raise RuntimeError(self.error_text)


@PROPERTY_SETTINGS
@given(
    marker=st.sampled_from(
        [
            "credential",
            "token",
            "authorization",
            "traceback",
            "login",
            "environment",
        ]
    ),
    supplied=st.text(
        alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "Z"),
            whitelist_characters="\n\r\t",
        ),
        min_size=1,
        max_size=80,
    ),
)
def test_property_15_error_sanitization_never_leaks_sensitive_input(
    marker: str, supplied: str
) -> None:
    """Feature: risk-feasibility-analyzer, Property 15: Error sanitization never leaks sensitive input."""
    sensitive_value = f"USER-SUPPLIED-SECRET::{supplied}::NEVER-PUBLIC"
    secret = f"SENSITIVE-BEGIN::{marker}::{sensitive_value}::SENSITIVE-END"
    source = FailingSnapshotSource(secret)
    analyzer = RiskFeasibilityService(
        FixedReaderSpy(stored_signal(), stored_settings(), stored_snapshot()),
        FixedReaderSpy(stored_signal(), stored_settings(), stored_snapshot()),
        ReadOnlyRiskSnapshotGateway(source, lambda: NOW),
        lambda: NOW,
    )

    result = asyncio.run(analyzer.analyze("signal-1"))
    public = str(result)

    assert result["status"] == "UNAVAILABLE"
    assert result["reasons"] == [
        {
            "code": "SNAPSHOT_UNAVAILABLE",
            "message": "A required account or market snapshot is unavailable.",
        }
    ]
    assert secret not in public
    assert sensitive_value not in public
    assert marker not in public.lower()
    assert source.calls == 1
