import asyncio
import csv
from dataclasses import replace
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.analysis.exceptions import AnalysisError
from app.analysis.types import AnalysisConfig
from app.backtest.exceptions import BacktestRiskRejected, HistoricalDataError
from app.backtest.execution import BacktestExecutionSimulator
from app.backtest.historical import HistoricalDataService, TIMEFRAME_SECONDS
from app.backtest.position import BacktestPositionManager
from app.backtest.report import BacktestReportService
from app.backtest.repository import BacktestRepository
from app.backtest.risk import BacktestRiskManager
from app.backtest.strategy import BacktestStrategyRunner
from app.backtest.types import BacktestCandle, BacktestConfig, deterministic_id
from app.config.settings import Settings
from app.mt5.manager import MT5ConnectionManager
from app.paper.pnl import PaperPnLCalculator

CSV_EXPORT_COLUMNS = (
    "trade_id", "direction", "entry_time", "exit_time", "entry_price",
    "exit_price", "stop_loss", "take_profit", "volume",
    "gross_profit_loss", "commission", "swap", "net_profit_loss",
    "close_reason", "signal_id", "trade_plan_id",
)


class BacktestEngine:
    """Persist and run isolated backtests; MT5 access is historical and read-only."""

    def __init__(
        self,
        repository: BacktestRepository,
        manager: MT5ConnectionManager,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.manager = manager
        self.settings = settings
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_requests: set[str] = set()

    @property
    def active_tasks(self) -> dict[str, asyncio.Task[None]]:
        return dict(self._tasks)

    async def submit(self, configuration: dict[str, Any]) -> dict[str, Any]:
        job = await self.repository.create(configuration)
        identifier = job["backtest_id"]
        task = asyncio.create_task(self.run(identifier, configuration))
        self._tasks[identifier] = task
        task.add_done_callback(lambda _: self._forget(identifier))
        return job

    def _forget(self, backtest_id: str) -> None:
        self._tasks.pop(backtest_id, None)
        self._cancel_requests.discard(backtest_id)

    async def cancel(self, backtest_id: str) -> dict[str, Any]:
        self._cancel_requests.add(backtest_id)
        return await self.repository.request_cancel(backtest_id)

    async def shutdown(self) -> None:
        identifiers = list(self._tasks)
        self._cancel_requests.update(identifiers)
        for identifier in identifiers:
            await self.repository.request_cancel(identifier)
        if self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def run(self, backtest_id: str, configuration: dict[str, Any]) -> None:
        processed = 0
        try:
            if (
                backtest_id in self._cancel_requests
                or await self.repository.cancel_requested(backtest_id)
            ):
                return
            historical, spreads, warnings, specification = await self._historical(configuration)
            candles = historical.candles("M5")
            await self.repository.mark_running(backtest_id, len(candles), specification)
            for warning in warnings:
                await self.repository.add_event(
                    backtest_id, "WARNING", "HISTORICAL_GAP", warning
                )
            if (
                backtest_id in self._cancel_requests
                or await self.repository.cancel_requested(backtest_id)
            ):
                await self.repository.finish(backtest_id, "CANCELLED")
                return
            result = await self._simulate(
                backtest_id, configuration, historical, candles, spreads, warnings,
                specification,
            )
            processed = result["processed"]
            await self.repository.save_results(
                backtest_id,
                result["positions"],
                result["trades"],
                result["snapshots"],
                result["report"],
                result["warnings"],
            )
            status = (
                "CANCELLED"
                if result["cancelled"]
                or backtest_id in self._cancel_requests
                or await self.repository.cancel_requested(backtest_id)
                else "COMPLETED"
            )
            await self.repository.finish(backtest_id, status, processed=processed)
            await self.repository.add_event(
                backtest_id, "INFO", status, f"Backtest {status.lower()}"
            )
        except Exception as error:
            await self.repository.finish(
                backtest_id, "FAILED", error=str(error), processed=processed
            )
            await self.repository.add_event(
                backtest_id, "ERROR", "FAILED", str(error)
            )

    async def _historical(
        self, configuration: dict[str, Any]
    ) -> tuple[
        HistoricalDataService,
        dict[datetime, float],
        list[str],
        dict[str, Any],
    ]:
        snapshot = await self.manager.risk_snapshot(configuration["symbol"])
        specification = dict(snapshot["symbol"])
        service = HistoricalDataService()
        spreads: dict[datetime, float] = {}
        warnings: list[str] = []
        if configuration["source"] == "CSV":
            start = self._instant(configuration["start_date"], end=False)
            end = self._instant(configuration["end_date"], end=True)
            all_rows = self._csv_rows(configuration["csv_path"])
            rows = self._closed_rows(all_rows, "M5", start, end)
            candles = service.load(rows, "M5", validate_gaps=False)
            spreads = self._spread_map(rows, "M5")
            warnings.extend(self._gap_warnings(candles, "M5"))
            service.candles("M15")
            service.candles("H1")
        else:
            start = self._instant(configuration["start_date"], end=False)
            end = self._instant(configuration["end_date"], end=True)
            for timeframe in ("H1", "M15", "M5"):
                _, _, rates = await self.manager.market_rates(
                    configuration["symbol"], timeframe, start, end, 1_000_000
                )
                rows = self._closed_rows(
                    self._rate_rows(rates), timeframe, start, end
                )
                candles = service.load(rows, timeframe, validate_gaps=False)
                warnings.extend(self._gap_warnings(candles, timeframe))
                if timeframe == "M5":
                    spreads = self._spread_map(rows, timeframe)
        if configuration["spread_mode"] == "HISTORICAL":
            missing = [item.timestamp for item in service.candles("M5") if item.timestamp not in spreads]
            if missing:
                raise HistoricalDataError("historical spread is missing for one or more M5 candles")
        return service, spreads, warnings, specification

    async def _simulate(
        self,
        backtest_id: str,
        configuration: dict[str, Any],
        historical: HistoricalDataService,
        candles: list[BacktestCandle],
        spreads: dict[datetime, float],
        warnings: list[str],
        specification: dict[str, Any],
    ) -> dict[str, Any]:
        domain_config = self._domain_config(configuration, specification)
        risk = BacktestRiskManager(domain_config)
        positions = BacktestPositionManager(BacktestExecutionSimulator(domain_config))
        analysis_overrides = dict(configuration.get("strategy_settings") or {})
        analysis_overrides["strategy_name"] = configuration["strategy_name"]
        analysis = AnalysisConfig.from_settings(self.settings, analysis_overrides)
        strategy = BacktestStrategyRunner(
            historical, analysis_config=analysis, symbol=configuration["symbol"]
        )
        pending: list[dict[str, Any]] = []
        entry_reasons: list[dict[str, Any]] = []
        rejection_reasons: list[dict[str, Any]] = []
        all_positions: dict[str, dict[str, Any]] = {}
        trades: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        warning_set = set(warnings)
        cancelled = False
        processed = 0

        for index, candle in enumerate(candles):
            if (
                backtest_id in self._cancel_requests
                or await self.repository.cancel_requested(backtest_id)
            ):
                cancelled = True
                break
            current = self._with_spread(domain_config, configuration, spreads, candle)
            positions.execution.config = current
            for signal in pending:
                try:
                    if candle.timestamp != signal["decision_time"]:
                        raise BacktestRiskRejected(
                            "Immediate next M5 entry candle is missing"
                        )
                    entry_price = positions.execution.executable_entry_price(
                        signal["direction"], candle
                    )
                    plan = risk.create_trade_plan(
                        signal,
                        entry_price=entry_price,
                        atr=signal["atr"],
                        decision_time=signal["decision_time"],
                        spread_points=current.spread_points,
                    )
                    opened = positions.open(plan, candle)
                    all_positions[opened["position_id"]] = dict(opened)
                    reason = {
                        "signal_id": signal["signal_id"],
                        "trade_plan_id": plan["trade_plan_id"],
                        "reasons": list(signal.get("reasons") or []),
                    }
                    entry_reasons.append(reason)
                    await self.repository.add_event(
                        backtest_id, "INFO", "ENTRY_APPROVED",
                        "Trade entry approved", reason,
                    )
                except BacktestRiskRejected as error:
                    rejection = {
                        "source": "RISK",
                        "signal_id": signal.get("signal_id"),
                        "reasons": str(error).split("; "),
                    }
                    rejection_reasons.append(rejection)
                    await self.repository.add_event(
                        backtest_id, "INFO", "RISK_REJECTION",
                        str(error), rejection,
                    )
            pending = []
            for trade in positions.process_bar(candle):
                self._apply_swap(trade, configuration)
                risk.record_close(trade)
                trades.append(trade)
                all_positions[trade["position_id"]] = dict(trade)
            floating = positions.floating_pnl(candle)
            equity = risk.state_manager.mark_to_market(floating)
            snapshots.append(self._snapshot(
                risk.state.balance, equity, floating, risk.state.peak_equity, candle
            ))
            processed = index + 1
            await self.repository.update_progress(
                backtest_id, processed, len(candles), candle.close_time
            )

            if index + 1 < len(candles) and self._in_session(
                candle.close_time, configuration.get("trading_sessions") or []
            ):
                try:
                    signal = strategy.evaluate(
                        candle.close_time,
                        spread_points=float(current.spread_points),
                    )
                    if signal.get("direction") in {"BUY", "SELL"}:
                        pending.append(signal)
                    elif signal.get("rejection_reasons"):
                        rejection = {
                            "source": "STRATEGY",
                            "signal_id": signal.get("signal_id"),
                            "reasons": list(signal["rejection_reasons"]),
                        }
                        rejection_reasons.append(rejection)
                        await self.repository.add_event(
                            backtest_id, "INFO", "STRATEGY_REJECTION",
                            "Strategy rules did not produce an entry", rejection,
                        )
                except (AnalysisError, IndexError, TypeError, ValueError) as error:
                    warning = f"Strategy skipped insufficient/invalid data: {error}"
                    warning_set.add(warning)
                    rejection = {
                        "source": "STRATEGY",
                        "signal_id": None,
                        "reasons": [str(error)],
                    }
                    rejection_reasons.append(rejection)
                    await self.repository.add_event(
                        backtest_id, "WARNING", "STRATEGY_REJECTION",
                        warning, rejection,
                    )
            await asyncio.sleep(0)

        if positions.positions and configuration["close_open_positions_at_end"]:
            final_candle = candles[max(processed - 1, 0)]
            for trade in positions.close_all(final_candle):
                self._apply_swap(trade, configuration)
                risk.record_close(trade)
                trades.append(trade)
                all_positions[trade["position_id"]] = dict(trade)
            floating = Decimal("0")
            equity = risk.state_manager.mark_to_market(floating)
            if snapshots:
                snapshots[-1] = self._snapshot(
                    risk.state.balance,
                    equity,
                    floating,
                    risk.state.peak_equity,
                    final_candle,
                )

        report = BacktestReportService().generate(
            trades,
            configuration["initial_balance"],
            metadata={
                "symbol": specification.get("name", configuration["symbol"]),
                "source": configuration["source"],
                "strategy_name": configuration["strategy_name"],
                "same_bar_policy": configuration["same_bar_policy"],
                "configuration": {
                    key: value for key, value in configuration.items()
                    if key != "csv_path"
                },
                "symbol_specification": specification,
                "symbol_specification_assumption": (
                    "Current MT5 demo symbol specification is applied to the historical period"
                ),
            },
            entry_reasons=entry_reasons,
            rejection_reasons=rejection_reasons,
            warnings=warning_set,
            equity_points=snapshots,
        )
        warning_set.update(report["warnings"])
        return {
            "processed": processed,
            "cancelled": cancelled,
            "positions": [self._position(item) for item in all_positions.values()],
            "trades": [self._trade(item) for item in trades],
            "snapshots": snapshots,
            "report": report,
            "warnings": sorted(warning_set),
        }

    def _domain_config(
        self, configuration: dict[str, Any], specification: dict[str, Any]
    ) -> BacktestConfig:
        risk = configuration.get("risk_settings") or {}
        target_rr = max(
            float(configuration["minimum_risk_reward"]),
            float(risk.get("target_risk_reward", configuration["minimum_risk_reward"])),
        )
        return BacktestConfig(
            initial_balance=configuration["initial_balance"],
            point=specification["point"],
            spread_points=configuration["fixed_spread_points"],
            slippage_points=configuration["slippage_points"],
            tick_size=specification["trade_tick_size"],
            tick_value=specification["trade_tick_value"],
            commission_per_lot=configuration["commission_per_lot"],
            swap_long_per_lot=configuration["swap_long_per_lot"],
            swap_short_per_lot=configuration["swap_short_per_lot"],
            risk_per_trade_percent=configuration["risk_per_trade_percent"],
            minimum_risk_reward=configuration["minimum_risk_reward"],
            maximum_spread_points=risk.get(
                "maximum_spread_points", self.settings.risk_maximum_spread_points
            ),
            stop_atr_multiplier=risk.get("atr_multiplier", self.settings.risk_atr_multiplier),
            target_risk_reward=target_rr,
            use_equity_for_risk=risk.get(
                "use_equity_for_risk", self.settings.risk_use_equity_for_risk
            ),
            stop_loss_method=risk.get(
                "stop_loss_method", self.settings.risk_stop_loss_method
            ),
            max_daily_loss_percent=risk.get(
                "max_daily_loss_percent", self.settings.risk_max_daily_loss_percent
            ),
            max_daily_drawdown_percent=risk.get(
                "max_daily_drawdown_percent",
                self.settings.risk_max_daily_drawdown_percent,
            ),
            max_trades_per_day=risk.get(
                "max_trades_per_day", self.settings.risk_max_trades_per_day
            ),
            max_consecutive_losses=risk.get(
                "max_consecutive_losses", self.settings.risk_max_consecutive_losses
            ),
            max_open_positions=configuration["maximum_open_positions"],
            cooldown_minutes_after_loss=risk.get(
                "cooldown_minutes_after_loss",
                self.settings.risk_cooldown_minutes_after_loss,
            ),
            volume_min=specification["volume_min"],
            volume_max=specification["volume_max"],
            volume_step=specification["volume_step"],
            trade_stops_level=specification.get("trade_stops_level") or 0,
            trade_freeze_level=specification.get("trade_freeze_level") or 0,
            same_bar_policy=configuration["same_bar_policy"],
        )

    @staticmethod
    def _with_spread(
        base: BacktestConfig,
        configuration: dict[str, Any],
        spreads: dict[datetime, float],
        candle: BacktestCandle,
    ) -> BacktestConfig:
        if configuration["spread_mode"] != "HISTORICAL":
            return base
        return replace(base, spread_points=Decimal(str(spreads[candle.timestamp])))

    @staticmethod
    def _apply_swap(trade: dict[str, Any], configuration: dict[str, Any]) -> None:
        swap = PaperPnLCalculator.swap(
            trade["direction"],
            trade["opened_at"],
            trade["closed_at"],
            trade["volume"],
            configuration["swap_long_per_lot"],
            configuration["swap_short_per_lot"],
        )
        trade["swap"] = swap
        trade["net_pnl"] = Decimal(str(trade["net_pnl"])) + swap

    @staticmethod
    def _snapshot(
        balance: Decimal,
        equity: Decimal,
        floating: Decimal,
        peak_equity: Decimal,
        candle: BacktestCandle,
    ) -> dict[str, Any]:
        return {
            "snapshot_id": deterministic_id("equity", candle.close_time),
            "timestamp": candle.close_time,
            "balance": float(balance),
            "equity": float(equity),
            "floating_pnl": float(floating),
            "drawdown": float(max(peak_equity - equity, Decimal("0"))),
        }

    @staticmethod
    def _position(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "position_id": item["position_id"],
            "signal_id": item.get("signal_id"),
            "trade_plan_id": item["trade_plan_id"],
            "symbol": item["symbol"],
            "direction": item["direction"],
            "volume": float(item["volume"]),
            "entry_price": float(item["entry_price"]),
            "stop_loss": float(item["stop_loss"]),
            "take_profit": float(item["take_profit"]),
            "status": item["status"],
            "opened_at": item["opened_at"],
            "closed_at": item.get("closed_at"),
            "exit_price": (
                float(item["exit_price"]) if item.get("exit_price") is not None else None
            ),
            "exit_reason": item.get("exit_reason"),
        }

    @staticmethod
    def _trade(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "trade_id": item["trade_id"],
            "position_id": item["position_id"],
            "signal_id": item.get("signal_id"),
            "trade_plan_id": item["trade_plan_id"],
            "symbol": item["symbol"],
            "direction": item["direction"],
            "volume": float(item["volume"]),
            "entry_price": float(item["entry_price"]),
            "exit_price": float(item["exit_price"]),
            "stop_loss": float(item["stop_loss"]),
            "take_profit": float(item["take_profit"]),
            "gross_pnl": float(item["gross_pnl"]),
            "commission": float(item["commission"]),
            "swap": float(item.get("swap", 0)),
            "net_pnl": float(item["net_pnl"]),
            "exit_reason": item["exit_reason"],
            "opened_at": item["opened_at"],
            "closed_at": item["closed_at"],
        }

    @staticmethod
    def _in_session(when: datetime, sessions: list[dict[str, Any]]) -> bool:
        if not sessions:
            return True
        current = when.time().replace(tzinfo=None)
        for session in sessions:
            if when.weekday() not in session.get("weekdays", [0, 1, 2, 3, 4]):
                continue
            start = time.fromisoformat(str(session["start"]))
            end = time.fromisoformat(str(session["end"]))
            if (start <= current < end) if start < end else (current >= start or current < end):
                return True
        return False

    @staticmethod
    def _instant(value: str | date | datetime, *, end: bool) -> datetime:
        if isinstance(value, str):
            if len(value) == 10 and "T" not in value:
                value = date.fromisoformat(value)
            else:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime):
            return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
        return datetime.combine(value, time.max if end else time.min, timezone.utc)

    @staticmethod
    def _csv_rows(path: str | None) -> list[dict[str, Any]]:
        if not path:
            raise HistoricalDataError("csv_path is required")
        try:
            with Path(path).open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                required = {"timestamp", "open", "high", "low", "close"}
                if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                    raise HistoricalDataError(
                        "CSV requires exact core columns: timestamp,open,high,low,close"
                    )
                return [dict(row) for row in reader]
        except OSError as error:
            raise HistoricalDataError(f"CSV cannot be read: {error}") from error

    @classmethod
    def _rate_rows(cls, rates: object | None) -> list[dict[str, Any]]:
        if rates is None:
            return []
        rows: list[dict[str, Any]] = []
        for source in rates:  # type: ignore[union-attr]
            if isinstance(source, dict):
                row = dict(source)
            elif hasattr(source, "dtype") and getattr(source.dtype, "names", None):
                row = {name: source[name].item() if hasattr(source[name], "item") else source[name] for name in source.dtype.names}
            elif hasattr(source, "_asdict"):
                row = dict(source._asdict())
            else:
                names = ("time", "open", "high", "low", "close", "tick_volume", "spread")
                row = {name: getattr(source, name) for name in names if hasattr(source, name)}
            timestamp = row.get("timestamp", row.get("time"))
            if isinstance(timestamp, (int, float)):
                timestamp = datetime.fromtimestamp(timestamp, timezone.utc)
            row["timestamp"] = timestamp
            row.setdefault("volume", row.get("tick_volume", 0))
            rows.append(row)
        return rows

    @staticmethod
    def _closed_rows(
        rows: list[dict[str, Any]],
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = min(end, now or datetime.now(timezone.utc))
        result: list[dict[str, Any]] = []
        for row in rows:
            candle = HistoricalDataService._candle(row, timeframe)
            if start <= candle.timestamp and candle.close_time <= cutoff:
                result.append(row)
        return result

    @staticmethod
    def _spread_map(rows: list[dict[str, Any]], timeframe: str) -> dict[datetime, float]:
        result: dict[datetime, float] = {}
        for row in rows:
            raw = row.get("spread")
            if raw in (None, ""):
                continue
            try:
                value = float(raw)
                candle = HistoricalDataService._candle(row, timeframe)
            except (TypeError, ValueError) as error:
                raise HistoricalDataError("historical spread is invalid") from error
            if value < 0:
                raise HistoricalDataError("historical spread cannot be negative")
            result[candle.timestamp] = value
        return result

    @staticmethod
    def _gap_warnings(candles: list[BacktestCandle], timeframe: str) -> list[str]:
        warnings: list[str] = []
        expected = TIMEFRAME_SECONDS[timeframe]
        for previous, current in zip(candles, candles[1:], strict=False):
            actual = int((current.timestamp - previous.timestamp).total_seconds())
            if actual != expected:
                warnings.append(
                    f"{timeframe} historical gap: {previous.timestamp.isoformat()} -> "
                    f"{current.timestamp.isoformat()} ({actual} seconds)"
                )
        return warnings
