from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Protocol

from app.operations.config import OperationalPaths, canonical_path
from app.operations.controller import ControllerClock
from app.operations.lifecycle import DurableJsonStore
from app.operations.models import ReleaseManifest

_REQUIRED_ARTIFACTS = (
    "backend.bundle", "venv.bundle", "frontend.bundle", "nginx.conf",
    "service-manifest.json",
)


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActivationResult:
    release_id: str
    previous_release_id: str | None


class RecoveryAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class BackupValidity(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"


class OffHostStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class RecoveryPreflight:
    availability: RecoveryAvailability
    rpo_met: bool
    backup: BackupValidity = BackupValidity.INVALID
    off_host: OffHostStatus = OffHostStatus.UNVERIFIED


@dataclass(frozen=True)
class ChangeRecord:
    change_id: str
    operator: str
    reviewer: str
    maintenance_window: bool
    rollback_approved: bool
    last_known_good: str


@dataclass(frozen=True)
class UpdatePreflight:
    tests: bool
    venv: bool
    dist: bool
    nginx: bool
    configuration: bool
    certificate: bool
    process_count: bool
    readiness: bool
    trading_safe: bool
    capacity: bool
    writers_offline: bool
    recovery: RecoveryPreflight


    def validate(self, change: ChangeRecord, *, migration_required: bool) -> None:
        if not change.change_id or not change.last_known_good:
            raise ReleaseError("change record is incomplete")
        if change.operator == change.reviewer:
            raise ReleaseError("operator and reviewer must be different")
        if not change.maintenance_window:
            raise ReleaseError("maintenance window is not active")
        required = (
            self.tests, self.venv, self.dist, self.nginx, self.configuration,
            self.certificate, self.process_count, self.readiness,
            self.trading_safe, self.capacity,
        )
        if not all(required):
            raise ReleaseError("update preflight failed")
        if (
            self.recovery.availability is not RecoveryAvailability.AVAILABLE
            or not self.recovery.rpo_met
        ):
            raise ReleaseError("recovery preflight failed")
        if migration_required and (
            self.recovery.backup is not BackupValidity.VALID
            or self.recovery.off_host is not OffHostStatus.VERIFIED
            or not self.writers_offline
        ):
            raise ReleaseError("migration recovery/offline preflight failed")


class MigrationAdapter(Protocol):
    async def migrate(self, current_revision: str, target_revision: str) -> bool: ...


class CandidateChecks(Protocol):
    async def backend_readiness(self) -> bool: ...

    async def trading_safe(self) -> bool: ...

    async def nginx_valid(self) -> bool: ...

    async def https(self) -> bool: ...

    async def static(self) -> bool: ...

    async def api_read_only(self) -> bool: ...

    async def websocket(self) -> bool: ...

    async def proxied_release(self, release_id: str) -> bool: ...


class ReleaseRepository:
    """Stages immutable release bytes and atomically switches a tiny reference."""

    def __init__(self, paths: OperationalPaths) -> None:
        self.paths = paths
        self.active = DurableJsonStore(paths.active_reference)

    def stage(
        self, manifest: ReleaseManifest, artifacts: Mapping[str, bytes]
    ) -> Path:
        if set(artifacts) != set(_REQUIRED_ARTIFACTS):
            raise ReleaseError("release artifact set is incomplete")
        self._verify_digests(manifest, artifacts)
        target = canonical_path(self.paths.release_root / manifest.release_id)
        partial = target.with_name(f"{target.name}.partial")
        if target.exists() or partial.exists():
            raise ReleaseError("release identity is immutable or interrupted")
        partial.mkdir(mode=0o700, parents=True)
        try:
            for name in _REQUIRED_ARTIFACTS:
                path = partial / name
                path.write_bytes(artifacts[name])
                os.chmod(path, 0o400)
            (partial / "release-manifest.json").write_bytes(manifest.canonical_bytes())
            os.replace(partial, target)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        return target


    def activate(self, release_id: str, *, offline: bool) -> ActivationResult:
        if not offline:
            raise ReleaseError("activation requires maintenance and all writers offline")
        target = canonical_path(self.paths.release_root / release_id)
        manifest_path = target / "release-manifest.json"
        if not target.is_dir() or not manifest_path.is_file():
            raise ReleaseError("staged release is unavailable")
        manifest = ReleaseManifest.parse_bytes(manifest_path.read_bytes())
        if manifest.release_id != release_id:
            raise ReleaseError("staged release identity mismatch")
        previous = self.current_release_id()
        self.active.write({
            "release_id": release_id,
            "previous_release_id": previous,
        })
        return ActivationResult(release_id, previous)

    def current_release_id(self) -> str | None:
        value = self.active.read()
        if value is None:
            return None
        release_id = value.get("release_id")
        if value.get("ambiguous") is True or not isinstance(release_id, str):
            raise ReleaseError("active release reference is ambiguous")
        return release_id

    @staticmethod
    def _verify_digests(
        manifest: ReleaseManifest, artifacts: Mapping[str, bytes]
    ) -> None:
        backend = hashlib.sha256(
            artifacts["backend.bundle"]
            + b"\0"
            + artifacts["venv.bundle"]
            + b"\0"
            + artifacts["service-manifest.json"]
        ).hexdigest()
        expected = {
            "backend": (backend, manifest.backend_sha256),
            "frontend": (
                hashlib.sha256(artifacts["frontend.bundle"]).hexdigest(),
                manifest.frontend_sha256,
            ),
            "nginx": (
                hashlib.sha256(artifacts["nginx.conf"]).hexdigest(),
                manifest.nginx_sha256,
            ),
        }
        failed = [name for name, pair in expected.items() if pair[0] != pair[1]]
        if failed:
            raise ReleaseError(f"release digest mismatch: {','.join(failed)}")
        service = json.loads(artifacts["service-manifest.json"].decode("ascii"))
        if not isinstance(service, dict) or service.get("release_id") != manifest.release_id:
            raise ReleaseError("service manifest identity mismatch")


@dataclass(frozen=True)
class AcceptanceResult:
    accepted: bool
    reason: str


@dataclass
class CandidateAcceptance:
    checks: CandidateChecks
    clock: ControllerClock
    timeout_seconds: float = 600

    async def run(self, release_id: str) -> AcceptanceResult:
        started = self.clock.monotonic()
        checks: tuple[tuple[str, Callable[[], Awaitable[bool]]], ...] = (
            ("backend-readiness", self.checks.backend_readiness),
            ("trading-safe", self.checks.trading_safe),
            ("nginx-validation", self.checks.nginx_valid),
            ("https", self.checks.https),
            ("static", self.checks.static),
            ("api-read-only", self.checks.api_read_only),
            ("websocket", self.checks.websocket),
            ("release-identity", lambda: self.checks.proxied_release(release_id)),
        )
        for reason, check in checks:
            if self.clock.monotonic() - started > self.timeout_seconds:
                return AcceptanceResult(False, "acceptance-timeout")
            try:
                passed = bool(await check())
            except Exception:
                passed = False
            if not passed:
                return AcceptanceResult(False, reason)
        if self.clock.monotonic() - started > self.timeout_seconds:
            return AcceptanceResult(False, "acceptance-timeout")
        return AcceptanceResult(True, "accepted")


@dataclass(frozen=True)
class UpdateResult:
    succeeded: bool
    reason: str
    active_release: str | None


@dataclass
class ReleaseOrchestrator:
    repository: ReleaseRepository
    acceptance: CandidateAcceptance
    startup: Callable[[str], Awaitable[bool]]
    migration: MigrationAdapter | None = None
    authorize_start: Callable[[bool], Awaitable[bool]] | None = None

    async def update(
        self,
        *,
        candidate: ReleaseManifest,
        preflight: UpdatePreflight,
        change: ChangeRecord,
        current_revision: str,
        migration_required: bool,
    ) -> UpdateResult:
        if not await self._start_allowed():
            return UpdateResult(False, "restore-hold", self._current())
        try:
            preflight.validate(change, migration_required=migration_required)
        except ReleaseError as error:
            return UpdateResult(False, str(error), self._current())
        if migration_required:
            if self.migration is None:
                return UpdateResult(False, "migration-adapter-unavailable", self._current())
            try:
                migrated = await self.migration.migrate(
                    current_revision, candidate.alembic_revision
                )
            except Exception:
                migrated = False
            if not migrated:
                return UpdateResult(False, "migration-failed-offline", self._current())
        try:
            self.repository.activate(candidate.release_id, offline=True)
        except ReleaseError as error:
            return UpdateResult(False, str(error), self._current())
        try:
            started = bool(await self.startup(candidate.release_id))
        except Exception:
            started = False
        accepted = (
            await self.acceptance.run(candidate.release_id)
            if started
            else AcceptanceResult(False, "startup-failed")
        )
        if accepted.accepted:
            return UpdateResult(True, "updated", candidate.release_id)
        if change.rollback_approved:
            return await self.rollback(
                release_id=change.last_known_good,
                database_revision=candidate.alembic_revision,
            )
        return UpdateResult(False, f"candidate-{accepted.reason}", candidate.release_id)

    async def rollback(
        self, *, release_id: str, database_revision: str
    ) -> UpdateResult:
        if not await self._start_allowed():
            return UpdateResult(False, "restore-hold", self._current())
        manifest = self._manifest(release_id)
        if manifest is None:
            return UpdateResult(False, "lkg-unavailable", self._current())
        if manifest.alembic_revision != database_revision:
            return UpdateResult(False, "alembic-incompatible-offline", self._current())
        try:
            self.repository.activate(release_id, offline=True)
            started = bool(await self.startup(release_id))
        except Exception:
            started = False
        accepted = await self.acceptance.run(release_id) if started else None
        if accepted is None or not accepted.accepted:
            return UpdateResult(False, "rollback-failed-offline", self._current())
        return UpdateResult(True, "rolled-back", release_id)

    def _manifest(self, release_id: str) -> ReleaseManifest | None:
        path = self.repository.paths.release_root / release_id / "release-manifest.json"
        try:
            return ReleaseManifest.parse_bytes(path.read_bytes())
        except (OSError, ValueError):
            return None

    def _current(self) -> str | None:
        try:
            return self.repository.current_release_id()
        except ReleaseError:
            return None


    async def _start_allowed(self) -> bool:
        if self.authorize_start is None:
            return True
        try:
            return bool(await self.authorize_start(True))
        except Exception:
            return False