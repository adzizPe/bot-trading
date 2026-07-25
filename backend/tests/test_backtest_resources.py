from __future__ import annotations

import asyncio
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers

from app.backtest.engine import BacktestEngine
from app.backtest.exceptions import (
    BacktestQueueFullError,
    BacktestValidationError,
    HistoricalDataError,
)
from app.backtest.repository import BacktestRepository
from app.backtest.uploads import BacktestUploadStore
from app.database.base import Base
from tests.test_backtest_engine import ReadOnlyManager, request
from tests.test_mt5_manager import make_settings

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


async def make_engine(tmp_path: Path, **overrides: object) -> tuple[Any, ...]:
    database = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with database.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = BacktestRepository(async_sessionmaker(database, expire_on_commit=False))
    settings = make_settings(**overrides)
    uploads = BacktestUploadStore(settings, tmp_path / "uploads")
    engine = BacktestEngine(
        repository, ReadOnlyManager(), settings, uploads  # type: ignore[arg-type]
    )
    return database, repository, engine


def csv_bytes(rows: int = 2, *, header: str | None = None) -> bytes:
    lines = [header or "timestamp,open,high,low,close,volume,spread"]
    for index in range(rows):
        timestamp = START + timedelta(minutes=index * 5)
        lines.append(f"{timestamp.isoformat()},100,101,99,100,1,2")
    return "\n".join(lines).encode()


def upload(data: bytes, filename: str = "rates.csv", mime: str = "text/csv") -> UploadFile:
    return UploadFile(
        io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": mime}),
    )


@pytest.mark.asyncio
async def test_csv_staging_is_canonical_validated_and_cleaned(tmp_path: Path) -> None:
    settings = make_settings()
    store = BacktestUploadStore(settings, tmp_path / "uploads")
    result = await store.stage(upload(csv_bytes(), "../../outside.csv"))
    assert set(result) == {"upload_id", "size", "row_count"}
    assert result["row_count"] == 2
    staged = store.csv_path(result["upload_id"])
    assert staged.parent == store.root
    assert staged.name == f"{result['upload_id']}.csv"
    assert not (tmp_path / "outside.csv").exists()
    store.cleanup(result["upload_id"])
    assert not staged.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "mime", "data", "message"),
    [
        ("rates.txt", "text/csv", csv_bytes(), ".csv extension"),
        ("rates.csv", "application/octet-stream", csv_bytes(), "MIME type"),
        ("rates.csv", "text/csv", b"time,open,high,low,close\n", "requires"),
        (
            "rates.csv",
            "text/csv",
            b"timestamp,open,high,low,close\n2026-01-05T00:00:00,1,2,1,1\n",
            "invalid fields",
        ),
    ],
)
async def test_csv_staging_rejects_invalid_inputs(
    tmp_path: Path, filename: str, mime: str, data: bytes, message: str
) -> None:
    store = BacktestUploadStore(make_settings(), tmp_path / "uploads")
    with pytest.raises(HistoricalDataError, match=message):
        await store.stage(upload(data, filename, mime))
    assert list(store.root.iterdir()) == []


@pytest.mark.asyncio
async def test_csv_staging_enforces_byte_and_row_limits(tmp_path: Path) -> None:
    byte_store = BacktestUploadStore(
        make_settings(max_csv_size_mb=1), tmp_path / "byte-uploads"
    )
    oversized = csv_bytes() + b" " * (1024 * 1024)
    with pytest.raises(HistoricalDataError, match="max_csv_size_mb"):
        await byte_store.stage(upload(oversized))

    row_store = BacktestUploadStore(
        make_settings(max_csv_rows=100), tmp_path / "row-uploads"
    )
    with pytest.raises(HistoricalDataError, match="max_csv_rows"):
        await row_store.stage(upload(csv_bytes(101)))


