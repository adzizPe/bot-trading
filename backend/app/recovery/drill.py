from __future__ import annotations

from base64 import b64encode
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.config.settings import Settings
from app.recovery.backup import BackupService
from app.recovery.catalog import FilesystemCatalog, MANIFEST_FILENAME, RECEIPT_FILENAME
from app.recovery.alembic_compat import AlembicCompatibilityService
from app.recovery.config import RecoveryConfig
from app.recovery.offhost import OffHostCopyService
from app.recovery.restore import RestoreService
from app.recovery.smoke import ReadOnlyRepositorySmokeChecker, RepositorySmokeResult
from app.recovery.status import DRILL_RESULT_PREFIX, age_seconds, target_met
from app.recovery.types import (
    BackupLifecycleStatus,
    ExitCode,
    FailureReason,
    OffHostStatus,
    RestoreStatus,
)
from app.recovery.verification import BackupVerifier

DRILL_STAGES = (
    "CREATE_DATABASE",
    "SEED_BASELINE",
    "BACKUP",
    "VERIFY",
    "OFFHOST_COPY",
    "DESTROY_SOURCE",
    "RESTORE",
    "POST_RESTORE_CHECKS",
    "COMPARE_DATA",
    "RPO_RTO_EVIDENCE",
    "SECRET_SCAN",
    "ZERO_TRADING_GUARD",
    "CLEANUP",
)
_FORBIDDEN_MODULE_TOKENS = frozenset(
    {
        "main",
        "mt5",
        "order",
        "demo",
        "trading",
        "strategy",
        "risk",
        "paper",
        "backtest",
        "safety",
    }
)


class DrillStageStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"


