from importlib import import_module
from types import ModuleType


class MetaTrader5Client:
    """Lazy adapter over the vendor package; calls occur only through the manager."""

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


    @property
    def trade_action_deal(self) -> int:
        return int(self._mt5().TRADE_ACTION_DEAL)

    @property
    def trade_action_sltp(self) -> int:
        return int(self._mt5().TRADE_ACTION_SLTP)

    @property
    def trade_action_remove(self) -> int:
        return int(self._mt5().TRADE_ACTION_REMOVE)

    def order_type(self, direction: str) -> int:
        module = self._mt5()
        return int(module.ORDER_TYPE_BUY if direction == "BUY" else module.ORDER_TYPE_SELL)

    def filling_mode_from_symbol(self, filling_mode: int) -> int:
        module = self._mt5()
        if filling_mode & int(module.SYMBOL_FILLING_IOC):
            return int(module.ORDER_FILLING_IOC)
        if filling_mode & int(module.SYMBOL_FILLING_FOK):
            return int(module.ORDER_FILLING_FOK)
        if filling_mode == int(module.ORDER_FILLING_RETURN):
            return int(module.ORDER_FILLING_RETURN)
        raise ValueError("Broker symbol exposes no supported filling mode")

    def trade_check_accepted(self, retcode: int) -> bool:
        module = self._mt5()
        return retcode in {0, int(module.TRADE_RETCODE_DONE)}

    def trade_outcome(self, retcode: int) -> str:
        module = self._mt5()
        if retcode in {
            int(module.TRADE_RETCODE_DONE), int(module.TRADE_RETCODE_DONE_PARTIAL),
            int(module.TRADE_RETCODE_PLACED),
        }:
            return "ACCEPTED"
        if retcode in {
            int(module.TRADE_RETCODE_REQUOTE), int(module.TRADE_RETCODE_PRICE_CHANGED),
        }:
            return "RETRYABLE"
        if retcode in {
            int(module.TRADE_RETCODE_TIMEOUT), int(module.TRADE_RETCODE_CONNECTION),
        }:
            return "UNKNOWN"
        return "REJECTED"

    def position_direction(self, position_type: int) -> str:
        return "BUY" if position_type == int(self._mt5().POSITION_TYPE_BUY) else "SELL"

    def deal_direction(self, deal_type: int) -> str:
        return "BUY" if deal_type == int(self._mt5().DEAL_TYPE_BUY) else "SELL"

    def order_check(self, request: dict[str, object]) -> object | None:
        return self._mt5().order_check(request)

    def order_calc_margin(
        self, order_type: int, symbol: str, volume: float, price: float
    ) -> float | None:
        value = self._mt5().order_calc_margin(order_type, symbol, volume, price)
        return None if value is None else float(value)

    def order_send(self, request: dict[str, object]) -> object | None:
        return self._mt5().order_send(request)

    def positions_get(self, **kwargs: object) -> object | None:
        return self._mt5().positions_get(**kwargs)

    def orders_get(self, **kwargs: object) -> object | None:
        return self._mt5().orders_get(**kwargs)

    def history_deals_get(self, *args: object, **kwargs: object) -> object | None:
        return self._mt5().history_deals_get(*args, **kwargs)

    def history_orders_get(self, *args: object, **kwargs: object) -> object | None:
        return self._mt5().history_orders_get(*args, **kwargs)

    def symbol_trading_allowed(self, trade_mode: int) -> bool:
        return trade_mode != int(self._mt5().SYMBOL_TRADE_MODE_DISABLED)
