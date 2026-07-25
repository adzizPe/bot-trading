from __future__ import annotations

import asyncio
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from functools import partial
from multiprocessing.connection import Connection
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Callable, Protocol


class ConnectorState(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    DEGRADED = "DEGRADED"
    TIMEOUT = "TIMEOUT"
    RECOVERING = "RECOVERING"
    FAILED = "FAILED"


class ConnectorError(RuntimeError):
    pass


class ConnectorTimeoutError(ConnectorError):
    def __init__(self, operation: str) -> None:
        super().__init__(f"MT5 vendor call timed out: {operation}")
        self.operation = operation


class ConnectorCallError(ConnectorError):
    def __init__(self, operation: str) -> None:
        super().__init__(f"MT5 vendor call failed: {operation}")
        self.operation = operation


class MT5ConnectorProtocol(Protocol):
    @property
    def state(self) -> ConnectorState: ...

    @property
    def generation(self) -> int: ...

    async def call(
        self, operation: str, *args: Any, timeout_seconds: float, **kwargs: Any
    ) -> Any: ...

    async def recover(self) -> None: ...

    async def stop(self) -> None: ...

    def mark_connected(self) -> None: ...

    def mark_degraded(self) -> None: ...

    def mark_failed(self) -> None: ...

    def record_retry(self) -> None: ...

    def metrics(self) -> dict[str, Any]: ...


class _Metrics:
    def __init__(self) -> None:
        self.calls = 0
        self.timeouts = 0
        self.retries = 0
        self.reconnects = 0
        self.failures = 0
        self.total_latency_ms = 0.0
        self.last_latency_ms = 0.0
        self.max_latency_ms = 0.0
        self.by_operation: dict[str, dict[str, float | int]] = {}

    def record(self, operation: str, latency_ms: float, outcome: str) -> None:
        self.calls += 1
        self.total_latency_ms += latency_ms
        self.last_latency_ms = latency_ms
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)
        bucket = self.by_operation.setdefault(
            operation,
            {"calls": 0, "timeouts": 0, "failures": 0, "latency_ms": 0.0},
        )
        bucket["calls"] = int(bucket["calls"]) + 1
        bucket["latency_ms"] = round(float(bucket["latency_ms"]) + latency_ms, 3)
        if outcome == "timeout":
            self.timeouts += 1
            bucket["timeouts"] = int(bucket["timeouts"]) + 1
        elif outcome == "failure":
            self.failures += 1
            bucket["failures"] = int(bucket["failures"]) + 1

    def snapshot(self, generation: int) -> dict[str, Any]:
        average = self.total_latency_ms / self.calls if self.calls else 0.0
        return {
            "generation": generation,
            "calls": self.calls,
            "timeouts": self.timeouts,
            "retries": self.retries,
            "reconnects": self.reconnects,
            "failures": self.failures,
            "average_latency_ms": round(average, 3),
            "last_latency_ms": round(self.last_latency_ms, 3),
            "max_latency_ms": round(self.max_latency_ms, 3),
            "operations": {key: dict(value) for key, value in self.by_operation.items()},
        }


