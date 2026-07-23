from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.backtest import (
    BacktestCandle,
    BacktestConfig,
    BacktestExecutionSimulator,
    BacktestPnLCalculator,
    BacktestPositionManager,
    BacktestReportService,
    BacktestRiskManager,
    BacktestRiskRejected,
    BacktestStateManager,
    BacktestStatisticsService,
    BacktestStrategyRunner,
    DrawdownCalculator,
    EquityCurveService,
    ExitReason,
    HistoricalDataError,
    HistoricalDataService,
    LookAheadError,
)
from app.paper.pnl import PaperPnLCalculator

UTC = timezone.utc
START = datetime(2026, 1, 5, tzinfo=UTC)


def candle(
    minute: int,
    *,
    open_price: float = 100,
    high: float = 101,
    low: float = 99,
    close: float = 100.5,
) -> dict[str, object]:
    return {
        "timestamp": START + timedelta(minutes=minute),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1,
    }


def candles(count: int = 12) -> list[dict[str, object]]:
    return [candle(index * 5) for index in range(count)]


def test_historical_data_rejects_empty_input() -> None:
    with pytest.raises(HistoricalDataError, match="empty"):
        HistoricalDataService().load([])


def test_historical_data_requires_timezone() -> None:
    value = candle(0)
    value["timestamp"] = datetime(2026, 1, 5)
    with pytest.raises(HistoricalDataError, match="invalid fields"):
        HistoricalDataService().load([value])


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ([candle(5), candle(0)], "ascending"),
        ([candle(0), candle(0)], "duplicate"),
        ([candle(0), candle(10)], "gap"),
    ],
)
def test_historical_timestamp_validation(
    values: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(HistoricalDataError, match=message):
        HistoricalDataService().load(values)


def test_historical_ohlc_validation() -> None:
    with pytest.raises(HistoricalDataError, match="high"):
        HistoricalDataService().load(
            [candle(0, open_price=100, high=99, low=98, close=100)]
        )
    with pytest.raises(HistoricalDataError, match="positive"):
        HistoricalDataService().load(
            [candle(0, open_price=0, high=1, low=0, close=1)]
        )


def test_csv_loading_is_optional_standard_library_path(tmp_path) -> None:
    path = tmp_path / "rates.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-05T00:00:00Z,100,101,99,100.5,2\n",
        encoding="utf-8",
    )
    result = HistoricalDataService().load_csv(path)
    assert result[0].timestamp == START
    assert result[0].volume == 2


def test_slice_uses_close_time_and_never_future_open_time() -> None:
    service = HistoricalDataService()
    service.load(candles(4))
    decision = START + timedelta(minutes=10)
    visible = service.slice_at("M5", decision)
    assert [item.timestamp for item in visible] == [
        START,
        START + timedelta(minutes=5),
    ]
    assert all(item.close_time <= decision for item in visible)


def test_m5_aggregation_only_exposes_closed_higher_timeframes() -> None:
    service = HistoricalDataService()
    service.load(candles(12))
    assert len(service.slice_at("M15", START + timedelta(minutes=14))) == 0
    m15 = service.slice_at("M15", START + timedelta(minutes=15))
    assert len(m15) == 1
    assert m15[0].timestamp == START
    assert service.slice_at("H1", START + timedelta(minutes=59)) == []
    assert len(service.slice_at("H1", START + timedelta(hours=1))) == 1


def test_strategy_decides_after_close_and_entry_is_next_m5_open() -> None:
    service = HistoricalDataService()
    service.load(candles(12))
    captured: dict[str, object] = {}

    def strategy(data, decision_time):  # type: ignore[no-untyped-def]
        captured["data"] = data
        return {"symbol": "XAUUSD", "direction": "BUY"}

    runner = BacktestStrategyRunner(service, strategy=strategy)
    decision = START + timedelta(minutes=10)
    signal = runner.evaluate(decision)
    visible = captured["data"]
    assert visible["M5"][-1]["close_time"] == decision  # type: ignore[index]
    assert runner.next_entry_candle(decision).timestamp == decision
    assert signal["decision_time"] == decision


