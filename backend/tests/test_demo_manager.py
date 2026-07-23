from types import SimpleNamespace

import pytest

from app.mt5.exceptions import (
    MT5ConfigurationError,
    MT5ConnectionError,
    MT5OwnershipError,
    MT5RealAccountRejected,
    MT5TradeValidationError,
)
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings as base_settings


def make_settings(**overrides: object):
    values: dict[str, object] = {"demo_execution_enabled": True}
    values.update(overrides)
    return base_settings(**values)


@pytest.mark.asyncio
async def test_execution_is_fail_closed_when_demo_trading_is_disabled() -> None:
    client = FakeMT5Client()
    manager = MT5ConnectionManager(
        client, base_settings(demo_execution_enabled=False)
    )

    with pytest.raises(MT5ConfigurationError, match="disabled"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998, take_profit=3002, magic=7,
            comment="bot-demo", deviation=20,
        )

    assert client.initialize_calls == 0
    assert client.order_send_calls == 0
    assert manager.order_send_calls == 0


@pytest.mark.asyncio
async def test_account_is_reverified_under_send_lock() -> None:
    client = broker_client()
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    client.account = SimpleNamespace(trade_mode=2, login=123456, trade_allowed=True)

    with pytest.raises(MT5RealAccountRejected):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998, take_profit=3002, magic=7,
            comment="bot-demo", deviation=20,
        )

    assert client.order_send_calls == 0
    assert manager.order_send_calls == 0


@pytest.mark.asyncio
async def test_order_check_rejection_never_calls_order_send() -> None:
    client = broker_client()
    client.order_check_result = SimpleNamespace(retcode=10030)
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()

    with pytest.raises(MT5TradeValidationError, match="order_check"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998, take_profit=3002, magic=7,
            comment="bot-demo", deviation=20,
        )

    assert client.order_send_calls == 0
    assert manager.order_send_calls == 0
    await manager.disconnect()


def broker_client() -> FakeMT5Client:
    client = FakeMT5Client()
    client.symbols["XAUUSD"] = SimpleNamespace(
        name="XAUUSD", select=True, digits=2, point=0.01,
        trade_tick_size=0.01, trade_tick_value=1.0,
        volume_min=0.01, volume_max=100.0,
        volume_step=0.01, trade_stops_level=10, trade_freeze_level=5,
        filling_mode=1, trade_mode=1,
    )
    client.ticks["XAUUSD"] = SimpleNamespace(bid=2999.90, ask=3000.10)
    return client


@pytest.mark.asyncio
async def test_market_buy_uses_ask_symbol_filling_and_safe_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = broker_client()
    client.order_send_result = SimpleNamespace(
        retcode=10009, order=101, deal=202, volume=0.10, price=3000.10,
        comment="filled", credential="must-not-leak",
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=0.109,
        stop_loss=2998, take_profit=3002, magic=9072026,
        comment="bot-demo", deviation=20,
    )
    request = client.order_requests[0]
    assert request["price"] == 3000.10
    assert request["volume"] == 0.1
    assert request["type_filling"] == 1
    assert request["magic"] == 9072026
    assert result["outcome"] == "ACCEPTED"
    assert "credential" not in result
    assert "must-not-leak" not in caplog.text
    await manager.disconnect()


@pytest.mark.asyncio
async def test_order_send_exception_is_unknown_and_never_retried() -> None:
    client = broker_client()
    client.order_send_result = RuntimeError("uncertain transport failure")
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="SELL", volume=0.1,
        stop_loss=3002, take_profit=2998, magic=7,
        comment="bot-demo", deviation=20,
    )
    assert result["outcome"] == "UNKNOWN"
    assert result["reconciliation_required"] is True
    assert "uncertain transport failure" not in str(result)
    assert client.order_send_calls == 1
    assert manager.order_send_calls == 1
    await manager.disconnect()


