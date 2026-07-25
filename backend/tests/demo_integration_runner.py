"""Two-phase, fail-closed runner for one explicitly approved MT5 demo order."""

import asyncio
import json
import os
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.analysis.candle_confirmation import CandleConfirmationDetector
from app.analysis.engine import StrategyEngine
from app.analysis.indicators import IndicatorService
from app.analysis.market_structure import MarketStructureDetector
from app.analysis.repository import SignalRepository
from app.analysis.scoring import SignalScoringService
from app.analysis.service import AnalysisService
from app.analysis.support_resistance import SupportResistanceDetector
from app.analysis.validator import SignalValidator
from app.config.settings import get_settings
from app.database.session import SessionFactory, close_database
from app.demo.audit import ExecutionAuditService
from app.demo.executor import MT5OrderExecutor
from app.demo.guard import DemoAccountGuard, require_manual_mode
from app.demo.reconciliation import OrderReconciliationService
from app.demo.repository import DemoRepository
from app.demo.service import DemoTradingService
from app.market_data.service import MarketDataService
from app.mt5.client import MetaTrader5Client
from app.mt5.manager import MT5ConnectionManager
from app.risk.repository import RiskRepository
from app.risk.service import TradePlanService
from app.safety.audit import AuditTrail
from app.safety.circuit import CircuitBreaker
from app.safety.emergency import EmergencyStopManager
from app.safety.exceptions import SafetyLockedError
from app.safety.guardians import NewsGuardian, TradingSessionGuardian
from app.safety.manager import SafetyManager
from app.safety.monitor import HeartbeatMonitor
from app.safety.repository import SafetyRepository

OPT_IN = "I_UNDERSTAND_THIS_SENDS_A_DEMO_ORDER"
DEFAULT_TEST_MAGIC = 19_072_026
ARTIFACT = Path(__file__).resolve().parents[1] / "data" / "m9_demo_preflight.json"
MAX_PREFLIGHT_AGE_SECONDS = 180


def _mask_login(login: int) -> str:
    text = str(login)
    visible = min(4, len(text))
    return "*" * max(0, len(text) - visible) + text[-visible:]


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _services(settings: Any, manager: MT5ConnectionManager) -> tuple[Any, ...]:
    signals = SignalRepository(SessionFactory)
    market = MarketDataService(manager, settings)
    analysis = AnalysisService(
        market,
        settings,
        signals,
        IndicatorService(MarketStructureDetector(), SupportResistanceDetector()),
        CandleConfirmationDetector(),
        SignalValidator(),
        StrategyEngine(SignalScoringService()),
    )
    risk = TradePlanService(
        manager, settings, signals, RiskRepository(SessionFactory)
    )
    demo_repository = DemoRepository(SessionFactory)
    return analysis, risk, demo_repository


def _safety_services(
    settings: Any, manager: MT5ConnectionManager,
    demo_repository: DemoRepository, risk: TradePlanService,
) -> tuple[SafetyManager, HeartbeatMonitor, DemoTradingService]:
    if not settings.safety_enabled:
        raise RuntimeError("Safety layer is disabled; integration is blocked")
    repository = SafetyRepository(SessionFactory)
    audit = AuditTrail(repository)
    emergency = EmergencyStopManager(repository, audit, demo_repository)
    circuit = CircuitBreaker(
        threshold=settings.safety_circuit_error_threshold,
        window_minutes=settings.safety_circuit_window_minutes,
        lock_minutes=settings.safety_circuit_lock_minutes,
    )
    sessions = tuple(
        value.strip().upper()
        for value in settings.safety_active_sessions.split(",") if value.strip()
    )
    custom_end = (
        time(23, 59, 59) if settings.safety_custom_end_hour == 24
        else time(settings.safety_custom_end_hour)
    )
    safety = SafetyManager(
        emergency, circuit, audit, repository,
        TradingSessionGuardian(
            active_sessions=sessions,
            custom_timezone=settings.safety_custom_timezone,
            custom_start=time(settings.safety_custom_start_hour),
            custom_end=custom_end,
        ),
        NewsGuardian(
            settings.safety_news_blackout_before_minutes,
            settings.safety_news_blackout_after_minutes,
            settings.safety_news_required,
        ),
    )
    heartbeat = HeartbeatMonitor(
        safety, SessionFactory, manager, repository, audit,
        settings.safety_heartbeat_interval_seconds,
    )
    service = DemoTradingService(
        manager, demo_repository, risk, settings, safety, repository
    )
    manager.set_pre_send_guard(safety.fast_guard)
    return safety, heartbeat, service


