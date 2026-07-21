from types import SimpleNamespace


class FakeMT5Client:
    demo_trade_mode = 0

    def __init__(self) -> None:
        self.initialize_results = [True]
        self.initialize_calls = 0
        self.shutdown_calls = 0
        self.error = (1, "initialize failed")
        self.account = SimpleNamespace(trade_mode=0, login=123456)
        self.terminal = SimpleNamespace(connected=True)
        self.symbols: dict[str, object] = {}
        self.ticks: dict[str, object] = {}
        self.rates: list[object] | None = []
        self.tick_calls = 0
        self.rates_calls = 0

    def initialize(self, path: str | None, **kwargs: object) -> bool:
        self.initialize_calls += 1
        index = min(self.initialize_calls - 1, len(self.initialize_results) - 1)
        return self.initialize_results[index]

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def last_error(self) -> tuple[int, str]:
        return self.error

    def account_info(self) -> object | None:
        return self.account

    def terminal_info(self) -> object | None:
        return self.terminal

    def symbol_info(self, symbol: str) -> object | None:
        return self.symbols.get(symbol)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return symbol in self.symbols

    def symbol_info_tick(self, symbol: str) -> object | None:
        self.tick_calls += 1
        return self.ticks.get(symbol)

    def timeframe_value(self, timeframe: str) -> int:
        values = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
        return values[timeframe]

    def copy_rates_from(
        self, symbol: str, timeframe: int, end_time: object, count: int
    ) -> object | None:
        self.rates_calls += 1
        return self.rates

    def copy_rates_range(
        self, symbol: str, timeframe: int, start_time: object, end_time: object
    ) -> object | None:
        self.rates_calls += 1
        return self.rates