@pytest.mark.asyncio
async def test_explicit_price_retcode_retries_once_inside_guard() -> None:
    class RetryClient(FakeMT5Client):
        def __init__(self) -> None:
            super().__init__()
            self.results = [
                SimpleNamespace(retcode=10020, order=0, deal=0, volume=0.1, price=0),
                SimpleNamespace(retcode=10009, order=10, deal=11, volume=0.1, price=3000.1),
            ]

        def order_send(self, request: dict[str, object]) -> object:
            self.order_send_calls += 1
            self.order_requests.append(dict(request))
            return self.results.pop(0)

    base = broker_client()
    client = RetryClient()
    client.symbols, client.ticks = base.symbols, base.ticks
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=0.1,
        stop_loss=2998, take_profit=3002, magic=7,
        comment="bot-demo", deviation=20,
    )
    assert result["outcome"] == "ACCEPTED"
    assert result["attempts"] == 2
    assert client.order_send_calls == 2
    await manager.disconnect()


@pytest.mark.asyncio
async def test_close_uses_opposite_side_bid_and_rejects_foreign_magic() -> None:
    client = broker_client()
    client.positions = [SimpleNamespace(
        ticket=77, magic=7, symbol="XAUUSD", type=0, volume=0.1,
        sl=2998.0, tp=3002.0, price_open=3000.1, price_current=2999.9,
    )]
    client.order_send_result = SimpleNamespace(
        retcode=10009, order=12, deal=13, volume=0.1, price=2999.9, comment="closed"
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=0.5, stop_loss=0,
        take_profit=0, magic=7, comment="bot-demo", deviation=20,
        position_ticket=77,
    )
    assert result["outcome"] == "ACCEPTED"
    assert client.order_requests[0]["type"] == 1
    assert client.order_requests[0]["price"] == 2999.9
    assert client.order_requests[0]["volume"] == 0.1
    with pytest.raises(MT5OwnershipError):
        await manager.modify_position_stop(
            position_ticket=77, stop_loss=2999, magic=8, comment="bot-demo"
        )
    await manager.disconnect()


@pytest.mark.asyncio
async def test_stop_change_only_tightens_and_honors_freeze_distance() -> None:
    client = broker_client()
    client.positions = [SimpleNamespace(
        ticket=77, magic=7, symbol="XAUUSD", type=0, volume=0.1,
        sl=2998.0, tp=3002.0, price_open=2998.5, price_current=2999.9,
    )]
    client.order_send_result = SimpleNamespace(
        retcode=10009, order=0, deal=0, volume=0.1, price=0, comment="modified"
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    with pytest.raises(MT5TradeValidationError, match="tightened"):
        await manager.modify_position_stop(
            position_ticket=77, stop_loss=2997.0, magic=7, comment="bot-demo"
        )
    with pytest.raises(MT5TradeValidationError, match="distance"):
        await manager.modify_position_stop(
            position_ticket=77, stop_loss=2999.85, magic=7, comment="bot-demo"
        )
    result = await manager.modify_position_stop(
        position_ticket=77, stop_loss=2999.0, magic=7, comment="bot-demo"
    )
    assert result["outcome"] == "ACCEPTED"
    assert client.order_requests[0]["sl"] == 2999.0
    await manager.disconnect()


@pytest.mark.asyncio
async def test_reconciliation_snapshot_excludes_foreign_magic() -> None:
    client = broker_client()
    common = {
        "symbol": "XAUUSD", "type": 0, "volume": 0.1, "sl": 2998.0,
        "tp": 3002.0, "price_open": 3000.0, "price_current": 3001.0,
        "time": 1_700_000_000,
    }
    client.positions = [
        SimpleNamespace(ticket=1, magic=7, **common),
        SimpleNamespace(ticket=2, magic=999, **common),
    ]
    client.history_orders = [
        SimpleNamespace(
            ticket=3, magic=7, symbol="XAUUSD", volume_current=0.1,
            price_open=3000.0,
        ),
        SimpleNamespace(
            ticket=4, magic=999, symbol="XAUUSD", volume_current=0.1,
            price_open=3000.0,
        ),
    ]
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    snapshot = await manager.broker_snapshot(7)
    assert [item["ticket"] for item in snapshot["positions"]] == [1]
    assert [item["ticket"] for item in snapshot["orders"]] == [3]
    assert manager.order_send_calls == 0
    await manager.disconnect()


@pytest.mark.asyncio
async def test_order_send_none_is_unknown_and_never_retried() -> None:
    client = broker_client()
    client.order_send_result = None
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()

    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="SELL", volume=0.1,
        stop_loss=3002, take_profit=2998, magic=7,
        comment="bot-demo", deviation=20,
    )

    assert result["outcome"] == "UNKNOWN"
    assert result["retcode"] is None
    assert client.order_send_calls == 1
    assert manager.order_send_calls == 1
    await manager.disconnect()


