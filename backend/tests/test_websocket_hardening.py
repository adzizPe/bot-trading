import asyncio
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth.permissions import Permission, ROLE_PERMISSIONS, RoleName
from app.auth.principal import Principal
from app.auth.service import AuthenticationError
from app.main import create_app
from app.mt5.manager import MT5ConnectionManager
from app.websocket.hub import ConnectionRejected, WebSocketHub
from app.websocket.types import WebSocketTopic
from tests.auth_helpers import (
    TEST_ACCESS_TOKEN,
    auth_headers,
    authenticate_app,
)
from tests.fakes import FakeMT5Client
from tests.test_mt5_manager import make_settings


class FakeReader:
    def __init__(self) -> None:
        self.calls = 0

    async def get_tick(self, symbol: str | None = None) -> dict[str, Any]:
        self.calls += 1
        return {
            "symbol": symbol or "XAUUSD",
            "bid": 3000.0 + self.calls,
            "ask": 3000.2 + self.calls,
            "spread_points": 20.0,
            "spread_price": 0.2,
            "timestamp": datetime.now(timezone.utc),
            "connection_status": "connected",
        }


class MutableAuth:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal
        self.revoked = False
        self.calls = 0

    async def authenticate_access(self, token: str) -> Principal:
        self.calls += 1
        if token != TEST_ACCESS_TOKEN or self.revoked:
            raise AuthenticationError("invalid token")
        return self.principal


class FakeSocket:
    def __init__(self, send_delay: float = 0.0) -> None:
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.closed = asyncio.Event()
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.send_delay = send_delay

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.sent.append(payload)

    async def receive_json(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_code = code
        self.close_reason = reason
        self.closed.set()


def principal(
    *,
    user_id: str = "user-1",
    permissions: frozenset[Permission] | None = None,
    expires_in: float = 60.0,
) -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        role=RoleName.VIEWER,
        permissions=(
            ROLE_PERMISSIONS[RoleName.VIEWER]
            if permissions is None else permissions
        ),
        session_id=f"session-{user_id}",
        access_expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )


def make_hub(**overrides: object) -> tuple[WebSocketHub, FakeReader, MutableAuth]:
    reader = FakeReader()
    auth = MutableAuth(principal())
    values: dict[str, object] = {
        "market_ws_interval_seconds": 0.05,
        "ws_idle_timeout_seconds": 2.0,
        "ws_heartbeat_interval_seconds": 1.0,
        "ws_heartbeat_timeout_seconds": 1.0,
        "ws_session_revalidate_seconds": 1.0,
    }
    values.update(overrides)
    settings = make_settings(**values)
    return WebSocketHub(reader, auth, settings), reader, auth


async def register(
    hub: WebSocketHub,
    socket: FakeSocket,
    user: Principal | None = None,
    *,
    source_ip: str = "127.0.0.1",
    topics: set[WebSocketTopic] | None = None,
    symbol: str | None = None,
):
    return await hub.register(
        socket,  # type: ignore[arg-type]
        user or principal(),
        TEST_ACCESS_TOKEN,
        source_ip,
        topics if topics is not None else {WebSocketTopic.MARKET},
        raw_market=True,
        symbol=symbol,
    )


async def wait_until(predicate: Any, timeout: float = 1.0) -> None:
    deadline = perf_counter() + timeout
    while not predicate():
        if perf_counter() >= deadline:
            raise TimeoutError("condition was not met")
        await asyncio.sleep(0.005)


def test_private_websocket_rejects_missing_and_invalid_tokens() -> None:
    settings = make_settings()
    app = create_app(settings, MT5ConnectionManager(FakeMT5Client(), settings))
    authenticate_app(app)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as missing:
            with client.websocket_connect("/api/v1/ws/market"):
                pass
        assert missing.value.code == 4401
        with pytest.raises(WebSocketDisconnect) as invalid:
            with client.websocket_connect(
                "/api/v1/ws/market",
                headers={"Cookie": "access_token=invalid"},
            ):
                pass
        assert invalid.value.code == 4401


def test_private_websocket_rejects_disallowed_origin_and_permission() -> None:
    settings = make_settings(cors_origins=["http://allowed.test"])
    app = create_app(settings, MT5ConnectionManager(FakeMT5Client(), settings))
    fake = authenticate_app(app)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as origin:
            with client.websocket_connect(
                "/api/v1/ws/market",
                headers={
                    "Cookie": f"access_token={TEST_ACCESS_TOKEN}",
                    "Origin": "http://evil.test",
                },
            ):
                pass
        assert origin.value.code == 4403
        fake.principal = principal(permissions=frozenset())
        with pytest.raises(WebSocketDisconnect) as permission:
            with client.websocket_connect(
                "/api/v1/ws/market",
                headers={"Cookie": f"access_token={TEST_ACCESS_TOKEN}"},
            ):
                pass
        assert permission.value.code == 4403


