from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
from time import perf_counter
from typing import Any

from app.auth.permissions import ROLE_PERMISSIONS, RoleName
from app.auth.principal import Principal
from app.config.settings import Settings
from app.websocket.hub import WebSocketHub
from app.websocket.types import WebSocketTopic


class OfflineMarketReader:
    def __init__(self) -> None:
        self.calls = 0

    async def get_tick(self, symbol: str | None = None) -> dict[str, Any]:
        self.calls += 1
        return {
            "symbol": symbol or "XAUUSD",
            "bid": 3000.0,
            "ask": 3000.2,
            "spread_points": 20.0,
            "spread_price": 0.2,
            "timestamp": datetime.now(timezone.utc),
            "connection_status": "connected",
        }


class OfflineAuth:
    async def authenticate_access(self, token: str) -> Principal:
        return make_principal("benchmark")


class OfflineSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent = 0
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, Any]) -> None:
        _ = payload
        self.sent += 1

    async def receive_json(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        _ = (code, reason)


def make_principal(user_id: str) -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        role=RoleName.VIEWER,
        permissions=ROLE_PERMISSIONS[RoleName.VIEWER],
        session_id=f"session-{user_id}",
        access_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def settings(clients: int, interval: float) -> Settings:
    return Settings(
        _env_file=None,
        market_ws_interval_seconds=interval,
        ws_max_connections_per_user=min(max(clients, 1), 100),
        ws_max_connections_per_ip=min(max(clients, 1), 500),
        ws_max_total_connections=max(clients, 1),
        ws_client_buffer_size=1000,
        ws_slow_client_drop_limit=1000,
        ws_reconnect_rate_limit=1000,
        ws_heartbeat_interval_seconds=300,
        ws_heartbeat_timeout_seconds=300,
        ws_idle_timeout_seconds=3600,
        ws_session_revalidate_seconds=300,
    )


async def add_clients(
    hub: WebSocketHub, count: int, *, serve: bool
) -> tuple[list[Any], list[asyncio.Task[None]]]:
    connections = []
    tasks = []
    for index in range(count):
        socket = OfflineSocket()
        connection = await hub.register(
            socket,  # type: ignore[arg-type]
            make_principal(f"benchmark-{index}"),
            "offline-token",
            "127.0.0.1",
            {WebSocketTopic.MARKET},
            raw_market=True,
            symbol="XAUUSD",
        )
        connections.append(connection)
        if serve:
            tasks.append(asyncio.create_task(hub.serve(connection)))
    if serve:
        while not all(item.websocket.accepted for item in connections):
            await asyncio.sleep(0)
    return connections, tasks


async def fanout_benchmark(clients: int, iterations: int) -> dict[str, Any]:
    reader = OfflineMarketReader()
    hub = WebSocketHub(reader, OfflineAuth(), settings(clients, 1.0))
    _, tasks = await add_clients(hub, clients, serve=True)
    target = clients * iterations
    started = perf_counter()
    for sequence in range(iterations):
        await hub.publish(
            WebSocketTopic.MARKET,
            {"sequence": sequence, "connection_status": "connected"},
            symbol="XAUUSD",
        )
        expected = clients * (sequence + 1)
        while int((await hub.status())["metrics"]["sent_messages"]) < expected:
            await asyncio.sleep(0)
    elapsed = perf_counter() - started
    status = await hub.status()
    await hub.stop()
    await asyncio.gather(*tasks, return_exceptions=True)
    metrics = status["metrics"]
    return {
        "clients": clients,
        "broadcasts": iterations,
        "delivered_messages": target,
        "elapsed_seconds": round(elapsed, 6),
        "throughput_messages_per_second": round(target / elapsed, 2),
        "publish_to_consumer_latency_average_ms": round(
            float(metrics["delivery_latency_average_ms"]), 6
        ),
        "publish_to_consumer_latency_max_ms": round(
            float(metrics["delivery_latency_max_ms"]), 6
        ),
        "broadcast_duration_average_ms": round(
            float(metrics["broadcast_duration_average_ms"]), 6
        ),
        "broadcast_duration_max_ms": round(
            float(metrics["broadcast_duration_max_ms"]), 6
        ),
        "dropped_messages": metrics["dropped_messages"],
    }


async def read_frequency(clients: int, interval: float, duration: float) -> int:
    reader = OfflineMarketReader()
    hub = WebSocketHub(reader, OfflineAuth(), settings(clients, interval))
    connections, _ = await add_clients(hub, clients, serve=False)
    await hub.start()
    await asyncio.sleep(duration)
    await hub.stop()
    for connection in connections:
        await hub.unregister(connection)
    return reader.calls


async def benchmark(
    clients: int, iterations: int, interval: float, duration: float
) -> dict[str, Any]:
    fanout = await fanout_benchmark(clients, iterations)
    single_reads = await read_frequency(1, interval, duration)
    many_reads = await read_frequency(clients, interval, duration)
    return {
        "fanout": fanout,
        "market_read_frequency": {
            "interval_seconds": interval,
            "sample_seconds": duration,
            "one_subscriber_reads": single_reads,
            "many_subscribers": clients,
            "many_subscriber_reads": many_reads,
            "independent_of_subscriber_count": abs(many_reads - single_reads) <= 1,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline WebSocket fan-out benchmark")
    parser.add_argument("--clients", type=int, default=100)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--duration", type=float, default=0.5)
    args = parser.parse_args()
    if args.clients < 1 or args.clients > 500:
        parser.error("--clients must be between 1 and 500")
    if args.iterations < 1 or args.iterations > 1000:
        parser.error("--iterations must be between 1 and 1000")
    print(json.dumps(asyncio.run(benchmark(
        args.clients, args.iterations, args.interval, args.duration
    )), sort_keys=True))


if __name__ == "__main__":
    main()
