from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.backtest.engine import BacktestEngine
from app.backtest.exceptions import BacktestQueueFullError
from app.backtest.repository import BacktestRepository
from app.backtest.uploads import BacktestUploadStore
from app.config.settings import Settings
from app.database.base import Base

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


class OfflineManager:
    async def risk_snapshot(self, symbol: str) -> dict[str, Any]:
        return {"symbol": {
            "name": symbol, "digits": 2, "point": 0.01,
            "trade_tick_size": 0.01, "trade_tick_value": 1,
            "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01,
            "trade_stops_level": 0, "trade_freeze_level": 0,
            "trade_contract_size": 100,
        }}

    async def market_rates(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("benchmark must not use MT5")


def configuration(source: str, upload_id: str | None = None) -> dict[str, Any]:
    return {
        "symbol": "XAUUSD", "start_date": START.isoformat(),
        "end_date": (START + timedelta(days=365)).isoformat(),
        "timeframe": "M5", "initial_balance": 10000,
        "risk_per_trade_percent": 1, "maximum_open_positions": 1,
        "spread_mode": "FIXED", "fixed_spread_points": 2,
        "use_historical_spread": False, "slippage_points": 0,
        "commission_per_lot": 0, "swap_long_per_lot": 0,
        "swap_short_per_lot": 0, "minimum_risk_reward": 1.5,
        "trading_sessions": [], "strategy_name": "EMA_RSI_ATR_MTF_V1",
        "strategy_settings": {}, "risk_settings": {},
        "close_open_positions_at_end": True, "same_bar_policy": "SL_FIRST",
        "source": source, "csv_upload_id": upload_id,
    }


def generate_csv(path: Path, candles: int) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("timestamp,open,high,low,close,volume,spread\n")
        for index in range(candles):
            timestamp = START + timedelta(minutes=5 * index)
            price = 2000 + (index % 20) * 0.01
            handle.write(
                f"{timestamp.isoformat()},{price:.2f},{price + 1:.2f},"
                f"{price - 1:.2f},{price:.2f},10,2\n"
            )


async def benchmark(candles: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="backtest-benchmark-") as temporary:
        root = Path(temporary)
        db_path = root / "benchmark.db"
        csv_path = root / "generated.csv"
        generate_csv(csv_path, candles)
        settings = Settings(
            _env_file=None,
            max_backtest_jobs=1,
            max_pending_jobs=3,
            max_candles=max(100, min(250_000, candles)),
            max_csv_rows=max(100, min(250_000, candles)),
            max_date_range_days=365,
            max_memory_budget_mb=1024,
            job_timeout_minutes=30,
        )
        database = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with database.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        repository = BacktestRepository(
            async_sessionmaker(database, expire_on_commit=False)
        )
        uploads = BacktestUploadStore(settings, root / "uploads")
        engine = BacktestEngine(
            repository, OfflineManager(), settings, uploads  # type: ignore[arg-type]
        )
        staged = await uploads.stage_path(csv_path)
        before_size = db_path.stat().st_size
        repository.reset_write_count()
        tracemalloc.start()
        cpu_start = time.process_time()
        wall_start = time.perf_counter()
        job = await engine.submit(configuration("CSV", staged["upload_id"]))
        completion = engine.active_tasks[job["backtest_id"]]

        admitted = 1
        rejected = 0
        pending_ids: list[str] = []
        probe = configuration("MT5")
        probe["end_date"] = (START + timedelta(days=1)).isoformat()
        for _ in range(settings.max_pending_jobs):
            pending = await engine.submit(dict(probe))
            pending_ids.append(pending["backtest_id"])
            admitted += 1
        try:
            await engine.submit(dict(probe))
        except BacktestQueueFullError:
            rejected += 1
        for identifier in pending_ids:
            await engine.cancel(identifier)

        await completion
        wall_seconds = time.perf_counter() - wall_start
        cpu_seconds = time.process_time() - cpu_start
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        detail = await repository.get(job["backtest_id"])
        await engine.shutdown()
        after_size = db_path.stat().st_size
        await database.dispose()
        return {
            "candles_requested": candles,
            "candles_processed": detail["processed_candles"] if detail else 0,
            "status": detail["status"] if detail else "MISSING",
            "wall_seconds": round(wall_seconds, 6),
            "process_cpu_seconds": round(cpu_seconds, 6),
            "tracemalloc_peak_bytes": peak_bytes,
            "sqlite_write_count": repository.write_count,
            "sqlite_file_growth_bytes": after_size - before_size,
            "queue_admitted": admitted,
            "queue_rejected": rejected,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic offline backtest benchmark")
    parser.add_argument("--candles", type=int, default=10_000)
    arguments = parser.parse_args()
    if not 100 <= arguments.candles <= 250_000:
        parser.error("--candles must be between 100 and 250000")
    print(json.dumps(asyncio.run(benchmark(arguments.candles)), sort_keys=True))


if __name__ == "__main__":
    main()