@pytest.mark.asyncio
async def test_queue_full_creates_no_row_and_fifo_is_preserved(tmp_path: Path) -> None:
    database, repository, engine = await make_engine(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def controlled(identifier: str, configuration: dict[str, Any]) -> None:
        order.append(identifier)
        started.set()
        await release.wait()
        await repository.finish(identifier, "COMPLETED")

    engine.run = controlled  # type: ignore[method-assign]
    first = await engine.submit(request("MT5"))
    await started.wait()
    second = await engine.submit(request("MT5"))
    third = await engine.submit(request("MT5"))
    fourth = await engine.submit(request("MT5"))
    with pytest.raises(BacktestQueueFullError, match="queue is full"):
        await engine.submit(request("MT5"))
    assert len(await repository.list()) == 4
    assert engine.queue_status()["pending_ids"] == [
        second["backtest_id"], third["backtest_id"], fourth["backtest_id"]
    ]
    release.set()
    while len(order) < 4:
        await asyncio.sleep(0)
    assert order == [
        first["backtest_id"], second["backtest_id"],
        third["backtest_id"], fourth["backtest_id"],
    ]
    await engine.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_submit_never_exceeds_running_plus_pending(tmp_path: Path) -> None:
    database, repository, engine = await make_engine(
        tmp_path, max_backtest_jobs=1, max_pending_jobs=2
    )
    release = asyncio.Event()

    async def controlled(identifier: str, configuration: dict[str, Any]) -> None:
        await release.wait()
        await repository.finish(identifier, "COMPLETED")

    engine.run = controlled  # type: ignore[method-assign]
    results = await asyncio.gather(
        *(engine.submit(request("MT5")) for _ in range(12)),
        return_exceptions=True,
    )
    accepted = [item for item in results if isinstance(item, dict)]
    rejected = [item for item in results if isinstance(item, BacktestQueueFullError)]
    assert len(accepted) == 3
    assert len(rejected) == 9
    assert len(await repository.list(limit=50)) == 3
    release.set()
    await engine.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_timeout_is_stable_failed_reason(tmp_path: Path) -> None:
    database, repository, engine = await make_engine(tmp_path)
    engine.settings.job_timeout_minutes = 0.001  # type: ignore[assignment]

    async def blocked(identifier: str, configuration: dict[str, Any]) -> None:
        await repository.mark_running(identifier, 1, {"name": "XAUUSD"})
        await asyncio.sleep(10)

    engine.run = blocked  # type: ignore[method-assign]
    job = await engine.submit(request("MT5"))
    completion = engine.active_tasks[job["backtest_id"]]
    await completion
    detail = await repository.get(job["backtest_id"])
    assert detail is not None
    assert detail["status"] == "FAILED"
    assert detail["error_message"] == "JOB_TIMEOUT"
    await engine.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_date_candle_and_memory_limits_create_no_rows(tmp_path: Path) -> None:
    database, repository, engine = await make_engine(
        tmp_path, max_date_range_days=1, max_candles=100
    )
    too_long = request("MT5")
    too_long["end_date"] = "2026-01-08"
    with pytest.raises(BacktestValidationError, match="date range"):
        await engine.submit(too_long)
    one_day = request("MT5")
    one_day["start_date"] = START
    one_day["end_date"] = START + timedelta(hours=10)
    with pytest.raises(BacktestValidationError, match="max_candles"):
        await engine.submit(one_day)
    assert await repository.list() == []
    await engine.shutdown()
    await database.dispose()

    database, repository, engine = await make_engine(
        tmp_path / "memory",
        max_candles=50_000,
        max_csv_rows=50_000,
        max_memory_budget_mb=64,
    )
    staged = await engine.upload_store.stage(upload(csv_bytes(40_000)))
    configuration = request("CSV", staged["upload_id"])
    with pytest.raises(BacktestValidationError, match="memory"):
        await engine.submit(configuration)
    assert await repository.list() == []
    assert not (engine.upload_store.root / f"{staged['upload_id']}.csv").exists()
    await engine.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_startup_recovery_fails_stale_and_overflow_fifo(tmp_path: Path) -> None:
    database, repository, engine = await make_engine(
        tmp_path, max_backtest_jobs=1, max_pending_jobs=1
    )
    stale = await repository.create(request("MT5"))
    await repository.mark_running(stale["backtest_id"], 1, {"name": "XAUUSD"})
    pending = [await repository.create(request("MT5")) for _ in range(3)]
    release = asyncio.Event()

    async def controlled(identifier: str, configuration: dict[str, Any]) -> None:
        await release.wait()
        await repository.finish(identifier, "COMPLETED")

    engine.run = controlled  # type: ignore[method-assign]
    await engine.start()
    stale_detail = await repository.get(stale["backtest_id"])
    overflow_detail = await repository.get(pending[2]["backtest_id"])
    assert stale_detail is not None and stale_detail["error_message"] == "SERVER_RESTARTED"
    assert overflow_detail is not None
    assert overflow_detail["error_message"] == "RECOVERY_QUEUE_OVERFLOW"
    assert engine.queue_status()["running_count"] + engine.queue_status()["pending_count"] == 2
    release.set()
    await engine.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_progress_writes_are_batched_and_upload_is_terminally_cleaned(
    tmp_path: Path,
) -> None:
    database, repository, engine = await make_engine(tmp_path)
    staged = await engine.upload_store.stage(upload(csv_bytes(1000)))
    configuration = request("CSV", staged["upload_id"])
    job = await repository.create(configuration)
    repository.reset_write_count()
    await engine.run(job["backtest_id"], configuration)
    detail = await repository.get(job["backtest_id"])
    assert detail is not None and detail["status"] == "COMPLETED"
    assert repository.write_count < 150
    assert not (engine.upload_store.root / f"{staged['upload_id']}.csv").exists()
    await database.dispose()


@pytest.mark.asyncio
async def test_repository_concurrent_progress_updates_do_not_corrupt_sqlite(
    tmp_path: Path,
) -> None:
    database = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'contention.db'}")
    async with database.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = BacktestRepository(async_sessionmaker(database, expire_on_commit=False))
    job = await repository.create(request("MT5"))
    await repository.mark_running(job["backtest_id"], 20, {"name": "XAUUSD"})
    await asyncio.gather(*(
        repository.update_progress(
            job["backtest_id"], index, 20, START + timedelta(minutes=5 * index)
        )
        for index in range(1, 21)
    ))
    detail = await repository.get(job["backtest_id"])
    assert detail is not None
    assert 1 <= detail["processed_candles"] <= 20
    assert detail["status"] == "RUNNING"
    await database.dispose()


