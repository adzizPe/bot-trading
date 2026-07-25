from __future__ import annotations

import argparse
import asyncio
import json
import time
from multiprocessing.connection import Connection
from statistics import mean
from types import SimpleNamespace

from app.mt5.connector import ConnectorTimeoutError, ProcessMT5Connector


def offline_process_worker(connection: Connection) -> None:
    try:
        while True:
            command = connection.recv()
            if command is None:
                return
            request_id, operation, _, _ = command
            if operation == "hang":
                time.sleep(10)
                continue
            connection.send((request_id, True, SimpleNamespace(connected=True)))
    except (EOFError, OSError, BrokenPipeError):
        return
    finally:
        connection.close()


async def benchmark(iterations: int) -> dict[str, object]:
    connector = ProcessMT5Connector(worker_target=offline_process_worker)
    await connector.call("ping", timeout_seconds=2)
    latencies: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        await connector.call("ping", timeout_seconds=0.25)
        latencies.append((time.perf_counter() - started) * 1000)

    timeout_started = time.perf_counter()
    try:
        await connector.call("hang", timeout_seconds=0.02)
    except ConnectorTimeoutError:
        pass
    timeout_elapsed = (time.perf_counter() - timeout_started) * 1000

    reconnect_started = time.perf_counter()
    await connector.recover()
    await connector.call("ping", timeout_seconds=2)
    connector.mark_connected()
    reconnect_elapsed = (time.perf_counter() - reconnect_started) * 1000
    await connector.stop()
    return {
        "iterations": iterations,
        "latency_average_ms": round(mean(latencies), 6),
        "latency_pseudo_max_ms": round(max(latencies), 6),
        "timeout_deadline_ms": 20,
        "timeout_observed_ms": round(timeout_elapsed, 6),
        "reconnect_ms": round(reconnect_elapsed, 6),
        "metrics": connector.metrics(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline MT5 connector benchmark")
    parser.add_argument("--iterations", type=int, default=1000)
    arguments = parser.parse_args()
    print(json.dumps(asyncio.run(benchmark(arguments.iterations)), sort_keys=True))


if __name__ == "__main__":
    main()