def _test_magic(application_magic: int) -> int:
    raw = os.getenv("MT5_DEMO_ORDER_TEST_MAGIC", "").strip()
    magic = int(raw) if raw.isdigit() else DEFAULT_TEST_MAGIC
    if magic <= 0 or magic == application_magic:
        raise RuntimeError(
            "Integration magic must be positive and distinct from application magic"
        )
    return magic


def _execution_settings(settings: Any, magic: int) -> dict[str, Any]:
    return {
        "magic": magic,
        "comment": "m9-explicit-test",
        "deviation_points": settings.demo_deviation_points,
        "maximum_spread_points": settings.demo_maximum_spread_points,
        "intent_ttl_seconds": settings.demo_intent_ttl_seconds,
        "execution_mode": "MANUAL_DEMO",
        "maximum_send_attempts": 1,
    }


async def _minimum_volume_plan(
    analysis: AnalysisService,
    risk: TradePlanService,
    volume_min: float,
    volume_step: float,
    symbol: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    signal = await analysis.generate_signal(symbol)
    if signal["status"] != "CANDIDATE" or signal["direction"] not in {"BUY", "SELL"}:
        reasons = signal.get("rejection_reasons") or ["Current strategy produced no candidate"]
        raise RuntimeError(f"Fresh signal is not CANDIDATE: {reasons}")
    preliminary = await risk.create_trade_plan(str(signal["signal_id"]))
    if preliminary["status"] != "APPROVED":
        raise RuntimeError(
            f"Preliminary trade plan rejected: {preliminary['rejection_reasons']}"
        )
    details = preliminary["calculation_details"]["position_size"]
    target_raw_lot = volume_min + volume_step * 0.25
    risk_percent = (
        target_raw_lot * float(details["risk_per_lot"])
        / float(details["risk_base"])
        * 100
    )
    plan = await risk.create_trade_plan(
        str(signal["signal_id"]),
        {"risk_per_trade_percent": risk_percent},
    )
    if plan["status"] != "APPROVED":
        raise RuntimeError(f"Minimum-volume trade plan rejected: {plan['rejection_reasons']}")
    if abs(float(plan["position_size_lots"]) - volume_min) > 1e-9:
        raise RuntimeError("Risk-managed trade plan did not normalize to broker volume_min")
    return signal, plan


async def run_preflight() -> dict[str, Any]:
    settings = get_settings()
    if not settings.demo_execution_enabled:
        raise RuntimeError("DEMO_EXECUTION_ENABLED is false for this process")
    require_manual_mode(settings.demo_execution_mode)
    magic = _test_magic(settings.demo_magic)
    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    heartbeat: HeartbeatMonitor | None = None
    try:
        await manager.connect()
        await DemoAccountGuard().validate(manager)
        account = await manager.account_info()
        terminal = await manager.terminal_info()
        snapshot = await manager.risk_snapshot(settings.mt5_symbol)
        symbol = snapshot["symbol"]
        tick = snapshot["tick"]
        point = float(symbol["point"])
        bid, ask = float(tick["bid"]), float(tick["ask"])
        if not terminal.get("connected") or not terminal.get("trade_allowed"):
            raise RuntimeError("MT5 terminal is not connected or trading is not allowed")
        if terminal.get("tradeapi_disabled"):
            raise RuntimeError("MT5 terminal API trading is disabled")

        inventory = await manager.position_inventory()
        application_positions = [
            item for item in inventory if int(item["magic"]) == settings.demo_magic
        ]
        dedicated_positions = [
            item for item in inventory if int(item["magic"]) == magic
        ]
        if application_positions:
            raise RuntimeError("Application magic still owns an open broker position")
        if dedicated_positions:
            raise RuntimeError("Integration magic already owns an open broker position")

        analysis, risk, repository = _services(settings, manager)
        safety, heartbeat, safety_service = _safety_services(
            settings, manager, repository, risk
        )
        await safety.initialize()
        await heartbeat.run_once()
        await heartbeat.start()
        signal, plan = await _minimum_volume_plan(
            analysis,
            risk,
            float(symbol["volume_min"]),
            float(symbol["volume_step"]),
            str(symbol["name"]),
        )
        context = await risk.execution_context(str(plan["trade_plan_id"]))
        if not context["risk"].get("account_available"):
            raise RuntimeError("Risk account is unavailable")
        if context["risk"].get("risk_locked"):
            raise RuntimeError(
                f"Risk lock active: {context['risk'].get('risk_lock_reasons', [])}"
            )
        await safety_service._assert_safety(
            "OPEN_ORDER", str(plan["trade_plan_id"])
        )
        safety_status = safety.status()
        if not safety_status["allowed"]:
            raise RuntimeError("Safety layer is not safe; preflight blocked")

        check = await manager.check_market_order(
            symbol=str(plan["symbol"]),
            direction=str(plan["direction"]),
            volume=float(plan["position_size_lots"]),
            stop_loss=float(plan["stop_loss"]),
            take_profit=float(plan["take_profit"]),
            magic=magic,
            comment="m9-explicit-test",
            deviation=settings.demo_deviation_points,
            maximum_spread_points=settings.demo_maximum_spread_points,
            risk_percent=float(plan["risk_percent"]),
        )
        checked_volume = float(check["sanitized_request"]["volume"])
        if abs(checked_volume - float(symbol["volume_min"])) > 1e-9:
            raise RuntimeError("Fresh order_check request is not broker volume_min")
        if manager.order_send_calls != 0:
            raise RuntimeError("Preflight unexpectedly invoked order_send")

        dedicated_before = await manager.broker_snapshot(magic)
        run_id = str(uuid4())
        report = {
            "phase": "PREFLIGHT_PASSED",
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc),
            "demo_account_guard": {
                "passed": True,
                "is_demo": account["is_demo"],
                "trade_mode": account["trade_mode"],
            },
            "terminal_connected": bool(terminal.get("connected")),
            "terminal_trade_allowed": bool(terminal.get("trade_allowed")),
            "terminal_api_disabled": bool(terminal.get("tradeapi_disabled")),
            "broker": account.get("company") or terminal.get("company"),
            "server": account.get("server"),
            "masked_login": _mask_login(int(account["login"])),
            "symbol": str(symbol["name"]),
            "bid": bid,
            "ask": ask,
            "spread_price": ask - bid,
            "spread_points": (ask - bid) / point,
            "volume_min": float(symbol["volume_min"]),
            "volume_step": float(symbol["volume_step"]),
            "stops_level": float(symbol["trade_stops_level"]),
            "freeze_level": float(symbol["trade_freeze_level"]),
            "margin_required": check["margin_required"],
            "margin_free": check["margin_free"],
            "market_open": check["market_open"],
            "safety_status": safety_status,
            "guardian_status": safety_status["guardians"],
            "circuit_breaker_status": safety_status["circuit_breaker"],
            "emergency_stop_status": safety_status["emergency"],
            "heartbeat_status": heartbeat.snapshot(),
            "risk_locked": False,
            "application_open_positions": len(application_positions),
            "integration_open_positions": len(dedicated_positions),
            "integration_magic": magic,
            "signal_id": signal["signal_id"],
            "signal_direction": signal["direction"],
            "trade_plan_id": plan["trade_plan_id"],
            "trade_plan_status": plan["status"],
            "trade_plan_volume": plan["position_size_lots"],
            "order_check": check["order_check"],
            "sanitized_request": check["sanitized_request"],
            "order_send_calls": manager.order_send_calls,
        }
        artifact = {
            **report,
            "foreign_inventory": [
                item for item in inventory if int(item["magic"]) != magic
            ],
            "initial_deal_tickets": [
                int(item["ticket"]) for item in dedicated_before["deals"]
            ],
        }
        await repository.add_event(
            "integration", run_id, "INTEGRATION_PREFLIGHT_PASSED",
            "Explicit demo integration preflight passed",
            ExecutionAuditService.sanitize(report),
        )
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(
            json.dumps(_json_safe(artifact), indent=2), encoding="utf-8"
        )
        return report
    finally:
        if heartbeat is not None:
            await heartbeat.stop()
        await manager.disconnect()
        await close_database()


