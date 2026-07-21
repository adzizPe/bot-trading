import asyncio
import logging
from enum import Enum
from typing import Any, Callable

from app.config.settings import Settings
from app.mt5.exceptions import (
    MT5ConfigurationError,
    MT5ConnectionError,
    MT5RealAccountRejected,
    MT5SymbolNotFound,
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
    """Serializes safe, read-only access to the process-global MT5 API."""

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
    ) -> Any:
        try:
            return await asyncio.to_thread(operation, *args)
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
        password = self._settings.mt5_password
        if password:
            secret = password.get_secret_value()
            if secret:
                message = message.replace(secret, "[REDACTED]")
        return message

    @staticmethod
    def _value(source: object, name: str, default: Any = None) -> Any:
        return getattr(source, name, default)
