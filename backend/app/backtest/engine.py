import asyncio
import math
import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from time import monotonic
from typing import Any

from app.analysis.exceptions import AnalysisError
from app.analysis.types import AnalysisConfig
from app.backtest.exceptions import (
    BacktestAdmissionClosedError,
    BacktestQueueFullError,
    BacktestRiskRejected,
    BacktestValidationError,
    HistoricalDataError,
)
from app.backtest.execution import BacktestExecutionSimulator
from app.backtest.historical import HistoricalDataService, TIMEFRAME_SECONDS
from app.backtest.position import BacktestPositionManager
from app.backtest.report import BacktestReportService
from app.backtest.repository import BacktestRepository
from app.backtest.risk import BacktestRiskManager
from app.backtest.strategy import BacktestStrategyRunner
from app.backtest.types import BacktestCandle, BacktestConfig, deterministic_id
from app.backtest.uploads import BacktestUploadStore
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
        upload_store: BacktestUploadStore | None = None,
    ) -> None:
        self.repository = repository
        self.manager = manager
        self.settings = settings
        self.upload_store = upload_store or BacktestUploadStore(settings)
        capacity = settings.max_backtest_jobs + settings.max_pending_jobs
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(
            maxsize=capacity
        )
        self._workers: list[asyncio.Task[None]] = []
        self._completion: dict[str, asyncio.Future[None]] = {}
        self._pending_ids: list[str] = []
        self._running: dict[str, dict[str, Any]] = {}
        self._estimates: dict[str, dict[str, Any]] = {}
        self._upload_owners: dict[str, str] = {}
        self._cancel_requests: set[str] = set()
        self._admission_lock = asyncio.Lock()
        self._started = False
        self._accepting = False

    @property
    def active_tasks(self) -> dict[str, asyncio.Future[None]]:
        return dict(self._completion)

    async def start(self) -> None:
        if self._started:
            return
        async with self._admission_lock:
            if self._started:
                return
            self._accepting = False
            stale = await self.repository.jobs_by_status("RUNNING")
            pending = await self.repository.jobs_by_status("PENDING")
            pending_upload_ids = {
                str(item["configuration"].get("csv_upload_id"))
                for item in pending
                if item["configuration"].get("csv_upload_id")
            }
            await self.repository.fail_jobs(
                [item["backtest_id"] for item in stale], "SERVER_RESTARTED"
            )
            for item in stale:
                upload_id = item["configuration"].get("csv_upload_id")
                if not upload_id or upload_id not in pending_upload_ids:
                    self._cleanup_upload(item["configuration"])

            capacity = self.settings.max_backtest_jobs + self.settings.max_pending_jobs
            memory_budget = self.settings.max_memory_budget_mb * 1024 * 1024
            admitted_memory = 0
            active_upload_ids: set[str] = set()
            for item in pending:
                identifier = item["backtest_id"]
                configuration = item["configuration"]
                try:
                    estimate = self._preflight(configuration)
                except BacktestValidationError:
                    await self.repository.fail_jobs([identifier], "RECOVERY_INPUT_INVALID")
                    self._cleanup_unclaimed_upload(configuration)
                    continue
                upload_id = estimate.get("upload_id")
                if upload_id and str(upload_id) in self._upload_owners:
                    await self.repository.fail_jobs([identifier], "RECOVERY_UPLOAD_REUSED")
                    continue
                if admitted_memory + estimate["estimated_memory_bytes"] > memory_budget:
                    await self.repository.fail_jobs([identifier], "RECOVERY_MEMORY_OVERFLOW")
                    self._cleanup_unclaimed_upload(configuration)
                    continue
                if len(self._pending_ids) >= capacity:
                    await self.repository.fail_jobs([identifier], "RECOVERY_QUEUE_OVERFLOW")
                    self._cleanup_unclaimed_upload(configuration)
                    continue
                self._pending_ids.append(identifier)
                self._estimates[identifier] = estimate
                self._completion[identifier] = asyncio.get_running_loop().create_future()
                self._queue.put_nowait((identifier, configuration))
                if upload_id:
                    normalized_upload_id = str(upload_id)
                    self._upload_owners[normalized_upload_id] = identifier
                    active_upload_ids.add(normalized_upload_id)
                admitted_memory += estimate["estimated_memory_bytes"]
            self.upload_store.cleanup_orphans(active_upload_ids)
            self._workers = [
                asyncio.create_task(self._worker(index), name=f"backtest-worker-{index}")
                for index in range(self.settings.max_backtest_jobs)
            ]
            self._started = True
            self._accepting = True

    async def submit(self, configuration: dict[str, Any]) -> dict[str, Any]:
        await self.start()
        try:
            estimate = self._preflight(configuration)
        except BacktestValidationError:
            self._cleanup_unclaimed_upload(configuration)
            raise
        async with self._admission_lock:
            upload_id = str(estimate.get("upload_id") or "")
            if upload_id and upload_id in self._upload_owners:
                raise BacktestValidationError(
                    "csv_upload_id is already assigned to another backtest"
                )
            if not self._accepting:
                self._cleanup_unclaimed_upload(configuration)
                raise BacktestAdmissionClosedError(
                    "Backtest admission is closed during server shutdown"
                )
            capacity = self.settings.max_backtest_jobs + self.settings.max_pending_jobs
            if len(self._running) + len(self._pending_ids) >= capacity:
                self._cleanup_unclaimed_upload(configuration)
                raise BacktestQueueFullError(
                    "Backtest queue is full: running and pending capacity is exhausted"
                )
            memory_budget = self.settings.max_memory_budget_mb * 1024 * 1024
            reserved_memory = sum(
                item["estimated_memory_bytes"] for item in self._estimates.values()
            )
            if reserved_memory + estimate["estimated_memory_bytes"] > memory_budget:
                self._cleanup_unclaimed_upload(configuration)
                raise BacktestValidationError(
                    "aggregate estimated memory exceeds max_memory_budget_mb"
                )
            try:
                job = await self.repository.create(configuration)
            except Exception:
                self._cleanup_unclaimed_upload(configuration)
                raise
            identifier = job["backtest_id"]
            self._pending_ids.append(identifier)
            self._estimates[identifier] = estimate
            self._completion[identifier] = asyncio.get_running_loop().create_future()
            if upload_id:
                self._upload_owners[upload_id] = identifier
            self._queue.put_nowait((identifier, configuration))
            return job

    async def _worker(self, _: int) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            identifier, configuration = item
            async with self._admission_lock:
                should_run = identifier in self._pending_ids
                if should_run:
                    self._pending_ids.remove(identifier)
                    self._running[identifier] = configuration
            if not should_run:
                self._queue.task_done()
                continue
            try:
                await asyncio.wait_for(
                    self.run(identifier, configuration),
                    timeout=self.settings.job_timeout_minutes * 60,
                )
            except asyncio.TimeoutError:
                await self._fail_best_effort(identifier, "JOB_TIMEOUT")
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._fail_best_effort(identifier, "WORKER_FAILURE")
            finally:
                async with self._admission_lock:
                    self._running.pop(identifier, None)
                    self._estimates.pop(identifier, None)
                    self._cancel_requests.discard(identifier)
                    self._release_upload(identifier, configuration)
                    completion = self._completion.pop(identifier, None)
                    if completion is not None and not completion.done():
                        completion.set_result(None)
                self._queue.task_done()

    async def _fail_best_effort(self, identifier: str, reason: str) -> None:
        try:
            await self.repository.finish(identifier, "FAILED", error=reason)
        except Exception:
            pass
        try:
            await self.repository.add_event(identifier, "ERROR", reason, reason)
        except Exception:
            pass

    async def cancel(self, backtest_id: str) -> dict[str, Any]:
        async with self._admission_lock:
            self._cancel_requests.add(backtest_id)
            result = await self.repository.request_cancel(backtest_id)
            if result["status"] == "CANCELLED":
                configuration = await self.repository.configuration(backtest_id) or {}
                self._remove_queued(backtest_id)
                if backtest_id in self._pending_ids:
                    self._pending_ids.remove(backtest_id)
                self._estimates.pop(backtest_id, None)
                self._cancel_requests.discard(backtest_id)
                self._release_upload(backtest_id, configuration)
                completion = self._completion.pop(backtest_id, None)
                if completion is not None and not completion.done():
                    completion.set_result(None)
            return result

    def _remove_queued(self, backtest_id: str) -> None:
        retained: list[tuple[str, dict[str, Any]] | None] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
            if item is None or item[0] != backtest_id:
                retained.append(item)
        for item in retained:
            self._queue.put_nowait(item)

    async def shutdown(self) -> None:
        if not self._started:
            return
        async with self._admission_lock:
            self._accepting = False
        pending: list[tuple[str, dict[str, Any]]] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None:
                pending.append(item)
            self._queue.task_done()
        await self.repository.fail_jobs(
            [identifier for identifier, _ in pending], "SERVER_SHUTDOWN"
        )
        async with self._admission_lock:
            for identifier, configuration in pending:
                self._release_upload(identifier, configuration)
                self._estimates.pop(identifier, None)
                if identifier in self._pending_ids:
                    self._pending_ids.remove(identifier)
                completion = self._completion.pop(identifier, None)
                if completion is not None and not completion.done():
                    completion.set_result(None)
        running_ids = list(self._running)
        self._cancel_requests.update(running_ids)
        for identifier in running_ids:
            await self.repository.request_cancel(identifier)
        for _ in self._workers:
            self._queue.put_nowait(None)
        if self._workers:
            done, unfinished = await asyncio.wait(self._workers, timeout=5)
            for task in unfinished:
                task.cancel()
            if unfinished:
                await asyncio.gather(*unfinished, return_exceptions=True)
                await self.repository.fail_jobs(running_ids, "SERVER_SHUTDOWN")
            if done:
                await asyncio.gather(*done, return_exceptions=True)
        self._workers.clear()
        self._pending_ids.clear()
        self._running.clear()
        self._estimates.clear()
        self._upload_owners.clear()
        self._cancel_requests.clear()
        capacity = self.settings.max_backtest_jobs + self.settings.max_pending_jobs
        self._queue = asyncio.Queue(maxsize=capacity)
        self._started = False

    def queue_status(self) -> dict[str, Any]:
        return {
            "accepting": self._accepting,
            "running_ids": sorted(self._running),
            "pending_ids": list(self._pending_ids),
            "running_count": len(self._running),
            "pending_count": len(self._pending_ids),
            "running_capacity": self.settings.max_backtest_jobs,
            "pending_capacity": self.settings.max_pending_jobs,
        }

    def resources(self) -> dict[str, int]:
        upload_ids = {
            config["csv_upload_id"]
            for config in (*self._running.values(),)
            if config.get("csv_upload_id")
        }
        for identifier in self._pending_ids:
            estimate = self._estimates.get(identifier)
            if estimate and estimate.get("upload_id"):
                upload_ids.add(str(estimate["upload_id"]))
        return {
            "estimated_candles": sum(
                item["estimated_candles"] for item in self._estimates.values()
            ),
            "estimated_memory_bytes": sum(
                item["estimated_memory_bytes"] for item in self._estimates.values()
            ),
            "staged_bytes": self.upload_store.staged_bytes(upload_ids),
            "admitted_jobs": len(self._running) + len(self._pending_ids),
        }

    def limits(self) -> dict[str, int]:
        names = (
            "max_backtest_jobs", "max_pending_jobs", "max_candles",
            "max_date_range_days", "max_csv_size_mb", "max_csv_rows",
            "max_memory_budget_mb", "job_timeout_minutes",
        )
        return {name: int(getattr(self.settings, name)) for name in names}

    def _preflight(self, configuration: dict[str, Any]) -> dict[str, Any]:
        symbol = str(configuration.get("symbol", ""))
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", symbol):
            raise BacktestValidationError("symbol format is invalid")
        if configuration.get("timeframe", "M5") != "M5":
            raise BacktestValidationError("only M5 timeframe is supported")
        start = self._instant(configuration["start_date"], end=False)
        end = self._instant(configuration["end_date"], end=True)
        if start >= end:
            raise BacktestValidationError("start_date must be before end_date")
        seconds = (end - start).total_seconds()
        if seconds > self.settings.max_date_range_days * 86400:
            raise BacktestValidationError("date range exceeds max_date_range_days")
        estimated = max(1, math.ceil(seconds / TIMEFRAME_SECONDS["M5"]))
        staged_bytes = 0
        upload_id: str | None = None
        if configuration.get("source") == "CSV":
            upload_id = configuration.get("csv_upload_id")
            if not upload_id:
                raise BacktestValidationError("csv_upload_id is required for CSV")
            metadata = self.upload_store.metadata(upload_id)
            estimated = int(metadata["row_count"])
            staged_bytes = int(metadata["size"])
            first_timestamp = self._instant(metadata["first_timestamp"], end=False)
            last_timestamp = self._instant(metadata["last_timestamp"], end=False)
            candle_duration = timedelta(seconds=TIMEFRAME_SECONDS["M5"])
            if last_timestamp < start or first_timestamp + candle_duration > end:
                raise BacktestValidationError(
                    "CSV timestamp range does not overlap the requested date range"
                )
            if estimated > self.settings.max_csv_rows:
                raise BacktestValidationError("CSV rows exceed max_csv_rows")
        elif configuration.get("source") == "MT5":
            if configuration.get("csv_upload_id") is not None:
                raise BacktestValidationError("csv_upload_id is forbidden for MT5")
        else:
            raise BacktestValidationError("source must be MT5 or CSV")
        if estimated > self.settings.max_candles:
            raise BacktestValidationError("estimated candles exceed max_candles")
        memory = estimated * 2048 + staged_bytes
        if memory > self.settings.max_memory_budget_mb * 1024 * 1024:
            raise BacktestValidationError("estimated memory exceeds max_memory_budget_mb")
        result: dict[str, Any] = {
            "estimated_candles": estimated,
            "estimated_memory_bytes": memory,
            "staged_bytes": staged_bytes,
        }
        if upload_id:
            result["upload_id"] = upload_id
        return result

    def _cleanup_unclaimed_upload(self, configuration: dict[str, Any]) -> None:
        upload_id = configuration.get("csv_upload_id")
        if upload_id and str(upload_id) in self._upload_owners:
            return
        self.upload_store.cleanup(upload_id)

    def _release_upload(
        self, backtest_id: str, configuration: dict[str, Any]
    ) -> None:
        upload_id = configuration.get("csv_upload_id")
        if not upload_id:
            return
        normalized = str(upload_id)
        if self._upload_owners.get(normalized) != backtest_id:
            return
        self._upload_owners.pop(normalized, None)
        self.upload_store.cleanup(normalized)

    def _cleanup_upload(self, configuration: dict[str, Any]) -> None:
        self.upload_store.cleanup(configuration.get("csv_upload_id"))

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
        finally:
            status = await self.repository.status(backtest_id)
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                self._cleanup_upload(configuration)

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
            rows = self.upload_store.rows(
                configuration["csv_upload_id"], limit=self.settings.max_candles
            )
            rows = self._closed_rows(rows, "M5", start, end)
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
                    configuration["symbol"], timeframe, start, end,
                    self.settings.max_candles
                )
                rows = self._closed_rows(
                    self._rate_rows(rates), timeframe, start, end
                )
                candles = service.load(rows, timeframe, validate_gaps=False)
                warnings.extend(self._gap_warnings(candles, timeframe))
                if timeframe == "M5":
                    spreads = self._spread_map(rows, timeframe)
        m5_count = len(service.candles("M5"))
        if m5_count > self.settings.max_candles:
            raise HistoricalDataError("runtime candle count exceeds max_candles")
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
        last_database_check = monotonic()
        snapshot_stride = max(1, math.ceil(len(candles) / 10_000))

        for index, candle in enumerate(candles):
            now = monotonic()
            checkpoint = index % 250 == 0 or now - last_database_check >= 1.0
            database_cancelled = False
            if checkpoint:
                database_cancelled = await self.repository.cancel_requested(backtest_id)
                last_database_check = now
            if backtest_id in self._cancel_requests or database_cancelled:
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
                    if len(entry_reasons) < 2000:
                        entry_reasons.append(reason)
                        if len(entry_reasons) <= 100:
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
                    if len(rejection_reasons) < 2000:
                        rejection_reasons.append(rejection)
                        if len(rejection_reasons) <= 100:
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
            if index % snapshot_stride == 0 or index + 1 == len(candles):
                snapshots.append(self._snapshot(
                    risk.state.balance, equity, floating, risk.state.peak_equity, candle
                ))
            processed = index + 1
            if checkpoint or processed == len(candles):
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
                        if len(rejection_reasons) < 2000:
                            rejection_reasons.append(rejection)
                            if len(rejection_reasons) <= 100:
                                await self.repository.add_event(
                                    backtest_id, "INFO", "STRATEGY_REJECTION",
                                    "Strategy rules did not produce an entry", rejection,
                                )
                except (AnalysisError, IndexError, TypeError, ValueError) as error:
                    warning = f"Strategy skipped insufficient/invalid data: {error}"
                    if len(warning_set) < 200:
                        warning_set.add(warning)
                    rejection = {
                        "source": "STRATEGY",
                        "signal_id": None,
                        "reasons": [str(error)],
                    }
                    if len(rejection_reasons) < 2000:
                        rejection_reasons.append(rejection)
                        if len(rejection_reasons) <= 100:
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
                    if key not in {"csv_path", "csv_upload_id"}
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
                if len(warnings) >= 200:
                    break
        return warnings