def test_strategy_rejects_non_close_decision_time() -> None:
    service = HistoricalDataService()
    service.load(candles(12))
    runner = BacktestStrategyRunner(service, strategy=lambda data, now: {})
    with pytest.raises(LookAheadError, match="M5 candle close"):
        runner.evaluate(START + timedelta(minutes=7))


def test_signal_ids_are_deterministic() -> None:
    service = HistoricalDataService()
    service.load(candles(12))
    runner = BacktestStrategyRunner(
        service,
        strategy=lambda data, now: {"symbol": "XAUUSD", "direction": "BUY"},
    )
    at = START + timedelta(minutes=10)
    assert runner.evaluate(at)["signal_id"] == runner.evaluate(at)["signal_id"]


def plan(direction: str = "BUY") -> dict[str, object]:
    return {
        "trade_plan_id": "plan-1",
        "signal_id": "signal-1",
        "symbol": "XAUUSD",
        "direction": direction,
        "decision_time": START + timedelta(minutes=5),
        "stop_loss": "99" if direction == "BUY" else "101.02",
        "take_profit": "101" if direction == "BUY" else "99.02",
        "volume": "1",
    }


def bar(
    minute: int,
    *,
    open_price: float = 100,
    high: float = 100.5,
    low: float = 99.5,
    close: float = 100,
) -> BacktestCandle:
    return BacktestCandle(
        timestamp=START + timedelta(minutes=minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
    )


def test_historical_prices_are_bid_and_entry_uses_ask_plus_slippage() -> None:
    simulator = BacktestExecutionSimulator(
        BacktestConfig(point="0.01", spread_points="2", slippage_points="1")
    )
    position = simulator.execute_entry(plan(), bar(5))
    assert simulator.quote("100") == (Decimal("100"), Decimal("100.02"))
    assert position["entry_price"] == Decimal("100.03")


def test_sell_entry_uses_bid_minus_adverse_slippage() -> None:
    simulator = BacktestExecutionSimulator(
        BacktestConfig(point="0.01", spread_points="2", slippage_points="1")
    )
    position = simulator.execute_entry(plan("SELL"), bar(5))
    assert position["entry_price"] == Decimal("99.99")


def test_same_bar_stop_and_target_defaults_to_stop_first() -> None:
    simulator = BacktestExecutionSimulator(BacktestConfig(spread_points="2"))
    position = simulator.execute_entry(plan(), bar(5))
    trigger = simulator.exit_trigger(position, bar(10, high=102, low=98))
    assert trigger is ExitReason.STOP_LOSS


def test_same_bar_policy_can_be_explicitly_tp_first() -> None:
    simulator = BacktestExecutionSimulator(
        BacktestConfig(spread_points="2", same_bar_policy="TP_FIRST")
    )
    position = simulator.execute_entry(plan(), bar(5))
    assert simulator.exit_trigger(
        position, bar(10, high=102, low=98)
    ) is ExitReason.TAKE_PROFIT


def test_sell_protective_levels_are_evaluated_on_ask_ohlc() -> None:
    simulator = BacktestExecutionSimulator(
        BacktestConfig(point="0.01", spread_points="2")
    )
    position = simulator.execute_entry(plan("SELL"), bar(5))
    assert simulator.exit_trigger(
        position, bar(10, high=101, low=100)
    ) is ExitReason.STOP_LOSS


def test_gap_stop_and_exit_slippage_are_adverse() -> None:
    simulator = BacktestExecutionSimulator(
        BacktestConfig(point="0.01", spread_points="2", slippage_points="1")
    )
    position = simulator.execute_entry(plan(), bar(5))
    exit_bar = bar(10, open_price=98, high=98.5, low=97.5, close=98)
    trade = simulator.execute_exit(position, exit_bar, ExitReason.STOP_LOSS)
    assert trade["exit_price"] == Decimal("97.99")
    assert trade["net_pnl"] < 0


def test_position_manager_opens_closes_and_calculates_floating_pnl() -> None:
    manager = BacktestPositionManager(
        BacktestExecutionSimulator(BacktestConfig(spread_points="0"))
    )
    position = manager.open(plan(), bar(5))
    assert manager.floating_pnl(bar(10, close=100.5)) == Decimal("50")
    closed = manager.process_bar(bar(10, high=101.5, low=99.5))
    assert closed[0]["position_id"] == position["position_id"]
    assert not manager.positions


def test_backtest_pnl_reuses_pure_paper_calculator() -> None:
    calculator = BacktestPnLCalculator()
    assert isinstance(calculator, PaperPnLCalculator)
    assert calculator.gross_pnl(
        "BUY", "100", "101", "1", "0.01", "1"
    ) == Decimal("100")


def test_risk_deduplicates_and_enforces_open_position_limit() -> None:
    manager = BacktestRiskManager(BacktestConfig(max_open_positions=2))
    manager.approve("one", START)
    with pytest.raises(BacktestRiskRejected, match="already processed"):
        manager.approve("one", START)
    manager.approve("two", START)
    assert "Maximum open positions reached" in manager.validate("three", START)


def test_risk_plan_reuses_position_size_calculator() -> None:
    manager = BacktestRiskManager(BacktestConfig(spread_points="2"))
    result = manager.create_trade_plan(
        {"signal_id": "signal", "direction": "BUY", "symbol": "XAUUSD"},
        entry_price="100",
        atr="1",
        decision_time=START,
    )
    assert result["stop_loss"] == Decimal("98.5")
    assert result["take_profit"] == Decimal("103.0")
    assert result["risk_amount"] == Decimal("100.0")
    actual_entry_manager = BacktestRiskManager(BacktestConfig(spread_points="2"))
    plan = actual_entry_manager.create_trade_plan(
        {"signal_id": "signal", "direction": "BUY", "symbol": "XAUUSD"},
        entry_price="100.03",
        atr="1",
        decision_time=START,
    )
    assert plan["executable_entry_price"] == Decimal("100.03")
    assert plan["stop_loss"] == Decimal("98.53")
    simulator = BacktestExecutionSimulator(
        BacktestConfig(point="0.01", spread_points="2", slippage_points="1")
    )
    position = simulator.execute_entry(plan, bar(0))
    assert position["entry_price"] == Decimal("100.03")


def test_daily_loss_limit_and_next_day_reset() -> None:
    state = BacktestStateManager("10000")
    manager = BacktestRiskManager(
        BacktestConfig(max_daily_loss_percent="1"), state
    )
    manager.approve("one", START)
    manager.record_close({"closed_at": START, "net_pnl": "-100"})
    assert "Maximum daily loss reached" in manager.validate("two", START)
    tomorrow = START + timedelta(days=1)
    assert "Maximum daily loss reached" not in manager.validate("two", tomorrow)


def test_equity_drawdown_statistics_and_report() -> None:
    trades = [
        {"trade_id": "1", "closed_at": START, "net_pnl": Decimal("100")},
        {
            "trade_id": "2",
            "closed_at": START + timedelta(hours=1),
            "net_pnl": Decimal("-120"),
        },
        {
            "trade_id": "3",
            "closed_at": START + timedelta(hours=2),
            "net_pnl": Decimal("20"),
        },
    ]
    curve = EquityCurveService().build(trades, "1000")
    drawdown = DrawdownCalculator().calculate(curve)
    stats = BacktestStatisticsService().calculate(trades, "1000", curve)
    assert [point.equity for point in curve] == [
        Decimal("1000"), Decimal("1100"), Decimal("980"), Decimal("1000")
    ]
    assert drawdown.max_drawdown == Decimal("120")
    assert stats["total_trades"] == 3
    assert stats["net_profit"] == Decimal("0")
    assert stats["consecutive_losses"] == 1

    report = BacktestReportService().generate(
        trades, "1000", metadata={"symbol": "XAUUSD"}
    )
    assert report["metadata"] == {"symbol": "XAUUSD"}
    assert report["statistics"]["final_balance"] == 1000.0
    assert report["equity_curve"][0]["timestamp"].endswith("+00:00")


def test_strategy_uses_trailing_candle_count_and_actual_symbol() -> None:
    from types import SimpleNamespace

    service = HistoricalDataService()
    service.load(candles(12))
    captured: dict[str, object] = {}

    def strategy(data, decision_time):  # type: ignore[no-untyped-def]
        captured["data"] = data
        return {"direction": "HOLD"}

    runner = BacktestStrategyRunner(
        service,
        analysis_config=SimpleNamespace(candle_count=2),
        strategy=strategy,
        symbol="EURUSD",
    )
    result = runner.evaluate(START + timedelta(hours=1))
    data = captured["data"]
    assert len(data["M5"]) == 2  # type: ignore[index]
    assert len(data["M15"]) == 2  # type: ignore[index]
    assert result["symbol"] == "EURUSD"


def test_closed_row_filter_excludes_active_future_and_out_of_range() -> None:
    from app.backtest.engine import BacktestEngine

    rows = [candle(-5), candle(0), candle(5), candle(10)]
    result = BacktestEngine._closed_rows(
        rows,
        "M5",
        START,
        START + timedelta(minutes=20),
        now=START + timedelta(minutes=12),
    )
    assert [item["timestamp"] for item in result] == [
        START,
        START + timedelta(minutes=5),
    ]


def test_backtest_swap_uses_directional_rates_and_calendar_days() -> None:
    from app.backtest.engine import BacktestEngine

    base = {
        "opened_at": START,
        "closed_at": START + timedelta(days=2),
        "volume": Decimal("2"),
        "net_pnl": Decimal("10"),
    }
    configuration = {
        "swap_long_per_lot": Decimal("-1"),
        "swap_short_per_lot": Decimal("-3"),
    }
    long_trade = {**base, "direction": "BUY"}
    short_trade = {**base, "direction": "SELL"}
    BacktestEngine._apply_swap(long_trade, configuration)
    BacktestEngine._apply_swap(short_trade, configuration)
    assert long_trade["swap"] == Decimal("-4")
    assert long_trade["net_pnl"] == Decimal("6")
    assert short_trade["swap"] == Decimal("-12")
    assert short_trade["net_pnl"] == Decimal("-2")


def test_full_statistics_contract_is_numeric_except_nullable_sharpe() -> None:
    trades = [{
        "trade_id": "1",
        "closed_at": START,
        "net_pnl": Decimal("100"),
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("99"),
        "take_profit": Decimal("102"),
    }]
    stats = BacktestStatisticsService().calculate(trades, "1000")
    required = {
        "final_balance", "net_profit", "total_return_percent", "total_trades",
        "winning_trades", "losing_trades", "win_rate", "gross_profit",
        "gross_loss", "profit_factor", "expectancy", "average_win",
        "average_loss", "maximum_drawdown", "maximum_drawdown_percent",
        "consecutive_wins", "consecutive_losses", "average_risk_reward",
        "sharpe_ratio",
    }
    assert set(stats) == required
    assert stats["profit_factor"] == Decimal("0")
    assert stats["average_risk_reward"] == Decimal("2")
    assert stats["sharpe_ratio"] is None
    report = BacktestReportService().generate(trades, "1000")
    assert required == set(report["performance"])
    assert report["warnings"]
    assert "Past performance" in report["disclaimer"]


def test_backtest_risk_uses_shared_stop_target_and_broker_distances() -> None:
    from app.risk.calculators import StopLossCalculator, TakeProfitCalculator
    from app.risk.types import RiskConfig, SymbolSpecification

    config = BacktestConfig(
        point="0.01", tick_size="0.1", trade_stops_level="200",
        stop_atr_multiplier="1.5", target_risk_reward="2",
    )
    manager = BacktestRiskManager(config)
    plan_result = manager.create_trade_plan(
        {"signal_id": "shared-risk", "direction": "BUY", "symbol": "XAUUSD"},
        entry_price="100", atr="1.03", decision_time=START,
    )
    shared_config = RiskConfig(
        maximum_spread_points=config.maximum_spread_points,
        atr_multiplier=config.stop_atr_multiplier,
        target_risk_reward=config.target_risk_reward,
        session_enabled=False,
    )
    specification = SymbolSpecification(
        digits=2, point=config.point, trade_tick_size=config.tick_size,
        trade_tick_value=config.tick_value, volume_min=config.volume_min,
        volume_max=config.volume_max, volume_step=config.volume_step,
        trade_stops_level=config.trade_stops_level,
        trade_freeze_level=config.trade_freeze_level,
    )
    stop = StopLossCalculator().calculate(
        "BUY", "100", "1.03", shared_config, specification
    )
    target = TakeProfitCalculator().calculate(
        "BUY", "100", stop["stop_loss"], shared_config, specification
    )
    assert plan_result["stop_loss"] == Decimal(str(stop["stop_loss"]))
    assert plan_result["take_profit"] == Decimal(str(target["take_profit"]))


def test_backtest_risk_rejects_spread_above_shared_limit() -> None:
    manager = BacktestRiskManager(
        BacktestConfig(spread_points="3", maximum_spread_points="2")
    )
    with pytest.raises(BacktestRiskRejected, match="Spread exceeds configured maximum"):
        manager.create_trade_plan(
            {"signal_id": "wide-spread", "direction": "BUY"},
            entry_price="100", atr="1", decision_time=START, spread_points="3",
        )


def test_daily_drawdown_uses_daily_peak_not_all_time_peak() -> None:
    state = BacktestStateManager("10000")
    manager = BacktestRiskManager(
        BacktestConfig(max_daily_drawdown_percent="5"), state
    )
    manager.validate("day-one", START)
    state.mark_to_market("1000")
    state.mark_to_market("0")
    tomorrow = START + timedelta(days=1)
    manager.validate("day-two", tomorrow)
    state.mark_to_market("500")
    state.mark_to_market("0")
    assert state.state.peak_equity == Decimal("11000")
    assert state.state.day_peak_equity == Decimal("10500")
    assert "Maximum daily drawdown reached" not in manager.validate(
        "within-daily-limit", tomorrow
    )
    state.mark_to_market("-100")
    assert "Maximum daily drawdown reached" in manager.validate(
        "over-daily-limit", tomorrow
    )


def test_floating_equity_includes_commission_and_accrued_swap() -> None:
    config = BacktestConfig(
        spread_points="0", commission_per_lot="2", swap_long_per_lot="-3"
    )
    manager = BacktestPositionManager(BacktestExecutionSimulator(config))
    manager.open(plan(), bar(5))
    next_day = bar(5 + 24 * 60, close=100.5)
    assert manager.floating_pnl(next_day) == Decimal("45")


def test_strategy_applies_shared_hard_spread_rejection(monkeypatch) -> None:
    from types import SimpleNamespace

    service = HistoricalDataService()
    service.load(candles(48))
    config = SimpleNamespace(
        candle_count=100, ema_slow_period=2, ema_fast_period=1,
        rsi_period=1, atr_period=1, structure_lookback=4,
        candle_body_atr_min=0, candle_close_location_min=0,
        max_spread_points=2, rsi_overbought=70, rsi_oversold=30,
        strategy_name="EMA_RSI_ATR_MTF_V1",
    )
    runner = BacktestStrategyRunner(service, analysis_config=config)
    monkeypatch.setattr(
        runner._indicator,
        "analyze",
        lambda symbol, timeframe, values, settings: {
            "ema_fast": 2, "ema_slow": 1, "ema_fast_previous": 1,
            "ema_slow_previous": 1, "rsi": 50, "atr": 1,
            "market_structure": "BULLISH", "data_valid": True,
            "close": values[-1]["close"], "candle_time": values[-1]["timestamp"],
        },
    )
    monkeypatch.setattr(runner._confirmation, "detect", lambda *args: "BULLISH")
    result = runner.evaluate(START + timedelta(hours=4), spread_points=3)
    assert result["direction"] == "HOLD"
    assert "Spread exceeds configured maximum" in result["rejection_reasons"]
