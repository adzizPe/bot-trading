from types import SimpleNamespace


_ORDER_SEND_UNSET = object()


class FakeMT5Client:
    demo_trade_mode = 0

    def __init__(self) -> None:
        self.initialize_results = [True]
        self.initialize_calls = 0
        self.shutdown_calls = 0
        self.error = (1, "initialize failed")
        self.account = SimpleNamespace(
            trade_mode=0, login=123456, trade_allowed=True, margin_free=10_000.0,
            balance=10_000.0, equity=10_000.0,
        )
        self.terminal = SimpleNamespace(
            connected=True, trade_allowed=True, tradeapi_disabled=False
        )
        self.symbols: dict[str, object] = {}
        self.ticks: dict[str, object] = {}
        self.rates: list[object] | None = []
        self.positions: list[object] = []
        self.orders: list[object] = []
        self.deals: list[object] = []
        self.history_orders: list[object] = []
        self.order_check_result: object | None = SimpleNamespace(retcode=0)
        self.order_calc_margin_result: float | None = 100.0
        self.order_send_result: object = _ORDER_SEND_UNSET
        self.order_requests: list[dict[str, object]] = []
        self.tick_calls = 0
        self.rates_calls = 0
        self.order_send_calls = 0

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


    @property
    def trade_action_deal(self) -> int:
        return 1

    @property
    def trade_action_sltp(self) -> int:
        return 2

    @property
    def trade_action_remove(self) -> int:
        return 3

    def order_type(self, direction: str) -> int:
        return 0 if direction == "BUY" else 1

    def filling_mode_from_symbol(self, filling_mode: int) -> int:
        if filling_mode not in {1, 2}:
            raise ValueError("unsupported filling mode")
        return filling_mode

    def trade_check_accepted(self, retcode: int) -> bool:
        return retcode in {0, 10009}

    def trade_outcome(self, retcode: int) -> str:
        if retcode in {10008, 10009, 10010}:
            return "ACCEPTED"
        if retcode in {10004, 10020}:
            return "RETRYABLE"
        if retcode in {10012, 10031}:
            return "UNKNOWN"
        return "REJECTED"

    def position_direction(self, position_type: int) -> str:
        return "BUY" if position_type == 0 else "SELL"

    def deal_direction(self, deal_type: int) -> str:
        return "BUY" if deal_type == 0 else "SELL"

    def symbol_trading_allowed(self, trade_mode: int) -> bool:
        return trade_mode != 0

    def order_check(self, request: dict[str, object]) -> object | None:
        return self.order_check_result

    def order_calc_margin(
        self, order_type: int, symbol: str, volume: float, price: float
    ) -> float | None:
        return self.order_calc_margin_result

    def order_send(self, request: dict[str, object]) -> object | None:
        self.order_send_calls += 1
        self.order_requests.append(dict(request))
        if self.order_send_result is _ORDER_SEND_UNSET:
            raise AssertionError("Unexpected order_send call; set order_send_result explicitly")
        if isinstance(self.order_send_result, Exception):
            raise self.order_send_result
        return self.order_send_result

    def positions_get(self, **kwargs: object) -> object | None:
        ticket = kwargs.get("ticket")
        return [item for item in self.positions if ticket is None or item.ticket == ticket]

    def orders_get(self, **kwargs: object) -> object | None:
        ticket = kwargs.get("ticket")
        return [item for item in self.orders if ticket is None or item.ticket == ticket]

    def history_deals_get(self, *args: object, **kwargs: object) -> object | None:
        return list(self.deals)

    def history_orders_get(self, *args: object, **kwargs: object) -> object | None:
        return list(self.history_orders)