def _portable(value: Any) -> Any:
    if hasattr(value, "_asdict"):
        return SimpleNamespace(**{
            key: _portable(item) for key, item in value._asdict().items()
        })
    if isinstance(value, list):
        return [_portable(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_portable(item) for item in value)
    if isinstance(value, dict):
        return {key: _portable(item) for key, item in value.items()}
    return value


def _process_worker(connection: Connection) -> None:
    from app.mt5.client import MetaTrader5Client

    client = MetaTrader5Client()
    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            request_id, operation, args, kwargs = command
            try:
                result = getattr(client, operation)(*args, **kwargs)
                connection.send((request_id, True, _portable(result)))
            except BaseException:
                connection.send((request_id, False, None))
    except (EOFError, OSError, BrokenPipeError):
        return
    finally:
        connection.close()


class ThreadMT5Connector:
    """Deterministic connector for injected clients; production uses a process."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._executor: ThreadPoolExecutor | None = None
        self._state = ConnectorState.DISCONNECTED
        self._generation = 0
        self._metrics = _Metrics()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> ConnectorState:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

    async def call(
        self, operation: str, *args: Any, timeout_seconds: float, **kwargs: Any
    ) -> Any:
        async with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix=f"mt5-test-connector-{self._generation}",
                )
            started = perf_counter()
            try:
                future = asyncio.get_running_loop().run_in_executor(
                    self._executor,
                    partial(getattr(self._client, operation), *args, **kwargs),
                )
                result = await asyncio.wait_for(future, timeout_seconds)
            except asyncio.TimeoutError as error:
                self._metrics.record(operation, (perf_counter() - started) * 1000, "timeout")
                self._state = ConnectorState.TIMEOUT
                self._generation += 1
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
                raise ConnectorTimeoutError(operation) from error
            except Exception as error:
                self._metrics.record(operation, (perf_counter() - started) * 1000, "failure")
                self._state = ConnectorState.DEGRADED
                raise ConnectorCallError(operation) from error
            self._metrics.record(operation, (perf_counter() - started) * 1000, "ok")
            return result

    async def recover(self) -> None:
        async with self._lock:
            self._state = ConnectorState.RECOVERING
            self._metrics.reconnects += 1
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"mt5-test-connector-{self._generation}",
            )

    async def stop(self) -> None:
        async with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
            self._state = ConnectorState.DISCONNECTED

    def mark_connected(self) -> None:
        self._state = ConnectorState.CONNECTED

    def mark_degraded(self) -> None:
        self._state = ConnectorState.DEGRADED

    def mark_failed(self) -> None:
        self._state = ConnectorState.FAILED

    def record_retry(self) -> None:
        self._metrics.retries += 1

    def metrics(self) -> dict[str, Any]:
        return self._metrics.snapshot(self._generation)


class ProcessMT5Connector:
    """Single Windows-spawn worker owning every MetaTrader5 vendor call."""

    def __init__(
        self, worker_target: Callable[[Connection], None] = _process_worker
    ) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._worker_target = worker_target
        self._process: multiprocessing.Process | None = None
        self._connection: Connection | None = None
        self._state = ConnectorState.DISCONNECTED
        self._generation = 0
        self._request_id = 0
        self._metrics = _Metrics()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> ConnectorState:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

    def _start_sync(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=self._worker_target,
            args=(child,),
            name=f"mt5-connector-{self._generation}",
            daemon=True,
        )
        process.start()
        child.close()
        self._connection = parent
        self._process = process

    def _roundtrip_sync(
        self,
        request_id: int,
        operation: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        self._start_sync()
        if self._connection is None:
            raise ConnectorCallError(operation)
        self._connection.send((request_id, operation, args, kwargs))
        if not self._connection.poll(timeout_seconds):
            raise ConnectorTimeoutError(operation)
        response_id, ok, payload = self._connection.recv()
        if response_id != request_id or not ok:
            raise ConnectorCallError(operation)
        return payload

    async def call(
        self, operation: str, *args: Any, timeout_seconds: float, **kwargs: Any
    ) -> Any:
        async with self._lock:
            self._request_id += 1
            request_id = self._request_id
            started = perf_counter()
            try:
                result = await asyncio.to_thread(
                    self._roundtrip_sync,
                    request_id,
                    operation,
                    args,
                    kwargs,
                    timeout_seconds,
                )
            except ConnectorTimeoutError:
                self._metrics.record(operation, (perf_counter() - started) * 1000, "timeout")
                self._state = ConnectorState.TIMEOUT
                await asyncio.to_thread(self._terminate_sync)
                self._generation += 1
                raise
            except Exception as error:
                self._metrics.record(operation, (perf_counter() - started) * 1000, "failure")
                self._state = ConnectorState.DEGRADED
                await asyncio.to_thread(self._terminate_sync)
                self._generation += 1
                raise ConnectorCallError(operation) from error
            self._metrics.record(operation, (perf_counter() - started) * 1000, "ok")
            return result

    def _terminate_sync(self) -> None:
        connection, process = self._connection, self._process
        self._connection = None
        self._process = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is None:
            return
        if process.is_alive():
            process.terminate()
            process.join(0.5)
        if process.is_alive():
            process.kill()
            process.join(0.5)

    async def recover(self) -> None:
        async with self._lock:
            self._state = ConnectorState.RECOVERING
            self._metrics.reconnects += 1
            await asyncio.to_thread(self._terminate_sync)
            await asyncio.to_thread(self._start_sync)

    async def stop(self) -> None:
        async with self._lock:
            connection = self._connection
            if connection is not None:
                try:
                    connection.send(None)
                except (OSError, BrokenPipeError):
                    pass
            await asyncio.to_thread(self._terminate_sync)
            self._state = ConnectorState.DISCONNECTED

    def mark_connected(self) -> None:
        self._state = ConnectorState.CONNECTED

    def mark_degraded(self) -> None:
        self._state = ConnectorState.DEGRADED

    def mark_failed(self) -> None:
        self._state = ConnectorState.FAILED

    def record_retry(self) -> None:
        self._metrics.retries += 1

    def metrics(self) -> dict[str, Any]:
        return self._metrics.snapshot(self._generation)


def connector_for(client: Any) -> MT5ConnectorProtocol:
    if (
        client.__class__.__module__ == "app.mt5.client"
        and client.__class__.__name__ == "MetaTrader5Client"
    ):
        return ProcessMT5Connector()
    return ThreadMT5Connector(client)
