from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID, uuid4

from app.recovery.catalog import FilesystemCatalog, RECEIPT_FILENAME
from app.recovery.leases import OperationLease
from app.recovery.types import (
    ARTIFACT_FILENAME,
    MANIFEST_FILENAME,
    BackupLifecycleStatus,
    BackupManifest,
    OffHostStatus,
    RPOClass,
)

RESTORE_LEASE_FILENAME = "restore.lease"
TRASH_DIRECTORY = ".trash"
_TRANSACTION_FILENAME = "transaction.json"
_MANAGED_FILES = (ARTIFACT_FILENAME, RECEIPT_FILENAME, MANIFEST_FILENAME)
_PARTIAL_FILES = (
    f"{ARTIFACT_FILENAME}.partial",
    f"{MANIFEST_FILENAME}.partial",
    f"{RECEIPT_FILENAME}.partial",
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class RetentionFault(Protocol):
    def __call__(self, stage: str, backup_id: UUID) -> None: ...


class RetentionAction(str, Enum):
    KEEP = "KEEP"
    DELETE = "DELETE"
    SKIP = "SKIP"


@dataclass(frozen=True, slots=True)
class RetentionPlanItem:
    backup_id: UUID
    completed_at: datetime
    age_seconds: int
    classes: tuple[RPOClass, ...]
    action: RetentionAction
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    generated_at: datetime
    items: tuple[RetentionPlanItem, ...]
    skipped_entries: int = 0

    @property
    def deletion_candidates(self) -> tuple[RetentionPlanItem, ...]:
        return tuple(
            item for item in self.items if item.action is RetentionAction.DELETE
        )

    @property
    def kept(self) -> tuple[RetentionPlanItem, ...]:
        return tuple(item for item in self.items if item.action is RetentionAction.KEEP)


@dataclass(frozen=True, slots=True)
class RetentionSummary:
    dry_run: bool
    kept: int
    eligible: int
    deleted: int
    skipped: int
    failures: int
    plan: RetentionPlan


class GFSRetentionPlanner:
    """Pure deterministic UTC GFS planner driven only by managed manifests."""

    def __init__(
        self,
        catalog: FilesystemCatalog,
        *,
        daily: int = 7,
        weekly: int = 4,
        monthly: int = 3,
        offhost_required: bool = True,
        offhost_root: Path | None = None,
        restore_lease_checker: Callable[[UUID], bool] | None = None,
    ) -> None:
        if min(daily, weekly, monthly) <= 0:
            raise ValueError("retention counts must be positive")
        self.catalog = catalog
        self.daily = daily
        self.weekly = weekly
        self.monthly = monthly
        self.offhost_required = offhost_required
        self.offhost_root = (
            Path(os.path.abspath(offhost_root)) if offhost_root is not None else None
        )
        self._restore_lease_checker = restore_lease_checker

    def plan(self, *, now: datetime | None = None) -> RetentionPlan:
        moment = now or datetime.now(timezone.utc)
        _require_utc(moment)
        manifests, skipped = self._read_managed_manifests()
        valid = sorted(
            (
                item
                for item in manifests
                if item.status is BackupLifecycleStatus.VALID
                and item.completed_at is not None
            ),
            key=lambda item: (-item.completed_at.timestamp(), str(item.backup_id)),
        )
        classes = self._select_classes(valid)
        latest = valid[0].backup_id if valid else None
        items: list[RetentionPlanItem] = []
        for manifest in sorted(
            manifests,
            key=lambda item: (
                -(item.completed_at or item.created_at).timestamp(),
                str(item.backup_id),
            ),
        ):
            timestamp = manifest.completed_at or manifest.created_at
            assigned = classes.get(manifest.backup_id, ())
            reasons: list[str] = []
            unsafe = self._unsafe_backup(manifest.backup_id)
            if unsafe:
                action = RetentionAction.SKIP
                reasons.append("UNSAFE_PATH")
            else:
                if assigned:
                    reasons.append("GFS_CLASS")
                if manifest.backup_id == latest:
                    reasons.append("LATEST_VALID")
                if manifest.status is not BackupLifecycleStatus.VALID:
                    reasons.append("NON_VALID_LIFECYCLE")
                if not manifest.verification.all_passed:
                    reasons.append("VERIFICATION_INCOMPLETE")
                if manifest.offhost.status is OffHostStatus.COPYING:
                    reasons.append("ACTIVE_COPY")
                if self._restore_leased(manifest.backup_id):
                    reasons.append("RESTORE_LEASE")
                if (
                    self.offhost_required
                    and manifest.status is BackupLifecycleStatus.VALID
                    and manifest.offhost.status is not OffHostStatus.VERIFIED
                ):
                    reasons.append("OFFHOST_REQUIRED")
                if self._partial_transfer_exists(manifest.backup_id):
                    reasons.append("ACTIVE_PARTIAL")
                action = RetentionAction.KEEP if reasons else RetentionAction.DELETE
            items.append(
                RetentionPlanItem(
                    backup_id=manifest.backup_id,
                    completed_at=timestamp,
                    age_seconds=max(0, int((moment - timestamp).total_seconds())),
                    classes=assigned,
                    action=action,
                    reasons=tuple(reasons),
                )
            )
        return RetentionPlan(moment, tuple(items), skipped)

    def _select_classes(
        self, valid: list[BackupManifest]
    ) -> dict[UUID, tuple[RPOClass, ...]]:
        selected: dict[UUID, list[RPOClass]] = {}
        policies: tuple[tuple[RPOClass, int, Callable[[datetime], object]], ...] = (
            (RPOClass.DAILY, self.daily, lambda value: value.date()),
            (
                RPOClass.WEEKLY,
                self.weekly,
                lambda value: (value.isocalendar().year, value.isocalendar().week),
            ),
            (
                RPOClass.MONTHLY,
                self.monthly,
                lambda value: (value.year, value.month),
            ),
        )
        for classification, limit, bucket_for in policies:
            buckets: set[object] = set()
            for manifest in valid:
                completed = manifest.completed_at
                if completed is None:
                    continue
                bucket = bucket_for(completed)
                if bucket in buckets:
                    continue
                if len(buckets) >= limit:
                    break
                buckets.add(bucket)
                selected.setdefault(manifest.backup_id, []).append(classification)
        return {key: tuple(value) for key, value in selected.items()}

    def _read_managed_manifests(self) -> tuple[list[BackupManifest], int]:
        root = self.catalog.backups_root
        if not root.exists():
            return [], 0
        manifests: list[BackupManifest] = []
        skipped = 0
        for entry in sorted(root.iterdir(), key=lambda value: value.name):
            if entry.is_symlink() or not entry.is_dir():
                skipped += 1
                continue
            try:
                backup_id = UUID(entry.name)
                manifests.append(self.catalog.read_manifest(backup_id))
            except (OSError, ValueError, json.JSONDecodeError):
                skipped += 1
        return manifests, skipped

    def _restore_leased(self, backup_id: UUID) -> bool:
        if self._restore_lease_checker is not None:
            return bool(self._restore_lease_checker(backup_id))
        local_marker = self.catalog.backup_directory(backup_id) / RESTORE_LEASE_FILENAME
        lock_marker = self.catalog.locks_root / f"restore-{backup_id}.lock"
        return _regular_marker(local_marker) or _regular_marker(lock_marker)

    def _partial_transfer_exists(self, backup_id: UUID) -> bool:
        local = self.catalog.backup_directory(backup_id)
        if any(_regular_marker(local / filename) for filename in _PARTIAL_FILES):
            return True
        if self.offhost_root is None:
            return False
        remote = self.offhost_root / "backups" / str(backup_id)
        return _regular_marker(remote / f"{ARTIFACT_FILENAME}.partial")

    def _unsafe_backup(self, backup_id: UUID) -> bool:
        directory = self.catalog.backup_directory(backup_id)
        if _unsafe_node(directory):
            return True
        try:
            children = tuple(directory.iterdir())
        except OSError:
            return True
        return any(_unsafe_node(child) for child in children)


class GFSRetentionExecutor:
    """Lease-serialized retention with recheck and recoverable managed trash."""

    def __init__(
        self,
        planner: GFSRetentionPlanner,
        *,
        lease_timeout_seconds: float = 5.0,
    ) -> None:
        if lease_timeout_seconds < 0:
            raise ValueError("lease timeout must be non-negative")
        self.planner = planner
        self.catalog = planner.catalog
        self.lease_timeout_seconds = lease_timeout_seconds
        self.trash_root = self.catalog.root / TRASH_DIRECTORY

    def run(
        self,
        *,
        dry_run: bool,
        now: datetime | None = None,
        fault: RetentionFault | None = None,
    ) -> RetentionSummary:
        if dry_run:
            plan = self.planner.plan(now=now)
            return RetentionSummary(
                True,
                len(plan.kept),
                len(plan.deletion_candidates),
                0,
                plan.skipped_entries
                + sum(item.action is RetentionAction.SKIP for item in plan.items),
                0,
                plan,
            )
        lease = OperationLease(
            self.catalog.root,
            operation_id=f"retention-{uuid4()}",
            timeout_seconds=self.lease_timeout_seconds,
        )
        with lease:
            self.reconcile_trash()
            plan = self.planner.plan(now=now)
            failures = 0
            skipped = plan.skipped_entries + sum(
                item.action is RetentionAction.SKIP for item in plan.items
            )
            for item in plan.kept:
                try:
                    manifest = self.catalog.read_manifest(item.backup_id)
                    if (
                        manifest.status is BackupLifecycleStatus.VALID
                        and manifest.rpo_class != item.classes
                    ):
                        self.catalog.write_manifest(
                            replace(manifest, rpo_class=item.classes)
                        )
                except (OSError, ValueError):
                    failures += 1
            deleted = 0
            for candidate in plan.deletion_candidates:
                current = self.planner.plan(now=now)
                current_item = next(
                    (
                        item
                        for item in current.items
                        if item.backup_id == candidate.backup_id
                    ),
                    None,
                )
                if (
                    current_item is None
                    or current_item.action is not RetentionAction.DELETE
                ):
                    skipped += 1
                    continue
                try:
                    self._delete_managed(current_item.backup_id, fault=fault)
                    deleted += 1
                except (OSError, ValueError, RuntimeError):
                    failures += 1
            return RetentionSummary(
                False,
                len(plan.kept),
                len(plan.deletion_candidates),
                deleted,
                skipped,
                failures,
                plan,
            )

    def reconcile_trash(self) -> None:
        if not self.trash_root.exists() or self.trash_root.is_symlink():
            return
        for transaction in sorted(
            self.trash_root.iterdir(), key=lambda value: value.name
        ):
            if _unsafe_node(transaction) or not transaction.is_dir():
                continue
            metadata_path = transaction / _TRANSACTION_FILENAME
            try:
                metadata = _read_transaction(metadata_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            backup_id = UUID(metadata["backup_id"])
            filenames = tuple(metadata["files"])
            state = metadata["state"]
            staged = transaction / str(backup_id)
            if state == "PREPARED":
                destination = self.catalog.backup_directory(backup_id)
                if _unsafe_node(staged) or _unsafe_node(destination):
                    continue
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                complete = True
                for filename in filenames:
                    source = staged / filename
                    target = destination / filename
                    if source.exists():
                        if _unsafe_node(source) or target.exists():
                            complete = False
                            continue
                        os.replace(source, target)
                if complete:
                    _remove_transaction_metadata(metadata_path, staged, transaction)
            elif state == "COMMITTED":
                for filename in filenames:
                    target = staged / filename
                    if target.is_file() and not _unsafe_node(target):
                        target.unlink()
                _remove_transaction_metadata(metadata_path, staged, transaction)

    def _delete_managed(self, backup_id: UUID, *, fault: RetentionFault | None) -> None:
        directory = self.catalog.backup_directory(backup_id)
        if _unsafe_node(directory) or not directory.is_dir():
            raise RuntimeError("retention candidate path is unsafe")
        children = tuple(directory.iterdir())
        if any(_unsafe_node(child) for child in children):
            raise RuntimeError("retention candidate contains an unsafe node")
        files = tuple(
            filename for filename in _MANAGED_FILES if (directory / filename).is_file()
        )
        if MANIFEST_FILENAME not in files:
            raise RuntimeError("retention candidate lost its source-of-truth manifest")
        self.trash_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        transaction = self.trash_root / str(uuid4())
        staged = transaction / str(backup_id)
        staged.mkdir(mode=0o700, parents=True)
        metadata_path = transaction / _TRANSACTION_FILENAME
        metadata = {
            "schema_version": 1,
            "backup_id": str(backup_id),
            "files": list(files),
            "state": "PREPARED",
        }
        _atomic_json_write(metadata_path, metadata)
        _inject(fault, "before_move", backup_id)
        for filename in files:
            os.replace(directory / filename, staged / filename)
            _inject(fault, "after_move", backup_id)
        _inject(fault, "before_commit", backup_id)
        metadata["state"] = "COMMITTED"
        _atomic_json_write(metadata_path, metadata)
        _inject(fault, "after_commit", backup_id)
        for filename in files:
            target = staged / filename
            if target.is_file() and not _unsafe_node(target):
                target.unlink()
            _inject(fault, "during_purge", backup_id)
        _remove_transaction_metadata(metadata_path, staged, transaction)
        try:
            directory.rmdir()
        except OSError:
            pass


RetentionPlanner = GFSRetentionPlanner
RetentionExecutor = GFSRetentionExecutor


def _inject(fault: RetentionFault | None, stage: str, backup_id: UUID) -> None:
    if fault is not None:
        fault(stage, backup_id)


def _regular_marker(path: Path) -> bool:
    return path.is_file() and not _unsafe_node(path)


def _unsafe_node(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _require_utc(value: datetime) -> None:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset().total_seconds() != 0
    ):
        raise ValueError("retention time must be timezone-aware UTC")


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    partial = path.with_name(f"{path.name}.partial")
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def _read_transaction(path: Path) -> dict[str, object]:
    if _unsafe_node(path):
        raise ValueError("unsafe transaction metadata")
    value = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid transaction metadata")
    try:
        UUID(value["backup_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid transaction backup ID") from error
    files = value.get("files")
    if (
        not isinstance(files, list)
        or not files
        or any(item not in _MANAGED_FILES for item in files)
        or len(set(files)) != len(files)
        or value.get("state") not in {"PREPARED", "COMMITTED"}
    ):
        raise ValueError("invalid transaction file set")
    return value


def _remove_transaction_metadata(
    metadata: Path, staged: Path, transaction: Path
) -> None:
    metadata.unlink(missing_ok=True)
    try:
        staged.rmdir()
    except OSError:
        pass
    try:
        transaction.rmdir()
    except OSError:
        pass