@pytest.mark.asyncio
async def test_handshake_and_reconnect_rate_limits() -> None:
    hub, _, _ = make_hub(
        ws_handshake_rate_limit=1,
        ws_reconnect_rate_limit=2,
    )
    await hub.check_handshake("10.0.0.1")
    with pytest.raises(ConnectionRejected, match="Handshake rate") as handshake:
        await hub.check_handshake("10.0.0.1")
    assert handshake.value.code == 4429

    first = await register(hub, FakeSocket())
    await hub.unregister(first)
    second = await register(hub, FakeSocket())
    await hub.unregister(second)
    with pytest.raises(ConnectionRejected, match="Reconnect rate") as reconnect:
        await register(hub, FakeSocket())
    assert reconnect.value.code == 4429
    metrics = (await hub.status())["metrics"]
    assert metrics["reconnects"] == 1
    assert metrics["rejected_handshakes"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "second_user", "second_ip"),
    [
        ({"ws_max_connections_per_user": 1}, "user-1", "127.0.0.2"),
        ({"ws_max_connections_per_ip": 1}, "user-2", "127.0.0.1"),
        ({"ws_max_total_connections": 1}, "user-2", "127.0.0.2"),
    ],
)
async def test_per_user_ip_and_total_connection_limits(
    overrides: dict[str, object], second_user: str, second_ip: str
) -> None:
    hub, _, _ = make_hub(**overrides)
    first = await register(hub, FakeSocket())
    with pytest.raises(ConnectionRejected, match="connection limit") as rejected:
        await register(
            hub,
            FakeSocket(),
            principal(user_id=second_user),
            source_ip=second_ip,
        )
    assert rejected.value.code == 4429
    await hub.unregister(first)


@pytest.mark.asyncio
async def test_topics_are_separate_permission_checked_channels() -> None:
    hub, _, _ = make_hub()
    all_topics = set(WebSocketTopic)
    connection = await hub.register(
        FakeSocket(),  # type: ignore[arg-type]
        principal(), TEST_ACCESS_TOKEN, "127.0.0.1", set(),
    )
    await hub._subscribe(connection, [topic.value for topic in all_topics])
    assert connection.topics == all_topics
    assert (await hub.status())["topics"] == [topic.value for topic in WebSocketTopic]

    restricted = principal(permissions=frozenset({Permission.READ_MARKET}))
    with pytest.raises(ConnectionRejected, match="backtest") as denied:
        await hub.register(
            FakeSocket(),  # type: ignore[arg-type]
            restricted,
            TEST_ACCESS_TOKEN,
            "127.0.0.2",
            {WebSocketTopic.BACKTEST},
        )
    assert denied.value.code == 4403
    await hub.unregister(connection)


@pytest.mark.asyncio
async def test_subscribe_rate_limit_closes_abusive_client() -> None:
    hub, _, _ = make_hub(ws_subscribe_rate_limit=1)
    connection = await hub.register(
        FakeSocket(),  # type: ignore[arg-type]
        principal(), TEST_ACCESS_TOKEN, "127.0.0.1", set(),
    )
    await hub._subscribe(connection, ["market"])
    with pytest.raises(ConnectionRejected, match="Subscribe rate") as rejected:
        await hub._subscribe(connection, ["signals"])
    assert rejected.value.code == 4429
    await hub.unregister(connection)


@pytest.mark.asyncio
async def test_single_publisher_fans_out_and_updates_market_cache() -> None:
    hub, reader, _ = make_hub(
        ws_max_connections_per_user=20,
        ws_reconnect_rate_limit=20,
    )
    connections = [
        await register(hub, FakeSocket(), symbol="XAUUSD") for _ in range(12)
    ]
    await hub.start()
    await wait_until(lambda: reader.calls >= 2)
    status = await hub.status()
    await hub.stop()

    assert reader.calls <= 3
    assert all(connection.queue.qsize() >= 1 for connection in connections)
    assert status["metrics"]["active_by_topic"]["market"] == 12
    assert status["metrics"]["market_reads"] == reader.calls
    assert status["market_cache"]["XAUUSD"]["bid"] >= 3001.0
    assert status["market_cache"]["XAUUSD"]["spread"] == {
        "points": 20.0, "price": 0.2,
    }
    assert status["market_cache"]["XAUUSD"]["market_status"] == "connected"
    for connection in connections:
        await hub.unregister(connection)