class DrillFault(Protocol):
    def __call__(self, stage: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DrillStageResult:
    name: str
    status: DrillStageStatus
    elapsed_seconds: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DrillResult:
    drill_id: UUID
    started_at: datetime
    completed_at: datetime
    success: bool
    exit_code: int
    stages: tuple[DrillStageResult, ...]
    backup_id: UUID | None
    backup_seconds: str | None
    restore_seconds: str | None
    integrity_check: str | None
    revision: str | None
    baseline_fingerprint: str | None
    restored_fingerprint: str | None
    offhost_checksum_verified: bool
    rpo_actual_seconds: int | None
    rpo_target_seconds: int
    rpo_met: bool
    rto_target_seconds: int
    rto_met: bool
    trading_guard_calls: int
    failed_stage: str | None = None
    failure_reason: FailureReason | None = None
    schema_version: int = 1

    def as_dict(self) -> dict[str, object]:
        return {
            "backup_id": str(self.backup_id) if self.backup_id else None,
            "backup_seconds": self.backup_seconds,
            "baseline_fingerprint": self.baseline_fingerprint,
            "completed_at": _timestamp(self.completed_at),
            "drill_id": str(self.drill_id),
            "exit_code": self.exit_code,
            "failed_stage": self.failed_stage,
            "failure_reason": self.failure_reason.value
            if self.failure_reason
            else None,
            "integrity_check": self.integrity_check,
            "offhost_checksum_verified": self.offhost_checksum_verified,
            "restored_fingerprint": self.restored_fingerprint,
            "restore_seconds": self.restore_seconds,
            "revision": self.revision,
            "rpo_actual_seconds": self.rpo_actual_seconds,
            "rpo_met": self.rpo_met,
            "rpo_target_seconds": self.rpo_target_seconds,
            "rto_met": self.rto_met,
            "rto_target_seconds": self.rto_target_seconds,
            "schema_version": self.schema_version,
            "stages": [
                {
                    "elapsed_seconds": item.elapsed_seconds,
                    "name": item.name,
                    "status": item.status.value,
                }
                for item in self.stages
            ],
            "started_at": _timestamp(self.started_at),
            "success": self.success,
            "trading_guard_calls": self.trading_guard_calls,
        }


class _StageFailure(Exception):
    def __init__(self, stage: str, reason: FailureReason) -> None:
        super().__init__(stage)
        self.stage = stage
        self.reason = reason


class RestoreDrillRunner:
    """Run a production-service restore drill wholly inside a temporary workspace."""

    def __init__(
        self,
        result_catalog: FilesystemCatalog,
        migration_scripts: Path,
        temp_root: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        key_factory: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        if not temp_root.is_absolute() or not migration_scripts.is_absolute():
            raise ValueError("drill roots must be absolute")
        self.result_catalog = result_catalog
        self.migration_scripts = migration_scripts
        self.temp_root = temp_root
        self._clock = clock
        self._utcnow = utcnow
        self._key_factory = key_factory

    def run(self, *, fault: DrillFault | None = None) -> DrillResult:
        drill_id = uuid4()
        started_at = self._utcnow()
        started_modules = frozenset(sys.modules)
        self.temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix="recovery-drill-", dir=self.temp_root))
        key = self._key_factory(32)
        if not isinstance(key, bytes) or len(key) != 32:
            shutil.rmtree(workspace, ignore_errors=True)
            raise ValueError("drill key factory must return 32 random bytes")
        config = self._config(workspace, key)
        catalog = FilesystemCatalog(config.local_root)
        compatibility = AlembicCompatibilityService(self.migration_scripts)
        smoke_checker = ReadOnlyRepositorySmokeChecker()
        stages = {
            name: DrillStageResult(name, DrillStageStatus.NOT_RUN)
            for name in DRILL_STAGES
        }
        state: dict[str, object] = {
            "backup_id": None,
            "backup_seconds": None,
            "restore_seconds": None,
            "integrity": None,
            "revision": None,
            "baseline": None,
            "restored": None,
            "offhost": False,
            "rpo_actual": None,
            "rpo_met": False,
            "rto_met": False,
            "trading_calls": 0,
        }
        failure: _StageFailure | None = None
        restore_started: float | None = None
        manifest = None
        try:
            self._stage(
                stages,
                "CREATE_DATABASE",
                fault,
                lambda: self._create_database(config, compatibility.repository_head),
            )
            baseline = self._stage(
                stages,
                "SEED_BASELINE",
                fault,
                lambda: self._seed_and_fingerprint(
                    config, smoke_checker, compatibility.repository_head
                ),
            )
            state["baseline"] = _fingerprint(baseline)
            backup_result = self._stage(
                stages,
                "BACKUP",
                fault,
                lambda: BackupService(
                    config, catalog, clock=self._clock, utcnow=self._utcnow
                ).run(key=key, database_revision=compatibility.repository_head),
            )
            manifest = backup_result.manifest
            if manifest.status is not BackupLifecycleStatus.VALID:
                raise _StageFailure(
                    "BACKUP", manifest.failure_reason or FailureReason.INTERNAL_FAILURE
                )
            state["backup_id"] = manifest.backup_id
            state["backup_seconds"] = manifest.metrics.backup_seconds
            verified = self._stage(
                stages,
                "VERIFY",
                fault,
                lambda: BackupVerifier(
                    catalog,
                    compatibility,
                    smoke_checker,
                    clock=self._clock,
                    utcnow=self._utcnow,
                ).verify(manifest.backup_id, key=key),
            )
            manifest = verified.manifest
            if manifest.status is not BackupLifecycleStatus.VALID:
                raise _StageFailure(
                    "VERIFY", manifest.failure_reason or FailureReason.INTERNAL_FAILURE
                )
            copied = self._stage(
                stages,
                "OFFHOST_COPY",
                fault,
                lambda: OffHostCopyService(
                    catalog,
                    config.offhost_root or workspace / "offhost",
                    source_database=config.source_database,
                    operation_timeout_seconds=config.operation_timeout.total_seconds(),
                    utcnow=self._utcnow,
                ).copy(manifest.backup_id),
            )
            state["offhost"] = (
                copied.receipt.status is OffHostStatus.VERIFIED
                and copied.receipt.source_checksum_sha256
                == copied.receipt.destination_checksum_sha256
            )
            self._stage(
                stages,
                "DESTROY_SOURCE",
                fault,
                lambda: self._destroy_source(config.source_database),
            )
            restore_started = self._clock()
            outcome = self._stage(
                stages,
                "RESTORE",
                fault,
                lambda: RestoreService(
                    config,
                    catalog,
                    compatibility,
                    smoke_checker,
                    clock=self._clock,
                    utcnow=self._utcnow,
                ).restore(
                    manifest.backup_id, key=key, dry_run=False, first_restore=True
                ),
            )
            if outcome.result.status is not RestoreStatus.RESTORED:
                raise _StageFailure(
                    "RESTORE",
                    outcome.result.failure_reason or FailureReason.INTERNAL_FAILURE,
                )
            restored = self._stage(
                stages,
                "POST_RESTORE_CHECKS",
                fault,
                lambda: self._post_checks(config, compatibility, smoke_checker),
            )
            state["integrity"], state["revision"], restored_smoke = restored
            state["restored"] = _fingerprint(restored_smoke)
            self._stage(
                stages,
                "COMPARE_DATA",
                fault,
                lambda: self._compare(baseline, restored_smoke),
            )
            state["restore_seconds"] = (
                f"{max(0.0, self._clock() - restore_started):.6f}"
            )
            self._stage(
                stages,
                "RPO_RTO_EVIDENCE",
                fault,
                lambda: self._timing_evidence(config, manifest, state),
            )
            self._stage(
                stages, "SECRET_SCAN", fault, lambda: self._scan_secrets(workspace, key)
            )
            self._stage(
                stages,
                "ZERO_TRADING_GUARD",
                fault,
                lambda: self._zero_trading_guard(started_modules),
            )
        except _StageFailure as error:
            failure = error
        except BaseException:
            current = next(
                (
                    name
                    for name, item in stages.items()
                    if item.status is DrillStageStatus.FAIL
                ),
                "CREATE_DATABASE",
            )
            failure = _StageFailure(current, FailureReason.INTERNAL_FAILURE)

        finally:
            cleanup_started = self._clock()
            try:
                shutil.rmtree(workspace)
                if fault is not None:
                    fault("CLEANUP")
                stages["CLEANUP"] = DrillStageResult(
                    "CLEANUP",
                    DrillStageStatus.PASS,
                    f"{max(0.0, self._clock() - cleanup_started):.6f}",
                )
            except BaseException:
                shutil.rmtree(workspace, ignore_errors=True)
                stages["CLEANUP"] = DrillStageResult(
                    "CLEANUP",
                    DrillStageStatus.FAIL,
                    f"{max(0.0, self._clock() - cleanup_started):.6f}",
                )
                failure = _StageFailure("CLEANUP", FailureReason.FILESYSTEM)
        if (
            failure is not None
            and stages[failure.stage].status is not DrillStageStatus.FAIL
        ):
            previous = stages[failure.stage]
            stages[failure.stage] = DrillStageResult(
                previous.name, DrillStageStatus.FAIL, previous.elapsed_seconds
            )
        success = failure is None and all(
            item.status is DrillStageStatus.PASS for item in stages.values()
        )
        result = DrillResult(
            drill_id=drill_id,
            started_at=started_at,
            completed_at=self._utcnow(),
            success=success,
            exit_code=int(
                ExitCode.SUCCESS if success else ExitCode.RESTORE_OR_DRILL_FAILURE
            ),
            stages=tuple(stages[name] for name in DRILL_STAGES),
            backup_id=state["backup_id"]
            if isinstance(state["backup_id"], UUID)
            else None,
            backup_seconds=str(state["backup_seconds"])
            if state["backup_seconds"] is not None
            else None,
            restore_seconds=str(state["restore_seconds"])
            if state["restore_seconds"] is not None
            else None,
            integrity_check=str(state["integrity"])
            if state["integrity"] is not None
            else None,
            revision=str(state["revision"]) if state["revision"] is not None else None,
            baseline_fingerprint=str(state["baseline"])
            if state["baseline"] is not None
            else None,
            restored_fingerprint=str(state["restored"])
            if state["restored"] is not None
            else None,
            offhost_checksum_verified=bool(state["offhost"]),
            rpo_actual_seconds=state["rpo_actual"]
            if isinstance(state["rpo_actual"], int)
            else None,
            rpo_target_seconds=int(config.rpo.total_seconds()),
            rpo_met=bool(state["rpo_met"]),
            rto_target_seconds=int(config.rto.total_seconds()),
            rto_met=bool(state["rto_met"]),
            trading_guard_calls=int(state["trading_calls"]),
            failed_stage=failure.stage if failure else None,
            failure_reason=failure.reason if failure else None,
        )
        self._persist(result)
        return result

    def _stage(
        self,
        stages: dict[str, DrillStageResult],
        name: str,
        fault: DrillFault | None,
        action: Callable[[], object],
    ) -> object:
        started = self._clock()
        try:
            if fault is not None:
                fault(name)
            value = action()
        except _StageFailure:
            stages[name] = DrillStageResult(
                name, DrillStageStatus.FAIL, f"{max(0.0, self._clock() - started):.6f}"
            )
            raise
        except BaseException as error:
            stages[name] = DrillStageResult(
                name, DrillStageStatus.FAIL, f"{max(0.0, self._clock() - started):.6f}"
            )
            raise _StageFailure(name, _stage_reason(name)) from error
        stages[name] = DrillStageResult(
            name, DrillStageStatus.PASS, f"{max(0.0, self._clock() - started):.6f}"
        )
        return value

    def _config(self, workspace: Path, key: bytes) -> RecoveryConfig:
        source = workspace / "drill.db"
        settings = Settings(
            _env_file=None,
            app_env="test",
            database_url=f"sqlite+aiosqlite:///{source.as_posix()}",
            backup_local_directory=workspace / "local",
            backup_offhost_directory=workspace / "offhost",
            backup_encryption_key=b64encode(key).decode("ascii"),
            backup_busy_timeout_seconds=1,
            backup_operation_timeout_seconds=30,
        )
        return RecoveryConfig.from_settings(settings)

    @staticmethod
    def _create_database(config: RecoveryConfig, revision: str) -> None:
        with closing(sqlite3.connect(config.source_database)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "CREATE TABLE alembic_version(version_num TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
            connection.execute(
                "CREATE TABLE roles(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
            )
            connection.execute(
                "CREATE TABLE signals(id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, score INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE safety_events(id INTEGER PRIMARY KEY, event_type TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE authentication_audit_events(id INTEGER PRIMARY KEY, event_type TEXT NOT NULL)"
            )
            connection.commit()

    @staticmethod
    def _seed_and_fingerprint(
        config: RecoveryConfig, smoke: ReadOnlyRepositorySmokeChecker, revision: str
    ) -> RepositorySmokeResult:
        with closing(sqlite3.connect(config.source_database)) as connection:
            connection.executemany(
                "INSERT INTO roles(name) VALUES (?)", (("operator",), ("auditor",))
            )
            connection.executemany(
                "INSERT INTO signals(symbol, score) VALUES (?, ?)",
                (("XAUUSD", 91), ("XAUUSD", 73)),
            )
            connection.execute(
                "INSERT INTO safety_events(event_type) VALUES ('DRILL_SAFE')"
            )
            connection.execute(
                "INSERT INTO authentication_audit_events(event_type) VALUES ('DRILL_LOGIN')"
            )
            connection.commit()
        return smoke.check(config.source_database, expected_revision=revision)

    @staticmethod
    def _destroy_source(source: Path) -> None:
        source.unlink()
        Path(f"{source}-wal").unlink(missing_ok=True)
        Path(f"{source}-shm").unlink(missing_ok=True)

    @staticmethod
    def _post_checks(
        config: RecoveryConfig,
        compatibility: AlembicCompatibilityService,
        smoke: ReadOnlyRepositorySmokeChecker,
    ) -> tuple[str, str, RepositorySmokeResult]:
        with closing(sqlite3.connect(config.source_database)) as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_key = connection.execute("PRAGMA foreign_key_check").fetchone()
        if integrity != "ok" or foreign_key is not None:
            raise RuntimeError("post-restore database checks failed")
        decision = compatibility.inspect_candidate(config.source_database)
        result = smoke.check(
            config.source_database, expected_revision=decision.target_revision
        )
        return integrity, decision.target_revision, result

    @staticmethod
    def _compare(
        expected: RepositorySmokeResult, actual: RepositorySmokeResult
    ) -> None:
        if expected != actual:
            raise RuntimeError("restored representative data does not match baseline")

    def _timing_evidence(
        self, config: RecoveryConfig, manifest: object, state: dict[str, object]
    ) -> None:
        verified_at = getattr(getattr(manifest, "verification"), "verified_at")
        if verified_at is None or state["restore_seconds"] is None:
            raise RuntimeError("timing evidence is incomplete")
        rpo_actual = age_seconds(self._utcnow(), verified_at)
        state["rpo_actual"] = rpo_actual
        state["rpo_met"] = target_met(rpo_actual, int(config.rpo.total_seconds()))
        state["rto_met"] = target_met(
            str(state["restore_seconds"]), int(config.rto.total_seconds())
        )

    @staticmethod
    def _scan_secrets(workspace: Path, key: bytes) -> None:
        canaries = (b64encode(key), key.hex().encode("ascii"))
        names = {MANIFEST_FILENAME, RECEIPT_FILENAME, "status.json"}
        for path in workspace.rglob("*"):
            if not path.is_file() or (
                path.name not in names and path.suffix not in {".json", ".jsonl"}
            ):
                continue
            content = path.read_bytes()
            if any(canary in content for canary in canaries):
                raise RuntimeError("secret marker found in metadata")

    @staticmethod
    def _zero_trading_guard(started_modules: frozenset[str]) -> None:
        imported = frozenset(sys.modules) - started_modules
        forbidden = [
            name
            for name in imported
            if name == "app.main"
            or (
                name.startswith("app.")
                and any(token in _FORBIDDEN_MODULE_TOKENS for token in name.split("."))
            )
        ]
        if forbidden:
            raise RuntimeError("prohibited dependency crossed recovery boundary")

    def _persist(self, result: DrillResult) -> None:
        self.result_catalog.initialize()
        path = (
            self.result_catalog.operations_root
            / f"{DRILL_RESULT_PREFIX}{result.drill_id}.json"
        )
        _atomic_json_write(path, result.as_dict())


def _fingerprint(result: RepositorySmokeResult) -> str:
    digest = hashlib.sha256()
    digest.update(result.revision.encode("utf-8"))
    for table in result.tables:
        digest.update(table.table.encode("ascii"))
        digest.update(str(table.bounded_count).encode("ascii"))
        digest.update(table.fingerprint_sha256.encode("ascii"))
    return digest.hexdigest()


def _stage_reason(stage: str) -> FailureReason:
    return {
        "BACKUP": FailureReason.INTERNAL_FAILURE,
        "VERIFY": FailureReason.INTEGRITY_FAILED,
        "OFFHOST_COPY": FailureReason.OFFHOST_UNAVAILABLE,
        "RESTORE": FailureReason.INTEGRITY_FAILED,
        "POST_RESTORE_CHECKS": FailureReason.INTEGRITY_FAILED,
        "COMPARE_DATA": FailureReason.INTEGRITY_FAILED,
        "CLEANUP": FailureReason.FILESYSTEM,
    }.get(stage, FailureReason.INTERNAL_FAILURE)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)
