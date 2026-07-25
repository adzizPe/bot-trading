from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from app.risk_feasibility.types import (
    EquityApplicability,
    FeasibilityCalculation,
    FeasibilityStatus,
    ReasonCode,
    Recommendation,
    RiskBaseType,
    ValidFeasibilityInput,
    reasons_for,
)


class RiskFeasibilityEngine:
    """Pure Decimal implementation of the unchanged position-size boundary."""

    def calculate(self, value: ValidFeasibilityInput) -> FeasibilityCalculation:
        minimum = (
            value.volume_min / value.volume_step
        ).to_integral_value(rounding=ROUND_CEILING) * value.volume_step
        if minimum > value.volume_max:
            return FeasibilityCalculation.unavailable(
                reasons_for(ReasonCode.BROKER_VOLUME_GRID_INVALID)
            )
        stop_distance = abs(value.entry_price - value.stop_loss_price)
        ticks = stop_distance / value.trade_tick_size
        risk_per_lot = ticks * value.trade_tick_value
        if value.risk_base_value <= 0:
            return FeasibilityCalculation(
                status=FeasibilityStatus.INFEASIBLE,
                recommendation=Recommendation.DO_NOT_FORCE,
                reasons=reasons_for(ReasonCode.RISK_BASE_NOT_POSITIVE),
                stop_distance=stop_distance,
                stop_distance_points=stop_distance / value.point,
                ticks_at_risk=ticks,
                risk_per_lot=risk_per_lot,
                minimum_broker_lot=minimum,
            )
        risk_amount = value.risk_base_value * value.risk_percent / Decimal("100")
        raw_lot = risk_amount / risk_per_lot
        capped_lot = min(raw_lot, value.volume_max)
        normalized = (
            capped_lot / value.volume_step
        ).to_integral_value(rounding=ROUND_FLOOR) * value.volume_step
        required_base = minimum * risk_per_lot * Decimal("100") / value.risk_percent
        maximum_stop = (
            risk_amount * value.trade_tick_size
            / (minimum * value.trade_tick_value)
        )
        boundary = (
            value.entry_price - maximum_stop
            if value.direction == "BUY"
            else value.entry_price + maximum_stop
        )
        minimum_risk = minimum * ticks * value.trade_tick_value
        minimum_percent = minimum_risk / value.risk_base_value * Decimal("100")
        delta_amount = max(minimum_risk - risk_amount, Decimal("0"))
        delta_percent = max(minimum_percent - value.risk_percent, Decimal("0"))
        reason_codes: list[ReasonCode] = []
        if normalized < minimum:
            reason_codes.append(ReasonCode.NORMALIZED_LOT_BELOW_BROKER_MINIMUM)
        if stop_distance > maximum_stop:
            reason_codes.append(ReasonCode.STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM)
        status = (
            FeasibilityStatus.INFEASIBLE
            if reason_codes
            else FeasibilityStatus.FEASIBLE
        )
        recommendation = (
            Recommendation.DO_NOT_FORCE
            if reason_codes
            else Recommendation.PROCEED
        )
        applicability = (
            EquityApplicability.APPLICABLE
            if value.risk_base_type is RiskBaseType.EQUITY
            else EquityApplicability.HYPOTHETICAL_NOT_APPLICABLE
        )
        return FeasibilityCalculation(
            status=status, recommendation=recommendation,
            reasons=reasons_for(*reason_codes), risk_amount=risk_amount,
            stop_distance=stop_distance,
            stop_distance_points=stop_distance / value.point,
            ticks_at_risk=ticks, risk_per_lot=risk_per_lot,
            raw_lot=raw_lot, capped_lot=capped_lot,
            normalized_lot=normalized, minimum_broker_lot=minimum,
            required_minimum_risk_base=required_base,
            required_minimum_equity=(
                required_base if applicability is EquityApplicability.APPLICABLE else None
            ),
            required_minimum_equity_applicability=applicability,
            maximum_stop_distance=maximum_stop,
            maximum_stop_distance_points=maximum_stop / value.point,
            boundary_stop_loss_price=boundary,
            minimum_lot_estimated_risk_amount=minimum_risk,
            minimum_lot_estimated_risk_percent=minimum_percent,
            minimum_lot_risk_delta_amount=delta_amount,
            minimum_lot_risk_delta_percent=delta_percent,
        )
