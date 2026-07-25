import asyncio
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
import logging
from time import monotonic, perf_counter
from typing import Any, Protocol
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from app.auth.principal import Principal
from app.auth.service import AuthenticationError
from app.config.settings import Settings
from app.websocket.types import (
    TOPIC_PERMISSIONS,
    CachedMessage,
    HubConnection,
    OutboundMessage,
    WebSocketTopic,
)

logger = logging.getLogger(__name__)


class MarketReader(Protocol):
    async def get_tick(self, symbol: str | None = None) -> dict[str, Any]: ...


class AccessAuthenticator(Protocol):
    async def authenticate_access(self, token: str) -> Principal: ...


class ConnectionRejected(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


Clock = Callable[[], float]


class WebSocketHub:
    """Single-process authenticated fan-out hub with bounded client queues."""

    def __init__(
        self,
        market_reader: MarketReader,
        authenticator: AccessAuthenticator,
        settings: Settings,
        clock: Clock = monotonic,
    ) -> None:
        self._market_reader = market_reader
        self._authenticator = authenticator
        self._settings = settings
        self._clock = clock
        self._lock = asyncio.Lock()
        self._connections: dict[str, HubConnection] = {}
        self._cache: dict[tuple[WebSocketTopic, str | None], CachedMessage] = {}
        self._handshakes: dict[str, deque[float]] = {}
        self._reconnects: dict[str, deque[float]] = {}
        self._subscribes: dict[str, deque[float]] = {}
        self._market_read_times: deque[float] = deque()
        self._publisher_wakeup = asyncio.Event()
        self._publisher_task: asyncio.Task[None] | None = None
        self._serve_tasks: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self._metrics: dict[str, int | float] = {
            "total_connections": 0,
            "reconnects": 0,
            "rejected_handshakes": 0,
            "rejected_limits": 0,
            "dropped_messages": 0,
            "published_messages": 0,
            "sent_messages": 0,
            "market_reads": 0,
            "market_read_failures": 0,
            "market_read_latency_total_ms": 0.0,
            "market_read_latency_max_ms": 0.0,
            "delivery_latency_total_ms": 0.0,
            "delivery_latency_max_ms": 0.0,
            "broadcast_duration_total_ms": 0.0,
            "broadcast_duration_max_ms": 0.0,
            "broadcasts": 0,
        }

    async def start(self) -> None:
        if self._publisher_task is not None and not self._publisher_task.done():
            return
        # FastAPI test/reload lifecycles may reuse the app on a new event loop.
        # Runtime primitives must belong to the loop that owns this lifespan.
        self._lock = asyncio.Lock()
        self._publisher_wakeup = asyncio.Event()
        self._serve_tasks = set()
        self._stopping = False
        self._publisher_task = asyncio.create_task(
            self._market_publisher(), name="websocket-market-publisher"
        )

    async def stop(self) -> None:
        self._stopping = True
        publisher = self._publisher_task
        self._publisher_task = None
        if publisher is not None:
            publisher.cancel()
            with suppress(asyncio.CancelledError):
                await publisher
        async with self._lock:
            connections = list(self._connections.values())
        for connection in connections:
            self._request_close(connection, 1012, "Server shutdown")
        current = asyncio.current_task()
        tasks = [task for task in self._serve_tasks if task is not current]
        if tasks:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self._settings.ws_send_timeout_seconds + 1.0,
                )

    async def check_handshake(self, source_ip: str) -> None:
        async with self._lock:
            allowed, _ = self._rate_allowed(
                self._handshakes,
                source_ip,
                self._settings.ws_handshake_rate_limit,
                self._settings.ws_handshake_rate_window_seconds,
            )
            if not allowed:
                self._metrics["rejected_handshakes"] += 1
                raise ConnectionRejected(4429, "Handshake rate limit exceeded")

    async def register(
        self,
        websocket: WebSocket,
        principal: Principal,
        token: str,
        source_ip: str,
        topics: set[WebSocketTopic],
        *,
        raw_market: bool = False,
        symbol: str | None = None,
    ) -> HubConnection:
        self._authorize(principal, topics)
        now = self._clock()
        connection = HubConnection(
            connection_id=uuid4().hex,
            websocket=websocket,
            principal=principal,
            token=token,
            source_ip=source_ip,
            topics=set(topics),
            queue=asyncio.Queue(maxsize=self._settings.ws_client_buffer_size),
            connected_at=datetime.now(timezone.utc),
            last_activity_at=now,
            last_pong_at=now,
            raw_market=raw_market,
            symbol=symbol,
        )
        async with self._lock:
            if self._stopping:
                raise ConnectionRejected(1012, "Server is shutting down")
            reconnect_key = f"{principal.user_id}:{source_ip}"
            allowed, repeated = self._rate_allowed(
                self._reconnects,
                reconnect_key,
                self._settings.ws_reconnect_rate_limit,
                self._settings.ws_reconnect_rate_window_seconds,
            )
            if not allowed:
                self._metrics["rejected_limits"] += 1
                raise ConnectionRejected(4429, "Reconnect rate limit exceeded")
            if repeated:
                self._metrics["reconnects"] += 1
            user_count = sum(
                item.principal.user_id == principal.user_id
                for item in self._connections.values()
            )
            ip_count = sum(
                item.source_ip == source_ip for item in self._connections.values()
            )
            if (
                len(self._connections) >= self._settings.ws_max_total_connections
                or user_count >= self._settings.ws_max_connections_per_user
                or ip_count >= self._settings.ws_max_connections_per_ip
            ):
                self._metrics["rejected_limits"] += 1
                raise ConnectionRejected(4429, "WebSocket connection limit exceeded")
            wake_market = (
                WebSocketTopic.MARKET in topics
                and not any(
                    WebSocketTopic.MARKET in item.topics
                    and item.symbol == symbol
                    for item in self._connections.values()
                )
            )
            self._connections[connection.connection_id] = connection
            self._metrics["total_connections"] += 1
        if wake_market:
            self._publisher_wakeup.set()
        return connection

    async def unregister(self, connection: HubConnection) -> None:
        async with self._lock:
            self._connections.pop(connection.connection_id, None)
            self._subscribes.pop(connection.connection_id, None)
        self._publisher_wakeup.set()

    async def serve(self, connection: HubConnection) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._serve_tasks.add(task)
        accepted = False
        workers: list[asyncio.Task[Any]] = []
        try:
            await connection.websocket.accept()
            accepted = True
            await self._prime(connection)
            workers = [
                asyncio.create_task(self._sender(connection)),
                asyncio.create_task(self._receiver(connection)),
                asyncio.create_task(self._watchdog(connection)),
                asyncio.create_task(connection.close_event.wait()),
            ]
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for worker in done:
                if worker.cancelled():
                    continue
                error = worker.exception()
                if error is None or isinstance(error, WebSocketDisconnect):
                    continue
                if isinstance(error, (RuntimeError, asyncio.CancelledError)):
                    continue
                logger.warning("WebSocket worker failed: %s", type(error).__name__)
                self._request_close(connection, 1011, "WebSocket worker failed")
        finally:
            for worker in workers:
                worker.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
            if accepted and connection.close_event.is_set():
                with suppress(RuntimeError, WebSocketDisconnect):
                    await connection.websocket.close(
                        code=connection.close_code, reason=connection.close_reason
                    )
            await self.unregister(connection)
            if task is not None:
                self._serve_tasks.discard(task)

    async def publish(
        self,
        topic: WebSocketTopic,
        payload: dict[str, Any],
        *,
        symbol: str | None = None,
    ) -> int:
        started = perf_counter()
        safe_payload = jsonable_encoder(payload)
        captured_at = datetime.now(timezone.utc)
        async with self._lock:
            self._cache[(topic, symbol)] = CachedMessage(safe_payload, captured_at)
            connections = [
                item for item in self._connections.values()
                if topic in item.topics
                and (topic is not WebSocketTopic.MARKET or item.symbol == symbol)
            ]
        for connection in connections:
            self._offer(
                connection,
                self._event_payload(connection, topic, safe_payload, captured_at),
                topic,
            )
        duration_ms = (perf_counter() - started) * 1000
        self._metrics["published_messages"] += len(connections)
        self._metrics["broadcasts"] += 1
        self._metrics["broadcast_duration_total_ms"] += duration_ms
        self._metrics["broadcast_duration_max_ms"] = max(
            float(self._metrics["broadcast_duration_max_ms"]), duration_ms
        )
        return len(connections)

    async def status(self) -> dict[str, Any]:
        now = self._clock()
        self._prune(self._market_read_times, now, 60.0)
        async with self._lock:
            connections = list(self._connections.values())
            cache = dict(self._cache)
        by_topic = {
            topic.value: sum(topic in item.topics for item in connections)
            for topic in WebSocketTopic
        }
        sent = int(self._metrics["sent_messages"])
        broadcasts = int(self._metrics["broadcasts"])
        metrics = {
            key: value for key, value in self._metrics.items()
            if not key.endswith("_total_ms") and key != "broadcasts"
        }
        metrics.update({
            "active_connections": len(connections),
            "active_by_topic": by_topic,
            "delivery_latency_average_ms": (
                float(self._metrics["delivery_latency_total_ms"]) / sent
                if sent else 0.0
            ),
            "market_read_latency_average_ms": (
                float(self._metrics["market_read_latency_total_ms"])
                / int(self._metrics["market_reads"])
                if self._metrics["market_reads"] else 0.0
            ),
            "broadcast_duration_average_ms": (
                float(self._metrics["broadcast_duration_total_ms"]) / broadcasts
                if broadcasts else 0.0
            ),
            "market_reads_per_minute": len(self._market_read_times),
        })
        market_cache: dict[str, Any] = {}
        for (topic, symbol), item in cache.items():
            if topic is not WebSocketTopic.MARKET:
                continue
            data = item.payload
            market_cache[symbol or "default"] = {
                "tick": data,
                "bid": data.get("bid"),
                "ask": data.get("ask"),
                "spread": {
                    "points": data.get("spread_points"),
                    "price": data.get("spread_price"),
                },
                "market_status": data.get("connection_status", "unavailable"),
                "captured_at": item.captured_at.isoformat(),
            }
        return {
            "state": "stopping" if self._stopping else "running",
            "topics": [topic.value for topic in WebSocketTopic],
            "metrics": metrics,
            "market_cache": market_cache,
        }

    async def active_market_symbols(self) -> set[str | None]:
        async with self._lock:
            return {
                item.symbol for item in self._connections.values()
                if WebSocketTopic.MARKET in item.topics
            }

    async def _subscribe(
        self, connection: HubConnection, values: object
    ) -> None:
        if connection.raw_market:
            raise ConnectionRejected(1008, "Subscriptions are fixed on this endpoint")
        if not isinstance(values, list) or len(values) > len(WebSocketTopic):
            raise ConnectionRejected(1008, "Invalid topic subscription")
        try:
            topics = {WebSocketTopic(str(value)) for value in values}
        except ValueError as error:
            raise ConnectionRejected(1008, "Unknown WebSocket topic") from error
        self._authorize(connection.principal, topics)
        async with self._lock:
            allowed, _ = self._rate_allowed(
                self._subscribes,
                connection.connection_id,
                self._settings.ws_subscribe_rate_limit,
                self._settings.ws_subscribe_rate_window_seconds,
            )
            if not allowed:
                self._metrics["rejected_limits"] += 1
                raise ConnectionRejected(4429, "Subscribe rate limit exceeded")
            added = topics - connection.topics
            connection.topics = topics
        if WebSocketTopic.MARKET in added:
            self._publisher_wakeup.set()
        await self._offer_control(connection, {
            "type": "subscribed",
            "topics": sorted(topic.value for topic in topics),
        })
        await self._prime(connection, added)

    async def _prime(
        self,
        connection: HubConnection,
        topics: set[WebSocketTopic] | None = None,
    ) -> None:
        selected = topics if topics is not None else connection.topics
        async with self._lock:
            cached = [
                (topic, item) for (topic, symbol), item in self._cache.items()
                if topic in selected
                and (topic is not WebSocketTopic.MARKET or symbol == connection.symbol)
            ]
        for topic, item in cached:
            self._offer(
                connection,
                self._event_payload(
                    connection, topic, item.payload, item.captured_at
                ),
                topic,
            )

    async def _receiver(self, connection: HubConnection) -> None:
        while not connection.close_event.is_set():
            message = await connection.websocket.receive_json()
            connection.last_activity_at = self._clock()
            if not isinstance(message, dict):
                self._request_close(connection, 1008, "JSON object required")
                return
            message_type = message.get("type")
            if message_type == "pong":
                connection.last_pong_at = self._clock()
            elif message_type == "ping":
                await self._offer_control(connection, {
                    "type": "pong", "timestamp": message.get("timestamp")
                })
            elif message_type == "subscribe":
                try:
                    await self._subscribe(connection, message.get("topics"))
                except ConnectionRejected as error:
                    self._request_close(connection, error.code, error.reason)
                    return
            else:
                self._request_close(connection, 1008, "Unsupported WebSocket message")
                return

    async def _sender(self, connection: HubConnection) -> None:
        while not connection.close_event.is_set():
            message = await connection.queue.get()
            try:
                await asyncio.wait_for(
                    connection.websocket.send_json(message.payload),
                    timeout=self._settings.ws_send_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self._request_close(connection, 1013, "Client send timeout")
                return
            latency_ms = (self._clock() - message.enqueued_at) * 1000
            self._metrics["sent_messages"] += 1
            self._metrics["delivery_latency_total_ms"] += latency_ms
            self._metrics["delivery_latency_max_ms"] = max(
                float(self._metrics["delivery_latency_max_ms"]), latency_ms
            )

    async def _watchdog(self, connection: HubConnection) -> None:
        now = self._clock()
        next_ping = now + self._settings.ws_heartbeat_interval_seconds
        next_revalidation = now + self._settings.ws_session_revalidate_seconds
        cadence = min(
            self._settings.ws_heartbeat_interval_seconds,
            self._settings.ws_heartbeat_timeout_seconds,
            self._settings.ws_session_revalidate_seconds,
            self._settings.ws_idle_timeout_seconds,
        )
        cadence = max(0.01, cadence / 2)
        while not connection.close_event.is_set():
            await asyncio.sleep(cadence)
            now = self._clock()
            if now - connection.last_activity_at >= self._settings.ws_idle_timeout_seconds:
                self._request_close(connection, 4408, "WebSocket idle timeout")
                return
            if (
                connection.last_ping_at > connection.last_pong_at
                and now - connection.last_ping_at
                >= self._settings.ws_heartbeat_timeout_seconds
            ):
                self._request_close(connection, 4408, "WebSocket heartbeat timeout")
                return
            if now >= next_ping and connection.last_ping_at <= connection.last_pong_at:
                connection.last_ping_at = now
                await self._offer_control(connection, {
                    "type": "ping",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                next_ping = now + self._settings.ws_heartbeat_interval_seconds
            if self._expired(connection.principal):
                self._request_close(connection, 4401, "Access token expired")
                return
            if now >= next_revalidation:
                try:
                    principal = await self._authenticator.authenticate_access(
                        connection.token
                    )
                except AuthenticationError:
                    self._request_close(connection, 4401, "Session expired or revoked")
                    return
                if (
                    principal.user_id != connection.principal.user_id
                    or principal.session_id != connection.principal.session_id
                ):
                    self._request_close(connection, 4401, "Session identity changed")
                    return
                try:
                    self._authorize(principal, connection.topics)
                except ConnectionRejected:
                    self._request_close(connection, 4403, "Topic permission revoked")
                    return
                connection.principal = principal
                next_revalidation = now + self._settings.ws_session_revalidate_seconds

    def _offer(
        self,
        connection: HubConnection,
        payload: dict[str, Any],
        topic: WebSocketTopic | None = None,
    ) -> None:
        if connection.close_event.is_set():
            return
        if connection.queue.full():
            with suppress(asyncio.QueueEmpty):
                connection.queue.get_nowait()
            connection.dropped_messages += 1
            self._metrics["dropped_messages"] += 1
        with suppress(asyncio.QueueFull):
            connection.queue.put_nowait(
                OutboundMessage(payload, self._clock(), topic)
            )
        if connection.dropped_messages >= self._settings.ws_slow_client_drop_limit:
            self._request_close(connection, 1013, "Slow client buffer exceeded")

    async def _offer_control(
        self, connection: HubConnection, payload: dict[str, Any]
    ) -> None:
        self._offer(connection, payload)

    @staticmethod
    def _event_payload(
        connection: HubConnection,
        topic: WebSocketTopic,
        payload: dict[str, Any],
        captured_at: datetime,
    ) -> dict[str, Any]:
        if connection.raw_market and topic is WebSocketTopic.MARKET:
            return payload
        if payload.get("type") == "error":
            return {**payload, "topic": topic.value}
        return {
            "type": "event",
            "topic": topic.value,
            "captured_at": captured_at.isoformat(),
            "data": payload,
        }

    async def _market_publisher(self) -> None:
        last_read: dict[str | None, float] = {}
        interval = self._settings.market_ws_interval_seconds
        while True:
            symbols = await self.active_market_symbols()
            if not symbols:
                self._publisher_wakeup.clear()
                if await self.active_market_symbols():
                    continue
                await self._publisher_wakeup.wait()
                continue
            now = self._clock()
            due = [
                symbol for symbol in symbols
                if now - last_read.get(symbol, float("-inf")) >= interval
            ]
            for symbol in due:
                started = perf_counter()
                try:
                    tick = await self._market_reader.get_tick(symbol)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # domain failures must not kill publisher
                    self._metrics["market_read_failures"] += 1
                    logger.warning(
                        "Market WebSocket publisher read failed: %s",
                        type(error).__name__,
                    )
                    await self.publish(
                        WebSocketTopic.MARKET,
                        {"type": "error", "detail": "Market data unavailable"},
                        symbol=symbol,
                    )
                else:
                    await self.publish(WebSocketTopic.MARKET, tick, symbol=symbol)
                finally:
                    completed = self._clock()
                    read_latency_ms = (perf_counter() - started) * 1000
                    last_read[symbol] = completed
                    self._market_read_times.append(completed)
                    self._metrics["market_reads"] += 1
                    self._metrics["market_read_latency_total_ms"] += read_latency_ms
                    self._metrics["market_read_latency_max_ms"] = max(
                        float(self._metrics["market_read_latency_max_ms"]),
                        read_latency_ms,
                    )
            active_last = [last_read[symbol] for symbol in symbols if symbol in last_read]
            delay = interval if not active_last else max(
                0.0, min(value + interval for value in active_last) - self._clock()
            )
            self._publisher_wakeup.clear()
            try:
                await asyncio.wait_for(self._publisher_wakeup.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    def _rate_allowed(
        self,
        store: dict[str, deque[float]],
        key: str,
        limit: int,
        window_seconds: float,
    ) -> tuple[bool, bool]:
        now = self._clock()
        history = store.get(key)
        max_entries = max(1024, self._settings.ws_max_total_connections * 8)
        if history is None:
            if len(store) >= max_entries:
                oldest = min(
                    store,
                    key=lambda candidate: store[candidate][-1]
                    if store[candidate] else float("-inf"),
                )
                store.pop(oldest, None)
            history = deque()
            store[key] = history
        self._prune(history, now, window_seconds)
        repeated = bool(history)
        if len(history) >= limit:
            return False, repeated
        history.append(now)
        return True, repeated

    @staticmethod
    def _prune(history: deque[float], now: float, window_seconds: float) -> None:
        cutoff = now - window_seconds
        while history and history[0] <= cutoff:
            history.popleft()

    @staticmethod
    def _authorize(principal: Principal, topics: set[WebSocketTopic]) -> None:
        for topic in topics:
            if not principal.has(TOPIC_PERMISSIONS[topic]):
                raise ConnectionRejected(4403, f"Missing permission for {topic.value}")

    @staticmethod
    def _expired(principal: Principal) -> bool:
        expires_at = principal.access_expires_at
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= datetime.now(timezone.utc)

    @staticmethod
    def _request_close(
        connection: HubConnection, code: int, reason: str
    ) -> None:
        if connection.close_event.is_set():
            return
        connection.close_code = code
        connection.close_reason = reason[:123]
        connection.close_event.set()