@pytest.mark.asyncio
async def test_order_check_exception_is_pre_send_rejection() -> None:
    class CheckFailureClient(FakeMT5Client):
        def order_check(self, request: dict[str, object]) -> object:
            raise RuntimeError("vendor detail must not escape")

    base = broker_client()
    client = CheckFailureClient()
    client.symbols, client.ticks = base.symbols, base.ticks
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    with pytest.raises(MT5ConnectionError, match="order check failed"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998, take_profit=3002, magic=7,
            comment="bot-demo", deviation=20,
        )
    assert client.order_send_calls == 0
    assert manager.order_send_calls == 0
    await manager.disconnect()


@pytest.mark.asyncio
async def test_margin_and_spread_guards_prevent_send() -> None:
    client = broker_client()
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    client.order_calc_margin_result = 20_000
    with pytest.raises(MT5TradeValidationError, match="margin"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998, take_profit=3002, magic=7,
            comment="bot-demo", deviation=20,
        )
    client.order_calc_margin_result = 100
    with pytest.raises(MT5TradeValidationError, match="spread"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998, take_profit=3002, magic=7,
            comment="bot-demo", deviation=20,
            maximum_spread_points=10,
        )
    assert client.order_send_calls == 0
    assert manager.order_send_calls == 0
    await manager.disconnect()


@pytest.mark.asyncio
async def test_broker_timeout_retcode_is_unknown_and_requires_reconciliation() -> None:
    client = broker_client()
    client.order_send_result = SimpleNamespace(
        retcode=10012, order=0, deal=0, volume=0.1, price=0
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()

    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=0.1,
        stop_loss=2998, take_profit=3002, magic=7,
        comment="bot-demo", deviation=20,
    )

    assert result["outcome"] == "UNKNOWN"
    assert result["retcode_name"] == "TIMEOUT"
    assert result["reconciliation_required"] is True
    assert client.order_send_calls == 1
    await manager.disconnect()


@pytest.mark.asyncio
async def test_fresh_account_price_stop_and_tick_value_recalculate_lot() -> None:
    client = broker_client()
    client.order_send_result = SimpleNamespace(
        retcode=10009, order=501, deal=502, volume=0.47, price=3000.10,
        comment="done",
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=99.0,
        stop_loss=2998.0, take_profit=3002.0, magic=7,
        comment="bot-demo", deviation=20, risk_percent=1.0,
    )
    assert client.order_requests[0]["volume"] == 0.47
    assert result["requested_volume"] == 0.47
    expected_request = {
        key: client.order_requests[0][key]
        for key in (
            "action", "symbol", "volume", "type", "price", "sl", "tp",
            "deviation", "magic", "type_filling",
        )
    }
    expected_request["application_comment"] = "bot-demo"
    assert result["sanitized_request"] == expected_request
    await manager.disconnect()


