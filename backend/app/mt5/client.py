from importlib import import_module
from types import ModuleType


class MetaTrader5Client:
    """Lazy, read-only adapter over the vendor package."""

    def __init__(self) -> None:
        self._module: ModuleType | None = None

    def _mt5(self) -> ModuleType:
        if self._module is None:
            self._module = import_module("MetaTrader5")
        return self._module

    @property
    def demo_trade_mode(self) -> int:
        return int(self._mt5().ACCOUNT_TRADE_MODE_DEMO)

    def initialize(
        self,
        path: str | None,
        *,
        login: int,
        password: str,
        server: str,
        timeout: int,
    ) -> bool:
        kwargs = {"login": login, "password": password, "server": server, "timeout": timeout}
        module = self._mt5()
        return bool(module.initialize(path, **kwargs) if path else module.initialize(**kwargs))

    def shutdown(self) -> None:
        self._mt5().shutdown()

    def last_error(self) -> tuple[int, str]:
        code, message = self._mt5().last_error()
        return int(code), str(message)

    def account_info(self) -> object | None:
        return self._mt5().account_info()

    def terminal_info(self) -> object | None:
        return self._mt5().terminal_info()

    def symbol_info(self, symbol: str) -> object | None:
        return self._mt5().symbol_info(symbol)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return bool(self._mt5().symbol_select(symbol, enable))

    def symbol_info_tick(self, symbol: str) -> object | None:
        return self._mt5().symbol_info_tick(symbol)

    def timeframe_value(self, timeframe: str) -> int:
        return int(getattr(self._mt5(), f"TIMEFRAME_{timeframe}"))

    def copy_rates_from(
        self, symbol: str, timeframe: int, end_time: object, count: int
    ) -> object | None:
        return self._mt5().copy_rates_from(symbol, timeframe, end_time, count)

    def copy_rates_range(
        self, symbol: str, timeframe: int, start_time: object, end_time: object
    ) -> object | None:
        return self._mt5().copy_rates_range(symbol, timeframe, start_time, end_time)
