from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.risk import (
    ConsecutiveLossLimiter,
    DailyRiskLimiter,
    DrawdownLimiter,
    PositionSizeCalculator,
    RiskCalculationError,
    RiskConfig,
    RiskManager,
    RiskRewardValidator,
    SpreadRiskValidator,
    StopLossCalculator,
    SymbolSpecification,
    TakeProfitCalculator,
)

NOW = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)


def config(**overrides: object) -> RiskConfig:
    values: dict[str, object] = {"maximum_spread_points": "20"}
    values.update(overrides)
    return RiskConfig(**values)  # type: ignore[arg-type]


def spec(**overrides: object) -> SymbolSpecification:
    values: dict[str, object] = {
        "digits": 2,
        "point": "0.01",
        "trade_tick_size": "0.01",
        "trade_tick_value": "1",
        "volume_min": "0.01",
        "volume_max": "100",
        "volume_step": "0.01",
        "trade_stops_level": "10",
        "trade_freeze_level": "0",
    }
    values.update(overrides)
    return SymbolSpecification(**values)  # type: ignore[arg-type]


def state(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "realized_loss": "0",
        "starting_balance": "10000",
        "peak_equity": "10000",
        "floating_drawdown": "0",
        "consecutive_losses": 0,
        "trades_count": 0,
        "open_positions": 0,
        "cooldown_until": None,
    }
    values.update(overrides)
    return values


def test_risk_amount_uses_equity_when_configured() -> None:
    result = PositionSizeCalculator().calculate("10000", "8000", "1", config(), spec())
    assert result["risk_amount"] == 80.0


def test_lot_uses_tick_size_and_tick_value() -> None:
    result = PositionSizeCalculator().calculate(
        "10000", "10000", "0.50", config(), spec(trade_tick_size="0.10", trade_tick_value="2")
    )
    assert result["lot_size"] == 10.0
    assert result["risk_per_lot"] == 10.0


def test_volume_below_minimum_is_rejected() -> None:
    with pytest.raises(RiskCalculationError, match="below volume_min"):
        PositionSizeCalculator().calculate(
            "100", "100", "100", config(), spec(volume_min="0.10")
        )


def test_volume_is_clamped_to_maximum() -> None:
    result = PositionSizeCalculator().calculate(
        "1000000", "1000000", "0.01", config(), spec(volume_max="2.37", volume_step="0.10")
    )
    assert result["lot_size"] == 2.3


def test_volume_is_floored_to_step() -> None:
    result = PositionSizeCalculator().calculate(
        "10000", "10000", "0.30", config(), spec(volume_step="0.10")
    )
    assert result["lot_size"] == 3.3


def test_zero_tick_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="trade_tick_value must be positive"):
        spec(trade_tick_value="0")


def test_zero_tick_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="trade_tick_size must be positive"):
        spec(trade_tick_size="0")


def test_buy_stop_loss_wrong_side_is_rejected() -> None:
    with pytest.raises(RiskCalculationError, match="BUY stop loss"):
        StopLossCalculator().calculate(
            "BUY", "100", "1", config(stop_loss_method="SWING"), spec(), "101"
        )


def test_sell_stop_loss_wrong_side_is_rejected() -> None:
    with pytest.raises(RiskCalculationError, match="SELL stop loss"):
        StopLossCalculator().calculate(
            "SELL", "100", "1", config(stop_loss_method="SWING"), spec(), "99"
        )


def test_take_profit_wrong_side_is_rejected() -> None:
    with pytest.raises(RiskCalculationError, match="BUY take profit"):
        TakeProfitCalculator().calculate("BUY", "100", "99", config(), spec(), "98")


def test_minimum_stops_level_is_enforced() -> None:
    result = StopLossCalculator().calculate("BUY", "100", "0.01", config(), spec())
    assert result["stop_loss"] == 99.9
    assert result["stop_distance"] == pytest.approx(0.1)


def test_risk_reward_below_minimum_is_rejected() -> None:
    reasons = RiskRewardValidator().validate("BUY", "100", "99", "101", config())
    assert reasons == ["Risk-reward ratio is below configured minimum"]


def test_spread_above_maximum_is_rejected() -> None:
    assert SpreadRiskValidator().validate("20.01", config()) == [
        "Spread exceeds configured maximum"
    ]


def test_daily_loss_limit() -> None:
    reasons = DailyRiskLimiter().validate(state(realized_loss="300"), config())
    assert "Maximum daily loss reached" in reasons


def test_drawdown_limit() -> None:
    reasons = DrawdownLimiter().validate(
        {"equity": "9499"}, state(peak_equity="10000"), config()
    )
    assert reasons == ["Maximum daily drawdown reached"]


def test_consecutive_loss_limit() -> None:
    reasons = ConsecutiveLossLimiter().validate(
        state(consecutive_losses=3), config(), NOW
    )
    assert "Maximum consecutive losses reached" in reasons


def test_maximum_trades_limit() -> None:
    reasons = DailyRiskLimiter().validate(state(trades_count=5), config())
    assert "Maximum trades per day reached" in reasons


def test_maximum_open_positions_limit() -> None:
    reasons = RiskManager().validate_locks(
        {"equity": "10000"}, state(open_positions=1), config(), "10", NOW
    )
    assert "Maximum open positions reached" in reasons


def test_cooldown_limit() -> None:
    reasons = ConsecutiveLossLimiter().validate(
        state(cooldown_until=NOW + timedelta(minutes=1)), config(), NOW
    )
    assert reasons == ["Loss cooldown is active"]


def test_valid_trade_is_approved_with_expected_math() -> None:
    risk_config = config()
    symbol = spec()
    position = PositionSizeCalculator().calculate(
        "10000", "10000", "1.50", risk_config, symbol
    )
    stop = StopLossCalculator().calculate(
        "BUY", "100", "1", risk_config, symbol
    )
    target = TakeProfitCalculator().calculate(
        "BUY", "100", stop["stop_loss"], risk_config, symbol
    )
    locks = RiskManager().validate_locks(
        {"balance": "10000", "equity": "10000"}, state(), risk_config, "10", NOW
    )
    assert Decimal(str(position["risk_amount"])) == Decimal("100.0")
    assert position["lot_size"] == 0.66
    assert stop["stop_loss"] == 98.5
    assert target["take_profit"] == 103.0
    assert target["risk_reward"] == 2.0
    assert RiskRewardValidator().validate(
        "BUY", "100", stop["stop_loss"], target["take_profit"], risk_config
    ) == []
    assert locks == []
