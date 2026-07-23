import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from app.demo.check import OrderCheckService
from app.demo.types import normalize_price, normalize_volume

from app.config.settings import Settings
from app.mt5.exceptions import (
    MT5ConfigurationError,
    MT5ConnectionError,
    MT5RealAccountRejected,
    MT5SymbolNotFound,
    MT5TradeValidationError,
    MT5OwnershipError,
)
from app.mt5.protocols import MT5ClientProtocol

logger = logging.getLogger(__name__)
SUPPORTED_GOLD_SYMBOLS = ("XAUUSD", "XAUUSDm", "XAUUSD.a", "GOLD")


class MT5ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CONNECTION_ERROR = "connection_error"
    REAL_ACCOUNT_REJECTED = "real_account_rejected"


class MT5ConnectionManager:
    """Serializes demo-guarded access to the process-global MT5 API."""

    def __init__(self, client: MT5ClientProtocol, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._lock = asyncio.Lock()
        self._state = MT5ConnectionState.DISCONNECTED
        self._connected = False
        self._demo_verified = False
        self._last_error: str | None = None
        self._resolved_symbol: str | None = None
        self._connection_version = 0
        self._order_send_calls = 0

    @property
    def order_send_calls(self) -> int:
        return self._order_send_calls

    @property
    def connection_version(self) -> int:
        return self._connection_version

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "connected": self._connected,
            "demo_verified": self._demo_verified,
            "configured": not self._missing_configuration(),
            "symbol": self._resolved_symbol or self._settings.mt5_symbol,
            "last_error": self._last_error,
        }

    async def connect(self) -> dict[str, Any]:
        async with self._lock:
            if self._connected and self._demo_verified:
                return self.status()
            missing = self._missing_configuration()
            if missing:
                self._state = MT5ConnectionState.CONNECTION_ERROR
                self._last_error = f"Missing MT5 configuration: {', '.join(missing)}"
                logger.warning("MT5 connection rejected: required configuration is missing")
                raise MT5ConfigurationError(self._last_error)

            self._state = MT5ConnectionState.CONNECTING
            password = self._settings.mt5_password.get_secret_value()  # type: ignore[union-attr]
            path = str(self._settings.mt5_path) if self._settings.mt5_path else None
            for attempt in range(1, self._settings.mt5_connect_retries + 1):
                try:
                    initialized = await asyncio.to_thread(
                        self._client.initialize,
                        path,
                        login=self._settings.mt5_login,
                        password=password,
                        server=self._settings.mt5_server,
                        timeout=self._settings.mt5_timeout_ms,
                    )
                except Exception as exc:  # vendor extension can raise non-domain errors
                    initialized = False
                    self._last_error = self._sanitize(str(exc))

                if initialized:
                    try:
                        account = await asyncio.to_thread(self._client.account_info)
                    except Exception as exc:
                        account = None
                        self._last_error = self._sanitize(f"MT5 account check failed: {exc}")
                    if account is not None:
                        await self._ensure_demo(account)
                        self._connected = True
                        self._demo_verified = True
                        self._state = MT5ConnectionState.CONNECTED
                        self._last_error = None
                        self._connection_version += 1
                        logger.info("MT5 demo connection established")
                        return self.status()
                    self._last_error = "MT5 account information is unavailable"
                elif self._last_error is None:
                    self._last_error = await self._vendor_error()

                logger.warning("MT5 connection attempt %s/%s failed: %s", attempt, self._settings.mt5_connect_retries, self._last_error)
                await self._safe_shutdown()
                if attempt < self._settings.mt5_connect_retries:
                    await asyncio.sleep(self._settings.mt5_retry_delay_seconds)

            self._state = MT5ConnectionState.CONNECTION_ERROR
            raise MT5ConnectionError(self._last_error or "MT5 connection failed")

    async def disconnect(self) -> dict[str, Any]:
        async with self._lock:
            was_active = self._connected or self._state != MT5ConnectionState.DISCONNECTED
            if self._connected or self._state == MT5ConnectionState.CONNECTING:
                await self._safe_shutdown()
                logger.info("MT5 connection closed")
            self._connected = False
            self._demo_verified = False
            self._resolved_symbol = None
            self._state = MT5ConnectionState.DISCONNECTED
            self._last_error = None
            if was_active:
                self._connection_version += 1
            return self.status()

    async def validate_demo_connection(self) -> None:
        async with self._lock:
            self._require_connected()
            await self._active_demo_account()

    async def market_tick(
        self, requested_symbol: str | None = None
    ) -> tuple[str, object, object]:
        async with self._lock:
            self._require_connected()
            await self._active_demo_account()
            symbol, info = await self._resolve_symbol(requested_symbol)
            tick = await self._vendor_call(
                self._client.symbol_info_tick,
                symbol,
                error_message="MT5 tick request failed",
            )
            if tick is None:
                raise MT5ConnectionError("MT5 tick data is unavailable")
            return symbol, info, tick

    async def risk_snapshot(self, requested_symbol: str | None = None) -> dict[str, Any]:
        """Return one atomic, demo-only account/symbol/tick snapshot for planning."""
        async with self._lock:
            self._require_connected()
            account = await self._active_demo_account()
            symbol, info = await self._resolve_symbol(requested_symbol)
            tick = await self._vendor_call(
                self._client.symbol_info_tick,
                symbol,
                error_message="MT5 tick request failed",
            )
            if tick is None:
                raise MT5ConnectionError("MT5 tick data is unavailable")
            account_fields = (
                "balance", "equity", "currency", "margin", "margin_free",
            )
            symbol_fields = (
                "digits", "point", "trade_tick_size", "trade_tick_value",
                "volume_min", "volume_max", "volume_step", "trade_stops_level",
                "trade_freeze_level", "trade_contract_size",
            )
            return {
                "demo_verified": True,
                "account": {field: self._value(account, field) for field in account_fields},
                "symbol": {
                    "name": symbol,
                    **{field: self._value(info, field) for field in symbol_fields},
                },
                "tick": {
                    "bid": self._value(tick, "bid"),
                    "ask": self._value(tick, "ask"),
                    "time": self._value(tick, "time"),
                    "time_msc": self._value(tick, "time_msc"),
                },
            }

    async def market_rates(
        self,
        requested_symbol: str | None,
        timeframe: str,
        start_time: object | None,
        end_time: object,
        count: int,
    ) -> tuple[str, object, object | None]:
        async with self._lock:
            self._require_connected()
            await self._active_demo_account()
            symbol, info = await self._resolve_symbol(requested_symbol)
            timeframe_value = self._client.timeframe_value(timeframe)
            if start_time is not None:
                rates = await self._vendor_call(
                    self._client.copy_rates_range,
                    symbol,
                    timeframe_value,
                    start_time,
                    end_time,
                    error_message="MT5 candle range request failed",
                )
            else:
                rates = await self._vendor_call(
                    self._client.copy_rates_from,
                    symbol,
                    timeframe_value,
                    end_time,
                    count,
                    error_message="MT5 candle request failed",
                )
            return symbol, info, rates

    async def account_info(self) -> dict[str, Any]:
        async with self._lock:
            self._require_connected()
            account = await self._active_demo_account()
            return {
                "login": int(self._value(account, "login", 0)),
                "is_demo": True,
                "trade_mode": "demo",
                "server": self._value(account, "server"),
                "company": self._value(account, "company"),
                "currency": self._value(account, "currency"),
                "leverage": self._value(account, "leverage"),
                "balance": self._value(account, "balance"),
                "equity": self._value(account, "equity"),
                "margin": self._value(account, "margin"),
                "margin_free": self._value(account, "margin_free"),
                "margin_level": self._value(account, "margin_level"),
            }

    async def terminal_info(self) -> dict[str, Any]:
        async with self._lock:
            self._require_connected()
            await self._active_demo_account()
            terminal = await self._vendor_call(
                self._client.terminal_info,
                error_message="MT5 terminal information failed",
            )
            if terminal is None or not bool(self._value(terminal, "connected", False)):
                self._connected = False
                self._demo_verified = False
                self._state = MT5ConnectionState.CONNECTION_ERROR
                self._last_error = "MT5 terminal connection was lost"
                raise MT5ConnectionError(self._last_error)
            fields = ("connected", "name", "company", "build", "trade_allowed", "tradeapi_disabled", "dlls_allowed", "ping_last")
            return {field: self._value(terminal, field) for field in fields}

    async def symbol_info(self) -> dict[str, Any]:
        async with self._lock:
            self._require_connected()
            await self._active_demo_account()
            for symbol in self._symbol_candidates():
                info = await self._vendor_call(
                    self._client.symbol_info,
                    symbol,
                    error_message="MT5 symbol information failed",
                )
                if info is not None:
                    self._resolved_symbol = str(self._value(info, "name", symbol))
                    fields = (
                        "name", "description", "digits", "point", "trade_tick_size",
                        "trade_tick_value", "volume_min", "volume_max", "volume_step",
                        "spread", "trade_stops_level", "trade_freeze_level",
                        "trade_contract_size", "visible", "select",
                    )
                    return {field: self._value(info, field, symbol if field == "name" else None) for field in fields}
            raise MT5SymbolNotFound("No supported XAU/USD symbol was found")

    async def execute_market_order(
        self, *, symbol: str, direction: str, volume: float, stop_loss: float,
        take_profit: float, magic: int, comment: str, deviation: int,
        maximum_spread_points: float = 300.0,
        position_ticket: int | None = None,
        risk_percent: float | None = None,
    ) -> dict[str, Any]:
        """Check and send once (or one explicit price retry) under the connection lock."""
        async with self._lock:
            if not self._settings.demo_execution_enabled:
                raise MT5ConfigurationError("Demo trading is disabled")
            if direction not in {"BUY", "SELL"}:
                raise MT5TradeValidationError("Direction must be BUY or SELL")
            if magic <= 0:
                raise MT5TradeValidationError("Magic must be greater than zero")
            if not 1 <= len(comment) <= 31:
                raise MT5TradeValidationError("Comment must contain 1 to 31 characters")
            if deviation < 0 or deviation > 1000:
                raise MT5TradeValidationError("Deviation must be between 0 and 1000 points")
            self._require_connected()
            account = await self._active_demo_account()
            await self._ensure_trade_permissions(account)
            if position_ticket is not None:
                position = await self._owned_position(position_ticket, magic)
                broker_symbol = str(self._value(position, "symbol", ""))
                if not broker_symbol:
                    raise MT5TradeValidationError("Broker position symbol is unavailable")
                actual_symbol, info = await self._resolve_symbol(broker_symbol)
                actual_direction = self._client.position_direction(
                    int(self._value(position, "type", -1))
                )
                if actual_direction != direction:
                    raise MT5TradeValidationError("Local and broker position directions differ")
                direction = "SELL" if direction == "BUY" else "BUY"
                volume = float(self._value(position, "volume", 0))
            else:
                actual_symbol, info = await self._resolve_symbol(symbol)
            if risk_percent is None or position_ticket is not None:
                prepared_volume = normalize_volume(
                    volume, self._value(info, "volume_min"),
                    self._value(info, "volume_max"), self._value(info, "volume_step"),
                )
            else:
                prepared_volume = volume
            tick_size = self._value(info, "trade_tick_size") or self._value(info, "point")
            digits = int(self._value(info, "digits", 0))
            prepared_stop = (
                0.0 if position_ticket is not None
                else normalize_price(stop_loss, tick_size, digits)
            )
            prepared_target = (
                0.0 if position_ticket is not None
                else normalize_price(take_profit, tick_size, digits)
            )
            filling = self._client.filling_mode_from_symbol(
                int(self._value(info, "filling_mode", 0))
            )
            last_request: dict[str, object] = {}
            for attempt in range(2):
                guarded_account, info, tick = await self._execution_guard(
                    actual_symbol, maximum_spread_points
                )
                tick_size = self._value(info, "trade_tick_size") or self._value(info, "point")
                digits = int(self._value(info, "digits", 0))
                price = float(self._value(tick, "ask" if direction == "BUY" else "bid", 0))
                prepared_price = normalize_price(price, tick_size, digits)
                if risk_percent is not None and position_ticket is None:
                    prepared_volume = self._risk_volume(
                        guarded_account, info, prepared_price, prepared_stop, risk_percent
                    )
                self._validate_geometry(
                    direction, prepared_price, prepared_stop, prepared_target, info,
                    closing=position_ticket is not None,
                )
                last_request = {
                    "action": self._client.trade_action_deal, "symbol": actual_symbol,
                    "volume": prepared_volume, "type": self._client.order_type(direction),
                    "price": prepared_price, "sl": prepared_stop, "tp": prepared_target,
                    "deviation": deviation, "magic": magic, "comment": comment,
                    "type_filling": filling,
                }
                if position_ticket is not None:
                    last_request["position"] = position_ticket
                else:
                    margin = await self._vendor_call(
                        self._client.order_calc_margin,
                        self._client.order_type(direction), actual_symbol,
                        prepared_volume, prepared_price,
                        error_message="MT5 margin calculation failed",
                    )
                    self._validate_margin(margin, guarded_account)
                await self._execution_guard(actual_symbol, maximum_spread_points)
                check = await self._vendor_call(
                    self._client.order_check, last_request,
                    error_message="MT5 order check failed",
                )
                if check is None or not self._client.trade_check_accepted(
                    int(self._value(check, "retcode", -1))
                ):
                    raise MT5TradeValidationError("Broker order_check rejected the request")
                if position_ticket is None:
                    check_margin = self._value(check, "margin")
                    if margin is None and check_margin is None:
                        raise MT5TradeValidationError(
                            "Broker provided no margin validation"
                        )
                    self._validate_margin(check_margin, guarded_account)
                await self._execution_guard(actual_symbol, maximum_spread_points)
                result = await self._send_unknown_safe(last_request, actual_symbol)
                if result["outcome"] != "RETRYABLE" or attempt == 1:
                    if result["outcome"] == "RETRYABLE":
                        result["outcome"] = "REJECTED"
                        result["reconciliation_required"] = False
                    result["attempts"] = attempt + 1
                    result["sanitized_request"] = self._sanitized_trade_request(
                        last_request
                    )
                    return result
            return self._unknown_result(last_request, actual_symbol)

    async def modify_position_stop(
        self, *, position_ticket: int, stop_loss: float, magic: int,
        comment: str, maximum_spread_points: float = 300.0,
    ) -> dict[str, Any]:
        async with self._lock:
            if not self._settings.demo_execution_enabled:
                raise MT5ConfigurationError("Demo trading is disabled")
            if position_ticket <= 0:
                raise MT5TradeValidationError("Position ticket must be greater than zero")
            if magic <= 0:
                raise MT5TradeValidationError("Magic must be greater than zero")
            if not 1 <= len(comment) <= 31:
                raise MT5TradeValidationError("Comment must contain 1 to 31 characters")
            self._require_connected()
            account = await self._active_demo_account()
            await self._ensure_trade_permissions(account)
            position = await self._owned_position(position_ticket, magic)
            symbol = str(self._value(position, "symbol", ""))
            if not symbol:
                raise MT5TradeValidationError("Broker position symbol is unavailable")
            actual_symbol, info = await self._resolve_symbol(symbol)
            _, info, tick = await self._execution_guard(
                actual_symbol, maximum_spread_points
            )
            direction = self._client.position_direction(int(self._value(position, "type", -1)))
            current_stop = float(self._value(position, "sl", 0) or 0)
            tick_size = self._value(info, "trade_tick_size") or self._value(info, "point")
            normalized = normalize_price(stop_loss, tick_size, int(self._value(info, "digits", 0)))
            market_price = float(self._value(tick, "bid" if direction == "BUY" else "ask", 0))
            if current_stop and (
                (direction == "BUY" and normalized <= current_stop)
                or (direction == "SELL" and normalized >= current_stop)
            ):
                raise MT5TradeValidationError("Stop loss may only be tightened")
            self._validate_stop_distance(direction, market_price, normalized, info)
            request: dict[str, object] = {
                "action": self._client.trade_action_sltp, "symbol": actual_symbol,
                "position": position_ticket, "sl": normalized,
                "tp": float(self._value(position, "tp", 0) or 0),
                "magic": magic, "comment": comment,
            }
            await self._execution_guard(actual_symbol, maximum_spread_points)
            check = await self._vendor_call(
                self._client.order_check, request,
                error_message="MT5 order check failed",
            )
            if check is None or not self._client.trade_check_accepted(
                int(self._value(check, "retcode", -1))
            ):
                raise MT5TradeValidationError("Broker order_check rejected the request")
            await self._execution_guard(actual_symbol, maximum_spread_points)
            result = await self._send_unknown_safe(request, actual_symbol)
            result["attempts"] = 1
            result["stop_loss"] = normalized
            return result

    async def cancel_pending_order(
        self, *, order_ticket: int, magic: int, comment: str,
        maximum_spread_points: float = 300.0,
    ) -> dict[str, Any]:
        async with self._lock:
            if not self._settings.demo_execution_enabled:
                raise MT5ConfigurationError("Demo trading is disabled")
            if order_ticket <= 0 or magic <= 0:
                raise MT5TradeValidationError("Order ticket and magic must be positive")
            self._require_connected()
            order = await self._owned_order(order_ticket, magic)
            symbol = str(self._value(order, "symbol", ""))
            if not symbol:
                raise MT5TradeValidationError("Broker order symbol is unavailable")
            actual_symbol, _ = await self._resolve_symbol(symbol)
            request: dict[str, object] = {
                "action": self._client.trade_action_remove,
                "order": order_ticket, "symbol": actual_symbol,
                "magic": magic, "comment": comment,
            }
            await self._execution_guard(actual_symbol, maximum_spread_points)
            check = await self._vendor_call(
                self._client.order_check, request,
                error_message="MT5 order check failed",
            )
            if check is None or not self._client.trade_check_accepted(
                int(self._value(check, "retcode", -1))
            ):
                raise MT5TradeValidationError("Broker order_check rejected the request")
            await self._execution_guard(actual_symbol, maximum_spread_points)
            result = await self._send_unknown_safe(request, actual_symbol)
            result["attempts"] = 1
            return result

    async def broker_snapshot(self, magic: int) -> dict[str, list[dict[str, Any]]]:
        async with self._lock:
            self._require_connected()
            await self._active_demo_account()
            positions_raw = await self._vendor_call(
                self._client.positions_get, error_message="MT5 positions request failed"
            ) or ()
            orders_raw = await self._vendor_call(
                self._client.orders_get, error_message="MT5 orders request failed"
            ) or ()
            start = datetime.now(timezone.utc) - timedelta(days=30)
            history_orders_raw = await self._vendor_call(
                self._client.history_orders_get, start, datetime.now(timezone.utc),
                error_message="MT5 order history request failed",
            ) or ()
            deals_raw = await self._vendor_call(
                self._client.history_deals_get, start, datetime.now(timezone.utc),
                error_message="MT5 deal history request failed",
            ) or ()
            return {
                "positions": [self._sanitize_position(item) for item in positions_raw if int(self._value(item, "magic", -1)) == magic],
                "orders": [self._sanitize_order(item) for item in (*orders_raw, *history_orders_raw) if int(self._value(item, "magic", -1)) == magic],
                "deals": [self._sanitize_deal(item) for item in deals_raw if int(self._value(item, "magic", -1)) == magic],
            }

    async def _resolve_symbol(
        self, requested_symbol: str | None = None
    ) -> tuple[str, object]:
        for candidate in self._symbol_candidates(requested_symbol):
            info = await self._vendor_call(
                self._client.symbol_info,
                candidate,
                error_message="MT5 symbol information failed",
            )
            if info is None:
                continue
            actual_symbol = str(self._value(info, "name", candidate))
            if not bool(self._value(info, "select", False)):
                selected = await self._vendor_call(
                    self._client.symbol_select,
                    actual_symbol,
                    True,
                    error_message="MT5 symbol selection failed",
                )
                if not selected:
                    continue
            refreshed = await self._vendor_call(
                self._client.symbol_info,
                actual_symbol,
                error_message="MT5 symbol information failed",
            )
            if refreshed is not None:
                info = refreshed
            self._resolved_symbol = actual_symbol
            return actual_symbol, info
        raise MT5SymbolNotFound("No supported XAU/USD symbol was found")

    async def _ensure_trade_permissions(self, account: object) -> None:
        if not bool(self._value(account, "trade_allowed", True)):
            raise MT5TradeValidationError("Account trading is not allowed")
        terminal = await self._vendor_call(
            self._client.terminal_info, error_message="MT5 terminal information failed"
        )
        if terminal is None or not bool(self._value(terminal, "trade_allowed", False)):
            raise MT5TradeValidationError("Terminal trading is not allowed")
        if bool(self._value(terminal, "tradeapi_disabled", False)):
            raise MT5TradeValidationError("Terminal API trading is disabled")

    async def _execution_guard(
        self, symbol: str, maximum_spread_points: float
    ) -> tuple[object, object, object]:
        if not self._settings.demo_execution_enabled:
            raise MT5ConfigurationError("Demo trading is disabled")
        if not math.isfinite(maximum_spread_points) or maximum_spread_points <= 0:
            raise MT5TradeValidationError("Maximum spread must be positive")
        self._require_connected()
        account = await self._active_demo_account()
        await self._ensure_trade_permissions(account)
        actual_symbol, info = await self._resolve_symbol(symbol)
        if actual_symbol != symbol:
            raise MT5TradeValidationError("Broker symbol changed during execution guard")
        tick = await self._vendor_call(
            self._client.symbol_info_tick, actual_symbol,
            error_message="MT5 tick request failed",
        )
        if tick is None:
            raise MT5ConnectionError("MT5 tick data is unavailable")
        point = float(self._value(info, "point", 0) or 0)
        bid = float(self._value(tick, "bid", 0) or 0)
        ask = float(self._value(tick, "ask", 0) or 0)
        if point <= 0 or bid <= 0 or ask < bid:
            raise MT5TradeValidationError("Broker symbol or tick is invalid")
        if (ask - bid) / point > maximum_spread_points:
            raise MT5TradeValidationError("Broker spread exceeds configured maximum")
        return account, info, tick

    def _risk_volume(
        self, account: object, info: object, entry: float, stop: float,
        risk_percent: float,
    ) -> float:
        if not math.isfinite(risk_percent) or not 0 < risk_percent <= 100:
            raise MT5TradeValidationError("Risk percent is invalid")
        balance = float(self._value(account, "balance", 0) or 0)
        equity = float(self._value(account, "equity", 0) or 0)
        base = equity if self._settings.risk_use_equity_for_risk else balance
        tick_size = float(
            self._value(info, "trade_tick_size") or self._value(info, "point") or 0
        )
        tick_value = float(self._value(info, "trade_tick_value", 0) or 0)
        stop_distance = abs(entry - stop)
        if base <= 0 or tick_size <= 0 or tick_value <= 0 or stop_distance <= 0:
            raise MT5TradeValidationError("Fresh risk lot inputs are unavailable")
        raw_volume = (base * risk_percent / 100) / (
            (stop_distance / tick_size) * tick_value
        )
        return normalize_volume(
            raw_volume, self._value(info, "volume_min"),
            self._value(info, "volume_max"), self._value(info, "volume_step"),
        )

    @staticmethod
    def _validate_margin(margin: object, account: object) -> None:
        if margin is None:
            return
        required = float(margin)
        available = float(getattr(account, "margin_free", 0) or 0)
        if not math.isfinite(required) or required < 0 or required > available:
            raise MT5TradeValidationError("Insufficient or invalid calculated margin")

    async def _owned_order(self, ticket: int, magic: int) -> object:
        orders = await self._vendor_call(
            self._client.orders_get, ticket=ticket,
            error_message="MT5 pending order lookup failed",
        )
        if not orders:
            raise MT5TradeValidationError("Broker pending order was not found")
        order = orders[0]
        if int(self._value(order, "magic", -1)) != magic:
            raise MT5OwnershipError("Broker order is not owned by the configured magic")
        return order

    async def _owned_position(self, ticket: int, magic: int) -> object:
        positions = await self._vendor_call(
            self._client.positions_get, ticket=ticket,
            error_message="MT5 position lookup failed",
        )
        if not positions:
            raise MT5TradeValidationError("Broker position was not found")
        position = positions[0]
        if int(self._value(position, "magic", -1)) != magic:
            raise MT5OwnershipError("Broker position is not owned by the configured magic")
        return position

    async def _send_unknown_safe(
        self, request: dict[str, object], symbol: str
    ) -> dict[str, Any]:
        self._order_send_calls += 1
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._client.order_send, request),
                timeout=self._settings.mt5_timeout_ms / 1000,
            )
        except Exception:
            return self._unknown_result(request, symbol)
        if result is None:
            return self._unknown_result(request, symbol)
        retcode = int(self._value(result, "retcode", -1))
        outcome = self._client.trade_outcome(retcode)
        return {
            "outcome": outcome, "retcode": retcode,
            "retcode_name": OrderCheckService.retcode_name(retcode),
            "retcode_message": OrderCheckService.retcode_name(retcode).replace("_", " ").title(),
            "broker_comment": self._sanitize(str(self._value(result, "comment", ""))) or None,
            "order": self._positive_ticket(self._value(result, "order")),
            "deal": self._positive_ticket(self._value(result, "deal")),
            "volume": self._finite_or_none(self._value(result, "volume")),
            "requested_volume": request.get("volume"),
            "price": self._finite_or_none(self._value(result, "price")),
            "requested_price": request.get("price", 0), "symbol": symbol,
            "reconciliation_required": outcome == "UNKNOWN",
        }

    def _unknown_result(
        self, request: dict[str, object], symbol: str
    ) -> dict[str, Any]:
        return {
            "outcome": "UNKNOWN", "retcode": None,
            "retcode_name": "UNKNOWN_RETCODE",
            "retcode_message": "Broker outcome is unknown",
            "broker_comment": None, "order": None, "deal": None,
            "volume": request.get("volume"),
            "requested_volume": request.get("volume"), "price": None,
            "requested_price": request.get("price", 0), "symbol": symbol,
            "attempts": 1, "reconciliation_required": True,
        }

    async def _active_demo_account(self) -> object:
        account = await self._vendor_call(
            self._client.account_info,
            error_message="MT5 account information failed",
        )
        if account is None:
            raise MT5ConnectionError("MT5 account information is unavailable")
        await self._ensure_demo(account)
        return account

    async def _vendor_call(
        self,
        operation: Callable[..., Any],
        *args: Any,
        error_message: str,
        **kwargs: Any,
    ) -> Any:
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except Exception as exc:
            message = self._sanitize(f"{error_message}: {exc}")
            self._last_error = message
            raise MT5ConnectionError(message) from None

    async def _ensure_demo(self, account: object) -> None:
        trade_mode = self._value(account, "trade_mode")
        if trade_mode == self._client.demo_trade_mode:
            return
        await self._safe_shutdown()
        self._connected = False
        self._demo_verified = False
        self._state = MT5ConnectionState.REAL_ACCOUNT_REJECTED
        self._last_error = "Only MetaTrader 5 demo accounts are allowed"
        logger.error("MT5 connection rejected because the active account is not demo")
        raise MT5RealAccountRejected(self._last_error)

    async def _safe_shutdown(self) -> None:
        try:
            await asyncio.to_thread(self._client.shutdown)
        except Exception as exc:
            logger.warning("MT5 shutdown failed: %s", self._sanitize(str(exc)))

    async def _vendor_error(self) -> str:
        try:
            code, message = await asyncio.to_thread(self._client.last_error)
            return self._sanitize(f"MT5 error {code}: {message}")
        except Exception as exc:
            return self._sanitize(f"MT5 error unavailable: {exc}")

    def _validate_geometry(
        self, direction: str, price: float, stop: float, target: float,
        info: object, *, closing: bool,
    ) -> None:
        values = (price,) if closing else (price, stop, target)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise MT5TradeValidationError("Trade prices must be finite and greater than zero")
        if not self._client.symbol_trading_allowed(int(self._value(info, "trade_mode", 0))):
            raise MT5TradeValidationError("Symbol trading is disabled")
        if closing:
            return
        valid = stop < price < target if direction == "BUY" else target < price < stop
        if not valid:
            raise MT5TradeValidationError("Stop/entry/target geometry is invalid")
        self._validate_stop_distance(direction, price, stop, info)
        minimum = max(
            float(self._value(info, "trade_stops_level", 0) or 0),
            float(self._value(info, "trade_freeze_level", 0) or 0),
        ) * float(self._value(info, "point", 0) or 0)
        if abs(target - price) < minimum:
            raise MT5TradeValidationError("Take profit violates broker stop/freeze distance")

    @staticmethod
    def _validate_stop_distance(
        direction: str, market_price: float, stop: float, info: object
    ) -> None:
        point = float(getattr(info, "point", 0) or 0)
        minimum = max(
            float(getattr(info, "trade_stops_level", 0) or 0),
            float(getattr(info, "trade_freeze_level", 0) or 0),
        ) * point
        valid_side = stop < market_price if direction == "BUY" else stop > market_price
        if not valid_side or abs(market_price - stop) < minimum:
            raise MT5TradeValidationError("Stop loss violates broker side or stop/freeze distance")

    def _sanitize_position(self, item: object) -> dict[str, Any]:
        opened = datetime.fromtimestamp(int(self._value(item, "time", 0)), timezone.utc)
        return {
            "ticket": int(self._value(item, "ticket", 0)),
            "symbol": str(self._value(item, "symbol", "")),
            "direction": self._client.position_direction(int(self._value(item, "type", -1))),
            "volume": float(self._value(item, "volume", 0)),
            "entry_price": float(self._value(item, "price_open", 0)),
            "current_price": float(self._value(item, "price_current", 0)),
            "stop_loss": float(self._value(item, "sl", 0) or 0),
            "take_profit": float(self._value(item, "tp", 0) or 0),
            "magic": int(self._value(item, "magic", 0)), "opened_at": opened,
        }

    def _sanitize_order(self, item: object) -> dict[str, Any]:
        return {
            "ticket": int(self._value(item, "ticket", 0)),
            "symbol": str(self._value(item, "symbol", "")),
            "magic": int(self._value(item, "magic", 0)),
            "volume": self._finite_or_none(self._value(item, "volume_current")),
            "price": self._finite_or_none(self._value(item, "price_open")),
        }

    def _sanitize_deal(self, item: object) -> dict[str, Any]:
        executed = datetime.fromtimestamp(int(self._value(item, "time", 0)), timezone.utc)
        return {
            "ticket": int(self._value(item, "ticket", 0)),
            "position_id": self._positive_ticket(self._value(item, "position_id")),
            "symbol": str(self._value(item, "symbol", "")),
            "direction": self._client.deal_direction(int(self._value(item, "type", -1))),
            "volume": float(self._value(item, "volume", 0)),
            "price": float(self._value(item, "price", 0)),
            "profit": float(self._value(item, "profit", 0)),
            "commission": float(self._value(item, "commission", 0)),
            "swap": float(self._value(item, "swap", 0)),
            "magic": int(self._value(item, "magic", 0)), "executed_at": executed,
        }

    def _missing_configuration(self) -> list[str]:
        missing: list[str] = []
        if self._settings.mt5_login is None:
            missing.append("MT5_LOGIN")
        if self._settings.mt5_password is None:
            missing.append("MT5_PASSWORD")
        if not self._settings.mt5_server:
            missing.append("MT5_SERVER")
        return missing

    def _symbol_candidates(self, requested_symbol: str | None = None) -> tuple[str, ...]:
        values = (requested_symbol, self._settings.mt5_symbol, *SUPPORTED_GOLD_SYMBOLS)
        return tuple(dict.fromkeys(value for value in values if value))

    def _require_connected(self) -> None:
        if not self._connected or not self._demo_verified:
            raise MT5ConnectionError("MT5 demo account is not connected")

    def _sanitize(self, message: str) -> str:
        for value in (self._settings.mt5_password, self._settings.demo_admin_token):
            if value:
                secret = value.get_secret_value()
                if secret:
                    message = message.replace(secret, "[REDACTED]")
        return message

    @staticmethod
    def _sanitized_trade_request(request: dict[str, object]) -> dict[str, object]:
        allowed = (
            "action", "symbol", "volume", "type", "price", "sl", "tp",
            "deviation", "magic", "type_filling", "position",
        )
        result = {name: request[name] for name in allowed if name in request}
        if "comment" in request:
            result["application_comment"] = request["comment"]
        return result

    @staticmethod
    def _positive_ticket(value: Any) -> int | None:
        try:
            ticket = int(value)
        except (TypeError, ValueError):
            return None
        return ticket if ticket > 0 else None

    @staticmethod
    def _finite_or_none(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _value(source: object, name: str, default: Any = None) -> Any:
        return getattr(source, name, default)