@pytest.mark.asyncio
async def test_market_sell_uses_fresh_bid() -> None:
    client = broker_client()
    client.order_send_result = SimpleNamespace(
        retcode=10009, order=601, deal=602, volume=0.1, price=2999.90,
        comment="done",
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    await manager.execute_market_order(
        symbol="XAUUSD", direction="SELL", volume=0.1,
        stop_loss=3002.0, take_profit=2998.0, magic=7,
        comment="bot-demo", deviation=20,
    )
    assert client.order_requests[0]["price"] == 2999.90
    await manager.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retcode", "name"),
    [(10014, "INVALID_VOLUME"), (10016, "INVALID_STOPS"), (10019, "NO_MONEY")],
)
async def test_explicit_broker_rejections_are_safe_and_not_retried(
    retcode: int, name: str,
) -> None:
    client = broker_client()
    client.order_send_result = SimpleNamespace(
        retcode=retcode, order=0, deal=0, volume=0.0, price=0.0,
        comment="vendor-safe",
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=0.1,
        stop_loss=2998.0, take_profit=3002.0, magic=7,
        comment="bot-demo", deviation=20,
    )
    assert result["outcome"] == "REJECTED"
    assert result["retcode_name"] == name
    assert result["retcode_message"]
    assert client.order_send_calls == 1
    await manager.disconnect()


@pytest.mark.asyncio
async def test_market_closed_is_rejected_before_send() -> None:
    client = broker_client()
    client.symbols["XAUUSD"].trade_mode = 0
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    with pytest.raises(MT5TradeValidationError, match="disabled"):
        await manager.execute_market_order(
            symbol="XAUUSD", direction="BUY", volume=0.1,
            stop_loss=2998.0, take_profit=3002.0, magic=7,
            comment="bot-demo", deviation=20,
        )
    assert client.order_send_calls == 0
    await manager.disconnect()


@pytest.mark.asyncio
async def test_requote_retcode_retries_once_then_done() -> None:
    class RequoteClient(FakeMT5Client):
        def __init__(self) -> None:
            super().__init__()
            self.results = [
                SimpleNamespace(retcode=10004, order=0, deal=0, volume=0.1, price=0),
                SimpleNamespace(
                    retcode=10009, order=701, deal=702, volume=0.1,
                    price=3000.1, comment="done",
                ),
            ]

        def order_send(self, request: dict[str, object]) -> object:
            self.order_send_calls += 1
            self.order_requests.append(dict(request))
            return self.results.pop(0)

    base = broker_client()
    client = RequoteClient()
    client.symbols, client.ticks = base.symbols, base.ticks
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    result = await manager.execute_market_order(
        symbol="XAUUSD", direction="BUY", volume=0.1,
        stop_loss=2998.0, take_profit=3002.0, magic=7,
        comment="bot-demo", deviation=20,
    )
    assert result["retcode_name"] == "DONE"
    assert result["attempts"] == 2
    assert client.order_send_calls == 2
    await manager.disconnect()


@pytest.mark.asyncio
async def test_close_sell_position_uses_buy_ask_and_owned_volume() -> None:
    client = broker_client()
    client.positions = [SimpleNamespace(
        ticket=88, magic=7, symbol="XAUUSD", type=1, volume=0.2,
        sl=3002.0, tp=2998.0, price_open=2999.9, price_current=3000.1,
    )]
    client.order_send_result = SimpleNamespace(
        retcode=10009, order=801, deal=802, volume=0.2,
        price=3000.1, comment="closed",
    )
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()
    await manager.execute_market_order(
        symbol="XAUUSD", direction="SELL", volume=99.0,
        stop_loss=0, take_profit=0, magic=7, comment="bot-demo",
        deviation=20, position_ticket=88,
    )
    assert client.order_requests[0]["type"] == 0
    assert client.order_requests[0]["price"] == 3000.1
    assert client.order_requests[0]["volume"] == 0.2
    await manager.disconnect()