def test_public_schema_has_no_csv_path_and_requires_upload_id() -> None:
    from app.schemas.backtest import BacktestRequest

    assert "csv_path" not in BacktestRequest.model_fields
    with pytest.raises(ValueError):
        BacktestRequest(
            symbol="XAUUSD",
            start_date="2026-01-01",
            end_date="2026-01-02",
            source="CSV",
        )
    with pytest.raises(ValueError):
        BacktestRequest(
            symbol="XAUUSD",
            start_date="2026-01-01",
            end_date="2026-01-02",
            source="MT5",
            csv_upload_id="0123456789abcdef0123456789abcdef",
        )


@pytest.mark.asyncio
async def test_csv_upload_is_single_use_and_rejected_reuse_is_not_deleted(
    tmp_path: Path,
) -> None:
    database, repository, engine = await make_engine(tmp_path)
    staged = await engine.upload_store.stage(upload(csv_bytes(12)))
    configuration = request("CSV", staged["upload_id"])
    started = asyncio.Event()
    release = asyncio.Event()

    async def controlled(identifier: str, payload: dict[str, Any]) -> None:
        started.set()
        await release.wait()
        await repository.finish(identifier, "COMPLETED")

    engine.run = controlled  # type: ignore[method-assign]
    first = await engine.submit(configuration)
    completion = engine.active_tasks[first["backtest_id"]]
    await started.wait()
    with pytest.raises(BacktestValidationError, match="already assigned"):
        await engine.submit(dict(configuration))
    assert engine.upload_store.csv_path(staged["upload_id"]).is_file()
    assert len(await repository.list()) == 1
    release.set()
    await completion
    assert not (engine.upload_store.root / f"{staged['upload_id']}.csv").exists()
    await engine.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_csv_non_overlapping_range_creates_no_row_and_cleans_upload(
    tmp_path: Path,
) -> None:
    database, repository, engine = await make_engine(tmp_path)
    staged = await engine.upload_store.stage(upload(csv_bytes(12)))
    configuration = request("CSV", staged["upload_id"])
    configuration["start_date"] = "2026-01-07"
    configuration["end_date"] = "2026-01-08"
    with pytest.raises(BacktestValidationError, match="does not overlap"):
        await engine.submit(configuration)
    assert await repository.list() == []
    assert not (engine.upload_store.root / f"{staged['upload_id']}.csv").exists()
    await engine.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_pending_cancel_immediately_releases_bounded_queue_slot(
    tmp_path: Path,
) -> None:
    database, repository, engine = await make_engine(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def controlled(identifier: str, configuration: dict[str, Any]) -> None:
        started.set()
        await release.wait()
        await repository.finish(identifier, "COMPLETED")

    engine.run = controlled  # type: ignore[method-assign]
    await engine.submit(request("MT5"))
    await started.wait()
    pending = [await engine.submit(request("MT5")) for _ in range(3)]
    cancelled = await engine.cancel(pending[1]["backtest_id"])
    assert cancelled["status"] == "CANCELLED"
    replacement = await engine.submit(request("MT5"))
    assert replacement["backtest_id"] in engine.queue_status()["pending_ids"]
    assert engine.queue_status()["pending_count"] == 3
    release.set()
    await engine.shutdown()
    assert engine.active_tasks == {}
    await database.dispose()


@pytest.mark.asyncio
async def test_aggregate_memory_reservation_rejects_before_create(tmp_path: Path) -> None:
    database, repository, engine = await make_engine(
        tmp_path,
        max_memory_budget_mb=64,
        max_candles=100_000,
        max_date_range_days=365,
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def controlled(identifier: str, configuration: dict[str, Any]) -> None:
        started.set()
        await release.wait()
        await repository.finish(identifier, "COMPLETED")

    engine.run = controlled  # type: ignore[method-assign]
    long_request = request("MT5")
    long_request["start_date"] = START.isoformat()
    long_request["end_date"] = (START + timedelta(days=30)).isoformat()
    await engine.submit(dict(long_request))
    await started.wait()
    await engine.submit(dict(long_request))
    await engine.submit(dict(long_request))
    with pytest.raises(BacktestValidationError, match="aggregate estimated memory"):
        await engine.submit(dict(long_request))
    assert len(await repository.list()) == 3
    release.set()
    await engine.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_worker_failure_does_not_strand_following_fifo_job(tmp_path: Path) -> None:
    database, repository, engine = await make_engine(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def controlled(identifier: str, configuration: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            raise RuntimeError("synthetic worker failure")
        await repository.finish(identifier, "COMPLETED")

    engine.run = controlled  # type: ignore[method-assign]
    first = await engine.submit(request("MT5"))
    first_completion = engine.active_tasks[first["backtest_id"]]
    await started.wait()
    second = await engine.submit(request("MT5"))
    second_completion = engine.active_tasks[second["backtest_id"]]
    release.set()
    await asyncio.gather(first_completion, second_completion)
    first_detail = await repository.get(first["backtest_id"])
    second_detail = await repository.get(second["backtest_id"])
    assert first_detail is not None and first_detail["error_message"] == "WORKER_FAILURE"
    assert second_detail is not None and second_detail["status"] == "COMPLETED"
    await engine.shutdown()
    await database.dispose()


def test_orphan_csv_without_metadata_is_cleaned(tmp_path: Path) -> None:
    store = BacktestUploadStore(make_settings(), tmp_path / "uploads")
    upload_id = "0123456789abcdef0123456789abcdef"
    orphan = store.root / f"{upload_id}.csv"
    orphan.write_bytes(b"orphan")
    assert store.cleanup_orphans(set(), older_than_hours=0) == 1
    assert not orphan.exists()