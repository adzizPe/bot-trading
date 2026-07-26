from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.operations.config import canonical_path
from app.operations.models import _SAFE_ID, _SHA256, hash_identity, utc_text


class RestoreHoldStatus(str, Enum):
    HELD = "HELD"
    RELEASED = "RELEASED"


class RestoreHoldRecord(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    status: RestoreHoldStatus
    change_id: str
    reason: str = Field(min_length=1, max_length=256)
    operator_identity_hash: str
    reviewer_identity_hash: str
    restore_id: str
    updated_at: datetime
    ambiguous: bool = False
    restore_result: str = "PENDING"
    evidence_valid: bool = False
    manual_start_completed: bool = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("change_id", "restore_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("restore hold identifier is invalid")
        return value

    @field_validator("operator_identity_hash", "reviewer_identity_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("restore hold identity must be hashed")
        return value

    @field_validator("updated_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("restore hold timestamp must be UTC")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_release(self) -> "RestoreHoldRecord":
        if self.status is RestoreHoldStatus.RELEASED:
            if self.operator_identity_hash == self.reviewer_identity_hash:
                raise ValueError("hold release requires reviewer separation")
            if self.restore_result != "SUCCESS" or not self.evidence_valid:
                raise ValueError("hold release requires successful restore evidence")
        if self.manual_start_completed and self.status is not RestoreHoldStatus.RELEASED:
            raise ValueError("manual start can complete only after hold release")
        return self

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        payload["updated_at"] = utc_text(self.updated_at)
        return json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")

    @classmethod
    def fail_closed(cls, reason: str = "ambiguous-state") -> "RestoreHoldRecord":
        return cls(
            status=RestoreHoldStatus.HELD,
            change_id="ambiguous",
            reason=reason,
            operator_identity_hash=hash_identity("unknown-operator"),
            reviewer_identity_hash=hash_identity("unknown-reviewer"),
            restore_id="unknown",
            updated_at=datetime.now(timezone.utc),
            ambiguous=True,
        )


class RestoreHoldStore:
    """Atomic sidecar store; any uncertain read is interpreted as HELD."""

    def __init__(self, path: Path, *, active_sqlite: Path | None = None) -> None:
        self.path = canonical_path(path)
        self.partial_path = self.path.with_name(f"{self.path.name}.partial")
        if active_sqlite is not None:
            database = canonical_path(active_sqlite)
            if self.path == database or self.path.parent == database.parent:
                raise ValueError("restore hold sidecar must be isolated from active SQLite")

    def read(self) -> RestoreHoldRecord | None:
        try:
            self.partial_path.stat()
        except FileNotFoundError:
            pass
        except OSError:
            return RestoreHoldRecord.fail_closed("partial-state-unreadable")
        else:
            return RestoreHoldRecord.fail_closed("interrupted-atomic-write")
        try:
            payload = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            return RestoreHoldRecord.fail_closed("hold-state-unreadable")
        try:
            return RestoreHoldRecord.model_validate_json(payload)
        except (ValueError, TypeError):
            return RestoreHoldRecord.fail_closed("hold-state-malformed")

    load = read

    def write(self, record: RestoreHoldRecord) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            self.partial_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(record.canonical_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(self.partial_path, self.path)

    def enter(
        self,
        *,
        change_id: str,
        reason: str,
        operator_identity: str,
        reviewer_identity: str,
        restore_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> RestoreHoldRecord:
        record = RestoreHoldRecord(
            status=RestoreHoldStatus.HELD,
            change_id=change_id,
            reason=reason,
            operator_identity_hash=hash_identity(operator_identity),
            reviewer_identity_hash=hash_identity(reviewer_identity),
            restore_id=restore_id,
            updated_at=clock(),
        )
        self.write(record)
        return record

    def release(
        self,
        *,
        held: RestoreHoldRecord,
        operator_identity: str,
        reviewer_identity: str,
        restore_result: str,
        evidence_valid: bool,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> RestoreHoldRecord:
        if held.status is not RestoreHoldStatus.HELD or held.ambiguous:
            raise ValueError("only an unambiguous held record may be released")
        if restore_result != "SUCCESS" or not evidence_valid:
            raise ValueError("hold release requires successful restore evidence")
        record = RestoreHoldRecord(
            status=RestoreHoldStatus.RELEASED,
            change_id=held.change_id,
            reason="reviewed-release",
            operator_identity_hash=hash_identity(operator_identity),
            reviewer_identity_hash=hash_identity(reviewer_identity),
            restore_id=held.restore_id,
            updated_at=clock(),
            restore_result=restore_result,
            evidence_valid=evidence_valid,
        )
        self.write(record)
        return record

    def complete_manual_start(
        self,
        *,
        released: RestoreHoldRecord,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> RestoreHoldRecord:
        if released.status is not RestoreHoldStatus.RELEASED or released.ambiguous:
            raise ValueError("manual start completion requires released hold")
        record = released.model_copy(
            update={"manual_start_completed": True, "updated_at": clock()}
        )
        self.write(record)
        return record


class RestoreHoldGuard:
    """Shared fail-closed gate for every lifecycle path that could start services."""

    def __init__(self, store: RestoreHoldStore) -> None:
        self.store = store

    def allows(self, *, automatic: bool) -> bool:
        record = self.store.read()
        if record is None:
            return True
        if record.status is RestoreHoldStatus.HELD or record.ambiguous:
            return False
        return record.manual_start_completed or not automatic

    async def authorize_start(self, automatic: bool) -> bool:
        return self.allows(automatic=automatic)

    async def startup_completed(self, automatic: bool) -> None:
        if automatic:
            return
        record = self.store.read()
        if record is not None and record.status is RestoreHoldStatus.RELEASED:
            self.store.complete_manual_start(released=record)

    def complete_manual_start(self) -> RestoreHoldRecord:
        record = self.store.read()
        if record is None:
            raise ValueError("no released restore hold exists")
        return self.store.complete_manual_start(released=record)