@pytest.mark.asyncio
async def test_backpressure_is_bounded_latest_wins_and_disconnects_slow_client() -> None:
    hub, _, _ = make_hub(
        ws_client_buffer_size=2,
        ws_slow_client_drop_limit=2,
    )
    connection = await register(hub, FakeSocket(), symbol="XAUUSD")
    for sequence in range(5):
        await hub.publish(
            WebSocketTopic.MARKET,
            {"sequence": sequence, "connection_status": "connected"},
            symbol="XAUUSD",
        )
    assert connection.queue.qsize() == 2
    queued = [connection.queue.get_nowait().payload for _ in range(2)]
    assert queued[-1]["sequence"] == 3 or queued[-1]["sequence"] == 4
    assert connection.close_event.is_set()
    assert connection.close_code == 1013
    assert (await hub.status())["metrics"]["dropped_messages"] >= 2
    await hub.unregister(connection)


@pytest.mark.asyncio
async def test_broadcast_to_many_clients_has_bounded_duration_and_metrics() -> None:
    hub, _, _ = make_hub(
        ws_max_connections_per_user=100,
        ws_max_connections_per_ip=500,
        ws_max_total_connections=500,
        ws_reconnect_rate_limit=1000,
    )
    connections = [
        await register(
            hub,
            FakeSocket(),
            principal(user_id=f"user-{index}"),
            source_ip=f"10.0.{index // 250}.{index % 250 + 1}",
            symbol="XAUUSD",
        )
        for index in range(400)
    ]
    started = perf_counter()
    delivered = await hub.publish(
        WebSocketTopic.MARKET,
        {"bid": 3000.0, "ask": 3000.2, "connection_status": "connected"},
        symbol="XAUUSD",
    )
    elapsed = perf_counter() - started
    status = await hub.status()
    assert delivered == 400
    assert elapsed < 1.0
    assert status["metrics"]["broadcast_duration_max_ms"] < 1000.0
    assert all(connection.queue.qsize() == 1 for connection in connections)
    for connection in connections:
        await hub.unregister(connection)


@pytest.mark.asyncio
async def test_idle_timeout_closes_connection() -> None:
    hub, _, _ = make_hub(
        ws_idle_timeout_seconds=0.05,
        ws_heartbeat_interval_seconds=1.0,
        ws_heartbeat_timeout_seconds=1.0,
    )
    socket = FakeSocket()
    connection = await register(hub, socket)
    await hub.serve(connection)
    assert socket.accepted is True
    assert socket.close_code == 4408
    assert socket.close_reason == "WebSocket idle timeout"


@pytest.mark.asyncio
async def test_heartbeat_timeout_closes_client_without_pong() -> None:
    hub, _, _ = make_hub(
        ws_idle_timeout_seconds=1.0,
        ws_heartbeat_interval_seconds=0.05,
        ws_heartbeat_timeout_seconds=0.05,
    )
    socket = FakeSocket()
    connection = await register(hub, socket)
    await hub.serve(connection)
    assert any(message.get("type") == "ping" for message in socket.sent)
    assert socket.close_code == 4408
    assert socket.close_reason == "WebSocket heartbeat timeout"


@pytest.mark.asyncio
async def test_active_connection_closes_when_session_is_revoked() -> None:
    hub, _, auth = make_hub(
        ws_idle_timeout_seconds=1.0,
        ws_heartbeat_interval_seconds=1.0,
        ws_heartbeat_timeout_seconds=1.0,
        ws_session_revalidate_seconds=0.05,
    )
    socket = FakeSocket()
    connection = await register(hub, socket, auth.principal)
    serving = asyncio.create_task(hub.serve(connection))
    await wait_until(lambda: socket.accepted)
    auth.revoked = True
    await serving
    assert auth.calls >= 1
    assert socket.close_code == 4401
    assert socket.close_reason == "Session expired or revoked"


@pytest.mark.asyncio
async def test_active_connection_closes_at_token_expiration() -> None:
    hub, _, auth = make_hub(
        ws_idle_timeout_seconds=1.0,
        ws_heartbeat_interval_seconds=1.0,
        ws_heartbeat_timeout_seconds=1.0,
        ws_session_revalidate_seconds=1.0,
    )
    expiring = principal(expires_in=0.06)
    auth.principal = expiring
    socket = FakeSocket()
    connection = await register(hub, socket, expiring)
    await hub.serve(connection)
    assert socket.close_code == 4401
    assert socket.close_reason == "Access token expired"



def test_generic_topic_protocol_and_metrics_endpoint() -> None:
    settings = make_settings()
    app = create_app(settings, MT5ConnectionManager(FakeMT5Client(), settings))
    authenticate_app(app, RoleName.VIEWER)
    with TestClient(app, headers=auth_headers()) as client:
        with client.websocket_connect("/api/v1/ws") as socket:
            socket.send_json({"type": "subscribe", "topics": ["signals", "health"]})
            assert socket.receive_json() == {
                "type": "subscribed", "topics": ["health", "signals"],
            }
            status = client.get("/api/v1/websocket/status")
            assert status.status_code == 200
            assert status.json()["metrics"]["active_connections"] == 1
            assert status.json()["metrics"]["active_by_topic"]["signals"] == 1
