from __future__ import annotations

import asyncio
import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import UploadFile

from app.backtest.exceptions import HistoricalDataError
from app.backtest.historical import HistoricalDataService
from app.config.settings import BACKEND_DIR, Settings

UPLOAD_CHUNK_SIZE = 1024 * 1024
ALLOWED_CSV_MIME_TYPES = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "text/plain",
}
REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close"}
OPTIONAL_COLUMNS = {"volume", "tick_volume", "spread"}


class BacktestUploadStore:
    """Own generated CSV staging paths; caller filenames never affect filesystem paths."""

    def __init__(self, settings: Settings, root: Path | None = None) -> None:
        self.settings = settings
        self.root = (root or BACKEND_DIR / "data" / "backtest_uploads").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def stage_path(self, path: Path) -> dict[str, Any]:
        """Internal/test helper that still copies into generated canonical staging."""
        from starlette.datastructures import Headers

        with path.open("rb") as handle:
            upload = UploadFile(
                file=handle,
                filename=path.name,
                headers=Headers({"content-type": "text/csv"}),
            )
            return await self.stage(upload)

    async def stage(self, upload: UploadFile) -> dict[str, Any]:
        filename = upload.filename or ""
        if Path(filename).suffix.lower() != ".csv":
            raise HistoricalDataError("CSV upload must use the .csv extension")
        content_type = (upload.content_type or "").lower().split(";", 1)[0].strip()
        if content_type not in ALLOWED_CSV_MIME_TYPES:
            raise HistoricalDataError("CSV upload MIME type is not allowed")
        upload_id = uuid4().hex
        temporary = self._path(upload_id, ".part")
        target = self._path(upload_id, ".csv")
        size = 0
        try:
            with temporary.open("xb") as handle:
                while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
                    size += len(chunk)
                    if size > self.settings.max_csv_size_mb * 1024 * 1024:
                        raise HistoricalDataError("CSV upload exceeds max_csv_size_mb")
                    handle.write(chunk)
            metadata = await asyncio.to_thread(self._validate, temporary, upload_id, size)
            self._metadata_path(upload_id).write_text(
                json.dumps(metadata, sort_keys=True), encoding="utf-8"
            )
            os.replace(temporary, target)
            return self.public_metadata(metadata)
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            self._metadata_path(upload_id).unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

    def metadata(self, upload_id: str) -> dict[str, Any]:
        path = self._metadata_path(upload_id)
        data_path = self._path(upload_id, ".csv")
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise HistoricalDataError("CSV upload does not exist or is invalid") from error
        if not data_path.is_file():
            raise HistoricalDataError("CSV upload does not exist or is invalid")
        return metadata

    def csv_path(self, upload_id: str) -> Path:
        self.metadata(upload_id)
        return self._path(upload_id, ".csv")

    def rows(self, upload_id: str, *, limit: int) -> list[dict[str, Any]]:
        path = self.csv_path(upload_id)
        rows: list[dict[str, Any]] = []
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if len(rows) >= limit:
                        raise HistoricalDataError("CSV exceeds runtime candle limit")
                    rows.append(dict(row))
        except UnicodeDecodeError as error:
            raise HistoricalDataError("CSV must be valid UTF-8") from error
        except OSError as error:
            raise HistoricalDataError("CSV upload cannot be read") from error
        return rows

    def cleanup(self, upload_id: str | None) -> None:
        if not upload_id:
            return
        for suffix in (".csv", ".json", ".part"):
            try:
                self._path(upload_id, suffix).unlink(missing_ok=True)
            except (HistoricalDataError, OSError):
                pass

    def cleanup_orphans(self, active_upload_ids: set[str], *, older_than_hours: int = 24) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
        removed = 0
        for metadata_path in self.root.glob("*.json"):
            upload_id = metadata_path.stem
            if upload_id in active_upload_ids:
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(metadata["created_at"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                created = datetime.min.replace(tzinfo=timezone.utc)
            if created <= cutoff:
                self.cleanup(upload_id)
                removed += 1
        for partial in self.root.glob("*.part"):
            try:
                modified = datetime.fromtimestamp(partial.stat().st_mtime, timezone.utc)
                if modified <= cutoff:
                    partial.unlink(missing_ok=True)
            except OSError:
                pass
        for data_path in self.root.glob("*.csv"):
            upload_id = data_path.stem
            if upload_id in active_upload_ids or self._metadata_path(upload_id).exists():
                continue
            try:
                modified = datetime.fromtimestamp(data_path.stat().st_mtime, timezone.utc)
                if older_than_hours <= 0 or modified <= cutoff:
                    data_path.unlink(missing_ok=True)
                    removed += 1
            except (HistoricalDataError, OSError):
                pass
        return removed

    @staticmethod
    def public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "upload_id": metadata["upload_id"],
            "size": metadata["size"],
            "row_count": metadata["row_count"],
        }

    def staged_bytes(self, upload_ids: set[str]) -> int:
        total = 0
        for upload_id in upload_ids:
            try:
                total += int(self.metadata(upload_id)["size"])
            except HistoricalDataError:
                continue
        return total

    def _validate(self, path: Path, upload_id: str, size: int) -> dict[str, Any]:
        row_count = 0
        first_timestamp: str | None = None
        last_timestamp: str | None = None
        previous = None
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                fields = set(reader.fieldnames or [])
                if not REQUIRED_COLUMNS.issubset(fields):
                    raise HistoricalDataError(
                        "CSV requires timestamp,open,high,low,close headers"
                    )
                if not fields.issubset(REQUIRED_COLUMNS | OPTIONAL_COLUMNS):
                    raise HistoricalDataError("CSV contains unsupported headers")
                for source in reader:
                    row_count += 1
                    if row_count > min(self.settings.max_csv_rows, self.settings.max_candles):
                        raise HistoricalDataError("CSV exceeds max_csv_rows or max_candles")
                    candle = HistoricalDataService._candle(source, "M5")
                    HistoricalDataService.validate(
                        [candle], "M5", validate_gaps=False
                    )
                    if previous is not None and candle.timestamp <= previous:
                        raise HistoricalDataError("CSV timestamps must be strictly ascending")
                    previous = candle.timestamp
                    first_timestamp = first_timestamp or candle.timestamp.isoformat()
                    last_timestamp = candle.timestamp.isoformat()
        except UnicodeDecodeError as error:
            raise HistoricalDataError("CSV must be valid UTF-8") from error
        except csv.Error as error:
            raise HistoricalDataError("CSV structure is invalid") from error
        if row_count == 0:
            raise HistoricalDataError("CSV must contain at least one candle row")
        return {
            "upload_id": upload_id,
            "size": size,
            "row_count": row_count,
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _metadata_path(self, upload_id: str) -> Path:
        return self._path(upload_id, ".json")

    def _path(self, upload_id: str, suffix: str) -> Path:
        try:
            normalized = UUID(upload_id).hex
        except (ValueError, TypeError, AttributeError) as error:
            raise HistoricalDataError("CSV upload identifier is invalid") from error
        candidate = (self.root / f"{normalized}{suffix}").resolve()
        if candidate.parent != self.root:
            raise HistoricalDataError("CSV upload path is invalid")
        return candidate
