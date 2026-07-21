import logging
from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.mt5.exceptions import (
    MT5ConfigurationError,
    MT5ConnectionError,
    MT5RealAccountRejected,
    MT5SymbolNotFound,
)
from app.mt5.manager import MT5ConnectionManager
from tests.fakes import FakeMT5Client


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mt5_login": 123456,
        "mt5_password": "demo-secret",
        "mt5_server": "Broker-Demo",
        "mt5_connect_retries": 1,
        "mt5_retry_delay_seconds": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_empty_mt5_configuration_is_normalized_and_rejected() -> None:
    settings = Settings(
        _env_file=None,
        mt5_login="",
        mt5_password="",
        mt5_server="",
        mt5_path="",
    )
    assert settings.mt5_login is None
    assert settings.mt5_password is None
    assert settings.mt5_server is None
    assert settings.mt5_path is None


@pytest.mark.asyncio
async def test_empty_configuration_cannot_connect() -> None:
    manager = MT5ConnectionManager(FakeMT5Client(), Settings(_env_file=None))
    with pytest.raises(MT5ConfigurationError):
        await manager.connect()


@pytest.mark.asyncio
async def test_password_never_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    secret = "never-log-this-password"
    client = FakeMT5Client()
    client.initialize_results = [False]
    client.error = (1001, f"authentication failed for {secret}")
    manager = MT5ConnectionManager(client, make_settings(mt5_password=secret))
    caplog.set_level(logging.WARNING)

    with pytest.raises(MT5ConnectionError):
        await manager.connect()

    assert secret not in caplog.text
    assert "[REDACTED]" in caplog.text


@pytest.mark.asyncio
async def test_connection_failure_stops_after_limited_retries() -> None:
    client = FakeMT5Client()
    client.initialize_results = [False, False]
    manager = MT5ConnectionManager(
        client,
        make_settings(mt5_connect_retries=2),
    )

    with pytest.raises(MT5ConnectionError):
        await manager.connect()

    assert client.initialize_calls == 2
    assert manager.status()["state"] == "connection_error"


@pytest.mark.asyncio
async def test_real_account_is_rejected_and_shutdown() -> None:
    client = FakeMT5Client()
    client.account = SimpleNamespace(trade_mode=2, login=999999)
    manager = MT5ConnectionManager(client, make_settings())

    with pytest.raises(MT5RealAccountRejected):
        await manager.connect()

    assert client.shutdown_calls == 1
    assert manager.status()["state"] == "real_account_rejected"
    assert manager.status()["connected"] is False


@pytest.mark.asyncio
async def test_symbol_not_found() -> None:
    manager = MT5ConnectionManager(FakeMT5Client(), make_settings())
    await manager.connect()

    with pytest.raises(MT5SymbolNotFound):
        await manager.symbol_info()


@pytest.mark.parametrize("symbol", ["XAUUSD", "XAUUSDm", "XAUUSD.a", "GOLD"])
@pytest.mark.asyncio
async def test_supported_gold_symbol_variations_are_detected(symbol: str) -> None:
    client = FakeMT5Client()
    client.symbols[symbol] = SimpleNamespace(name=symbol)
    manager = MT5ConnectionManager(client, make_settings())
    await manager.connect()

    info = await manager.symbol_info()

    assert info["name"] == symbol
