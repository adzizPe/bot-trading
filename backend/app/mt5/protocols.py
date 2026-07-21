from typing import Protocol


class MT5ClientProtocol(Protocol):
    @property
    def demo_trade_mode(self) -> int: ...

    def initialize(
        self,
        path: str | None,
        *,
        login: int,
        password: str,
        server: str,
        timeout: int,
    ) -> bool: ...

    def shutdown(self) -> None: ...

    def last_error(self) -> tuple[int, str]: ...

    def account_info(self) -> object | None: ...

    def terminal_info(self) -> object | None: ...

    def symbol_info(self, symbol: str) -> object | None: ...

    def symbol_select(self, symbol: str, enable: bool) -> bool: ...

    def symbol_info_tick(self, symbol: str) -> object | None: ...

    def timeframe_value(self, timeframe: str) -> int: ...

    def copy_rates_from(
        self, symbol: str, timeframe: int, end_time: object, count: int
    ) -> object | None: ...

    def copy_rates_range(
        self, symbol: str, timeframe: int, start_time: object, end_time: object
    ) -> object | None: ...