async def _cleanup_integration_positions(
    manager: MT5ConnectionManager, settings: Any, magic: int,
    safety_service: DemoTradingService,
) -> list[dict[str, Any]]:
    snapshot = await manager.broker_snapshot(magic)
    if not snapshot["positions"]:
        return []
    if len(snapshot["positions"]) != 1:
        raise RuntimeError("Unexpected number of dedicated integration positions")
    await safety_service._assert_safety("CLOSE_POSITION")
    position = snapshot["positions"][0]
    result = await manager.execute_market_order(
        symbol=str(position["symbol"]),
        direction=str(position["direction"]),
        volume=float(position["volume"]),
        stop_loss=0,
        take_profit=0,
        magic=magic,
        comment="m9-test-cleanup",
        deviation=settings.demo_deviation_points,
        maximum_spread_points=settings.demo_maximum_spread_points,
        position_ticket=int(position["ticket"]),
        maximum_send_attempts=1,
    )
    await asyncio.sleep(1)
    remaining = await manager.broker_snapshot(magic)
    if remaining["positions"]:
        raise RuntimeError("Dedicated integration position could not be closed")
    return [result]


async def _reconcile_until_position(
    reconciliation: OrderReconciliationService,
    manager: MT5ConnectionManager,
    magic: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    latest_run: dict[str, Any] = {}
    latest_snapshot: dict[str, Any] = {}
    for _ in range(12):
        latest_run = await reconciliation.reconcile(magic)
        latest_snapshot = await manager.broker_snapshot(magic)
        if latest_snapshot["positions"]:
            return latest_run, latest_snapshot
        await asyncio.sleep(0.75)
    raise RuntimeError("Accepted order did not produce a visible dedicated position")


async def _reconcile_until_closed(
    reconciliation: OrderReconciliationService,
    manager: MT5ConnectionManager,
    magic: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    latest_run: dict[str, Any] = {}
    latest_snapshot: dict[str, Any] = {}
    for _ in range(12):
        latest_run = await reconciliation.reconcile(magic)
        latest_snapshot = await manager.broker_snapshot(magic)
        if not latest_snapshot["positions"]:
            return latest_run, latest_snapshot
        await asyncio.sleep(0.75)
    raise RuntimeError("Dedicated integration position remains open after close")


async def run_execute() -> dict[str, Any]:
    if os.getenv("RUN_MT5_DEMO_ORDER_TEST") != OPT_IN:
        raise RuntimeError("Destructive demo-order opt-in phrase is missing")
    if not ARTIFACT.exists():
        raise RuntimeError("A passed preflight artifact is required before execution")
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    created = datetime.fromisoformat(str(artifact["created_at"]))
    age = (datetime.now(timezone.utc) - created).total_seconds()
    if age < 0 or age > MAX_PREFLIGHT_AGE_SECONDS:
        raise RuntimeError("Preflight artifact is stale; no order was sent")

    settings = get_settings()
    if not settings.demo_execution_enabled:
        raise RuntimeError("DEMO_EXECUTION_ENABLED is false for this process")
    require_manual_mode(settings.demo_execution_mode)
    magic = _test_magic(settings.demo_magic)
    if magic != int(artifact["integration_magic"]):
        raise RuntimeError("Integration magic differs from the reviewed preflight")

    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    repository: DemoRepository | None = None
    heartbeat: HeartbeatMonitor | None = None
    safety_service: DemoTradingService | None = None
    cleanup_results: list[dict[str, Any]] = []
    try:
        await manager.connect()
        await DemoAccountGuard().validate(manager)
        account = await manager.account_info()
        if not account["is_demo"] or account["trade_mode"] != "demo":
            raise RuntimeError("Active account is not verified demo")
        if _mask_login(int(account["login"])) != artifact["masked_login"]:
            raise RuntimeError("Active account changed after preflight")

        inventory_before = await manager.position_inventory()
        application_positions = [
            item for item in inventory_before
            if int(item["magic"]) == settings.demo_magic
        ]
        dedicated_positions = [
            item for item in inventory_before if int(item["magic"]) == magic
        ]
        if application_positions or dedicated_positions:
            raise RuntimeError("A protected application/integration position is open")
        foreign_before = [
            item for item in inventory_before if int(item["magic"]) != magic
        ]
        if foreign_before != artifact["foreign_inventory"]:
            raise RuntimeError("Broker position inventory changed after preflight")

        _, risk, repository = _services(settings, manager)
        safety, heartbeat, safety_service = _safety_services(
            settings, manager, repository, risk
        )
        await safety.initialize()
        await heartbeat.run_once()
        await heartbeat.start()
        context = await risk.execution_context(str(artifact["trade_plan_id"]))
        if context["plan"]["status"] != "APPROVED":
            raise RuntimeError("Reviewed trade plan is no longer APPROVED")
        if context["signal"]["status"] != "CANDIDATE":
            raise RuntimeError("Reviewed signal is no longer CANDIDATE")
        if not context["risk"].get("account_available"):
            raise RuntimeError("Risk account became unavailable")
        if context["risk"].get("risk_locked"):
            raise RuntimeError(
                f"Risk lock active: {context['risk'].get('risk_lock_reasons', [])}"
            )
        plan = context["plan"]
        await safety_service._assert_safety(
            "OPEN_ORDER", str(plan["trade_plan_id"])
        )
        safety_status = safety.status()
        if not safety_status["allowed"]:
            raise RuntimeError("Safety layer became unsafe before order_check")
        check = await manager.check_market_order(
            symbol=str(plan["symbol"]), direction=str(plan["direction"]),
            volume=float(plan["position_size_lots"]),
            stop_loss=float(plan["stop_loss"]),
            take_profit=float(plan["take_profit"]), magic=magic,
            comment="m9-explicit-test",
            deviation=settings.demo_deviation_points,
            maximum_spread_points=settings.demo_maximum_spread_points,
            risk_percent=float(plan["risk_percent"]),
        )
        if check["order_check"]["retcode_name"] != "DONE":
            raise RuntimeError("Fresh broker order_check did not return DONE")
        if abs(float(check["sanitized_request"]["volume"]) - float(artifact["volume_min"])) > 1e-9:
            raise RuntimeError("Fresh checked volume differs from broker volume_min")
        if manager.order_send_calls != 0:
            raise RuntimeError("order_send occurred before the execute phase")
        await safety_service._assert_safety(
            "OPEN_ORDER", str(plan["trade_plan_id"])
        )
        safety_status = safety.status()
        if not safety_status["allowed"]:
            raise RuntimeError("Safety layer became unsafe after order_check")

        executor = MT5OrderExecutor(manager, repository, risk, safety)
        reconciliation = OrderReconciliationService(manager, repository)
        execution_settings = _execution_settings(settings, magic)
        before_send = manager.order_send_calls
        execution = await executor.execute(
            str(plan["trade_plan_id"]),
            f"m9-integration-{artifact['run_id']}",
            execution_settings,
        )
        opening_send_calls = manager.order_send_calls - before_send
        if opening_send_calls != 1:
            raise RuntimeError("Opening phase did not issue exactly one order_send")
        if execution["status"] not in {"ACCEPTED", "UNKNOWN"}:
            raise RuntimeError(f"Opening order was not accepted: {execution['status']}")

        open_reconciliation, open_snapshot = await _reconcile_until_position(
            reconciliation, manager, magic
        )
        if len(open_snapshot["positions"]) != 1:
            raise RuntimeError("Integration magic does not own exactly one position")
        broker_position = open_snapshot["positions"][0]
        position_ticket = int(broker_position["ticket"])
        refreshed_execution = await repository.get_execution(
            str(execution["execution_request_id"])
        )
        if refreshed_execution is None:
            raise RuntimeError("Execution ledger entry disappeared")
        local_positions = await repository.list_positions("OPEN", 100, magic)
        local_position = next(
            (item for item in local_positions
             if int(item["broker_position_ticket"]) == position_ticket),
            None,
        )
        if local_position is None:
            raise RuntimeError("Reconciliation did not persist the dedicated position")

        await safety_service._assert_safety("CLOSE_POSITION")
        close_safety_status = safety.status()
        if not close_safety_status["allowed"]:
            raise RuntimeError("Safety layer became unsafe before controlled close")
        before_close = manager.order_send_calls
        close_result = await executor.close(
            str(local_position["position_id"]), execution_settings
        )
        closing_send_calls = manager.order_send_calls - before_close
        if closing_send_calls != 1:
            raise RuntimeError("Close phase did not issue exactly one order_send")
        if close_result["outcome"] not in {"ACCEPTED", "UNKNOWN"}:
            raise RuntimeError(f"Controlled close was rejected: {close_result['outcome']}")
        close_reconciliation, final_snapshot = await _reconcile_until_closed(
            reconciliation, manager, magic
        )

        inventory_after = await manager.position_inventory()
        foreign_after = [
            item for item in inventory_after if int(item["magic"]) != magic
        ]
        if foreign_after != foreign_before:
            raise RuntimeError("A non-integration broker position changed during the test")
        if any(int(item["magic"]) == magic for item in inventory_after):
            raise RuntimeError("Integration position is still present after close")

        initial_deals = set(int(value) for value in artifact["initial_deal_tickets"])
        new_deals = [
            item for item in final_snapshot["deals"]
            if int(item["ticket"]) not in initial_deals
        ]
        if len(new_deals) < 2:
            raise RuntimeError("Opening and closing deals were not both reconciled")
        close_deal = max(new_deals, key=lambda item: item["executed_at"])
        order_ticket = refreshed_execution.get("actual_order_ticket")
        deal_ticket = refreshed_execution.get("actual_deal_ticket")
        if not order_ticket or not deal_ticket or not position_ticket:
            raise RuntimeError("Broker order/deal/position ticket verification failed")

        response = refreshed_execution.get("sanitized_response") or {}
        net_pnl = (
            float(close_deal.get("profit", 0))
            + float(close_deal.get("commission", 0))
            + float(close_deal.get("swap", 0))
        )
        report = {
            "phase": "INTEGRATION_COMPLETED",
            "run_id": artifact["run_id"],
            "demo_account_guard": {"passed": True, "trade_mode": "demo"},
            "safety_guard_result": safety_status,
            "close_safety_guard_result": close_safety_status,
            "order_check": check["order_check"],
            "sanitized_request": check["sanitized_request"],
            "order_send_retcode": refreshed_execution.get("retcode"),
            "order_send_retcode_message": refreshed_execution.get("retcode_message"),
            "order_ticket": int(order_ticket),
            "deal_ticket": int(deal_ticket),
            "position_ticket": position_ticket,
            "direction": plan["direction"],
            "entry_price": response.get("price"),
            "close_price": close_result.get("price"),
            "volume": response.get("volume") or artifact["volume_min"],
            "demo_net_profit_loss": net_pnl,
            "opening_reconciliation": open_reconciliation,
            "closing_reconciliation": close_reconciliation,
            "position_closed": not final_snapshot["positions"],
            "remaining_integration_positions": final_snapshot["positions"],
            "opening_order_send_calls": opening_send_calls,
            "closing_order_send_calls": closing_send_calls,
            "order_send_calls": manager.order_send_calls,
            "other_positions_untouched": foreign_after == foreign_before,
            "integration_test": "PASSED",
        }
        await repository.add_event(
            "integration", str(artifact["run_id"]), "INTEGRATION_TEST_COMPLETED",
            "Explicit MT5 demo integration test completed and position closed",
            ExecutionAuditService.sanitize(report),
        )
        ARTIFACT.unlink(missing_ok=True)
        return report
    except Exception:
        if (
            manager.status().get("connected")
            and safety_service is not None
            and manager.order_send_calls <= 1
        ):
            cleanup_results = await _cleanup_integration_positions(
                manager, settings, magic, safety_service
            )
        raise
    finally:
        if cleanup_results and repository is not None:
            await repository.add_event(
                "integration", str(artifact.get("run_id", "unknown")),
                "INTEGRATION_FAILURE_CLEANUP",
                "Dedicated integration positions were cleaned after failure",
                ExecutionAuditService.sanitize({"results": cleanup_results}),
            )
        if heartbeat is not None:
            await heartbeat.stop()
        await manager.disconnect()
        await close_database()


async def run_inspect() -> dict[str, Any]:
    """Collect complete read-only safety readiness without order_check or send."""
    settings = get_settings()
    manager = MT5ConnectionManager(MetaTrader5Client(), settings)
    try:
        await manager.connect()
        await DemoAccountGuard().validate(manager)
        account = await manager.account_info()
        terminal = await manager.terminal_info()
        readiness = await manager.market_order_readiness(
            settings.mt5_symbol,
            min(settings.demo_maximum_spread_points, settings.safety_max_spread_points),
        )
        broker = await manager.risk_snapshot(settings.mt5_symbol)
        symbol = broker["symbol"]
        inventory = await manager.position_inventory()
        _, risk, repository = _services(settings, manager)
        safety, heartbeat, safety_service = _safety_services(
            settings, manager, repository, risk
        )
        await safety.initialize()
        await heartbeat.run_once()
        safety_error: dict[str, str] | None = None
        try:
            await safety_service._assert_safety("OPEN_ORDER")
        except SafetyLockedError as error:
            safety_error = {"guardian": error.guardian, "reason": error.reason}
        safety_status = safety.status()
        risk_status = await risk.status()
        magic = _test_magic(settings.demo_magic)
        ready = bool(readiness["order_check_ready"] and safety_status["allowed"])
        return {
            "phase": (
                "PREFLIGHT_READY_BEFORE_ORDER_CHECK"
                if ready else "PREFLIGHT_BLOCKED_BEFORE_ORDER_CHECK"
            ),
            "terminal_connected": readiness["terminal_connected"],
            "terminal_trade_allowed": readiness["terminal_trade_allowed"],
            "terminal_api_disabled": readiness["terminal_api_disabled"],
            "account_trade_mode": account["trade_mode"],
            "market_open": readiness["market_open"],
            "symbol": readiness["symbol"],
            "bid": readiness["bid"],
            "ask": readiness["ask"],
            "spread_points": readiness["spread_points"],
            "volume_min": float(symbol["volume_min"]),
            "volume_step": float(symbol["volume_step"]),
            "trade_stops_level": float(symbol["trade_stops_level"]),
            "trade_freeze_level": float(symbol["trade_freeze_level"]),
            "safety_status": safety_status,
            "safety_error": safety_error,
            "guardian_status": safety_status["guardians"],
            "circuit_breaker_status": safety_status["circuit_breaker"],
            "emergency_stop_status": safety_status["emergency"],
            "heartbeat_status": heartbeat.snapshot(),
            "risk_lock_status": {
                "locked": bool(risk_status.get("risk_locked")),
                "reasons": risk_status.get("risk_lock_reasons", []),
            },
            "application_open_positions": sum(
                int(item["magic"]) == settings.demo_magic for item in inventory
            ),
            "integration_open_positions": sum(
                int(item["magic"]) == magic for item in inventory
            ),
            "foreign_open_positions": sum(
                int(item["magic"]) not in {settings.demo_magic, magic}
                for item in inventory
            ),
            "order_send_calls": readiness["order_send_calls"],
            "order_check_readiness": (
                "READY_NOT_RUN" if ready else "BLOCKED_NOT_RUN"
            ),
            "order_check_calls": readiness["order_check_calls"],
            "symbol_trade_allowed": readiness["symbol_trade_allowed"],
            "spread_allowed": readiness["spread_allowed"],
            "quote_age_seconds": readiness["quote_age_seconds"],
            "demo_account_guard": "PASSED",
            "terminal": {
                "connected": terminal.get("connected"),
                "trade_allowed": terminal.get("trade_allowed"),
                "tradeapi_disabled": terminal.get("tradeapi_disabled"),
            },
        }
    finally:
        await manager.disconnect()
        await close_database()


async def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("inspect", "preflight", "execute"))
    args = parser.parse_args()
    if os.getenv("RUN_MT5_DEMO_ORDER_TEST") != OPT_IN:
        raise RuntimeError("Destructive demo-order opt-in phrase is missing")
    if args.phase == "inspect":
        result = await run_inspect()
    elif args.phase == "preflight":
        result = await run_preflight()
    else:
        result = await run_execute()
    print(json.dumps(_json_safe(result), indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
