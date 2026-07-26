from __future__ import annotations

import argparse
from base64 import b64encode
from dataclasses import asdict
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
import sys
from typing import Any, Sequence
from uuid import UUID

from pydantic import ValidationError

from app.config.settings import Settings
from app.recovery.artifact import (
    ArtifactAuthenticationError,
    ArtifactError,
    ArtifactFormatError,
)
from app.recovery.backup import BackupService
from app.recovery.catalog import FilesystemCatalog
from app.recovery.alembic_compat import (
    AlembicCompatibilityError,
    AlembicCompatibilityService,
)
from app.recovery.config import RecoveryConfig
from app.recovery.drill import RestoreDrillRunner
from app.recovery.keys import EncryptionKeyError, load_encryption_key
from app.recovery.leases import LeaseUnavailableError
from app.recovery.offhost import OffHostCopyError, OffHostCopyService
from app.recovery.restore import RestoreService
from app.recovery.retention import GFSRetentionExecutor, GFSRetentionPlanner
from app.recovery.smoke import ReadOnlyRepositorySmokeChecker, RepositorySmokeError
from app.recovery.status import StatusService
from app.recovery.types import (
    BackupLifecycleStatus,
    ExitCode,
    FailureReason,
    RestoreStatus,
)
from app.recovery.verification import BackupVerificationError, BackupVerifier

_COMMANDS = frozenset(
    {"backup", "verify", "copy-offhost", "retention", "restore", "drill", "status"}
)
_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
_MAX_SUMMARY_BYTES = 4096


class CliInputError(ValueError):
    """Sanitized command-line validation failure."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliInputError("command parameters are invalid")


def _backup_id(value: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, AttributeError) as error:
        raise CliInputError("backup ID is invalid") from error


def _parser() -> _Parser:
    parser = _Parser(prog="recovery", add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup", add_help=False)
    verify = commands.add_parser("verify", add_help=False)
    verify.add_argument("--backup-id", required=True, type=_backup_id)
    copy = commands.add_parser("copy-offhost", add_help=False)
    copy.add_argument("--backup-id", required=True, type=_backup_id)
    retention = commands.add_parser("retention", add_help=False)
    retention.add_argument("--dry-run", action="store_true")
    restore = commands.add_parser("restore", add_help=False)
    restore.add_argument("--backup-id", required=True, type=_backup_id)
    restore.add_argument("--dry-run", action="store_true")
    restore.add_argument("--first-restore", action="store_true")
    commands.add_parser("drill", add_help=False)
    commands.add_parser("status", add_help=False)
    return parser


def _settings() -> Settings:
    # The CLI must never use Settings' configured dotenv files. Key material is
    # deliberately loaded separately by app.recovery.keys.
    return Settings(_env_file=None, backup_encryption_key=None)


def _config(settings: Settings, key: bytes | None) -> RecoveryConfig:
    if key is not None:
        encoded = b64encode(key).decode("ascii")
        return RecoveryConfig.from_settings(settings, encryption_key=encoded)
    keyless = settings.model_copy(update={"backup_encryption_required": False})
    return RecoveryConfig.from_settings(keyless)


def _key(settings: Settings, *, interactive: bool) -> bytes:
    return load_encryption_key(
        env_name=settings.backup_encryption_key_env,
        interactive=interactive,
    )


def _catalog(config: RecoveryConfig) -> FilesystemCatalog:
    catalog = FilesystemCatalog(config.local_root)
    catalog.initialize()
    return catalog


def _compatibility() -> AlembicCompatibilityService:
    return AlembicCompatibilityService(_MIGRATIONS)


def _execute(args: argparse.Namespace) -> tuple[ExitCode, dict[str, Any]]:
    settings = _settings()
    command = str(args.command)
    needs_key = command in {"backup", "verify", "restore"}
    interactive = command == "restore" and bool(
        getattr(sys.stdin, "isatty", lambda: False)()
    )
    key = _key(settings, interactive=interactive) if needs_key else None
    config = _config(settings, key)
    catalog = _catalog(config)

    if command == "backup":
        compatibility = _compatibility()
        result = BackupService(config, catalog).run(
            key=key or b"", database_revision=compatibility.repository_head
        )
        manifest = result.manifest
        code = (
            ExitCode.SUCCESS
            if manifest.status is BackupLifecycleStatus.VALID
            else _reason_exit(manifest.failure_reason)
        )
        return code, {
            "backup_id": str(manifest.backup_id),
            "reason": _value(manifest.failure_reason),
            "status": manifest.status.value,
        }

    if command == "verify":
        result = BackupVerifier(
            catalog, _compatibility(), ReadOnlyRepositorySmokeChecker()
        ).verify(args.backup_id, key=key or b"")
        manifest = result.manifest
        code = (
            ExitCode.SUCCESS
            if manifest.status is BackupLifecycleStatus.VALID
            else _reason_exit(manifest.failure_reason)
        )
        return code, {
            "backup_id": str(manifest.backup_id),
            "reason": _value(manifest.failure_reason),
            "status": manifest.status.value,
        }

    if command == "copy-offhost":
        if config.offhost_root is None:
            raise CliInputError("off-host destination is not configured")
        result = OffHostCopyService(
            catalog,
            config.offhost_root,
            source_database=config.source_database,
            operation_timeout_seconds=config.operation_timeout.total_seconds(),
        ).copy(args.backup_id)
        return ExitCode.SUCCESS, {
            "backup_id": str(result.manifest.backup_id),
            "reused": result.reused_artifact,
            "status": result.receipt.status.value,
        }

    if command == "retention":
        planner = GFSRetentionPlanner(
            catalog,
            daily=config.daily_retention,
            weekly=config.weekly_retention,
            monthly=config.monthly_retention,
            offhost_required=config.offhost_root is not None,
            offhost_root=config.offhost_root,
        )
        result = GFSRetentionExecutor(planner).run(dry_run=bool(args.dry_run))
        code = ExitCode.SUCCESS if result.failures == 0 else ExitCode.RETENTION_FAILURE
        return code, {
            "deleted": result.deleted,
            "dry_run": result.dry_run,
            "eligible": result.eligible,
            "failures": result.failures,
            "kept": result.kept,
            "skipped": result.skipped,
            "status": "PASS" if result.failures == 0 else "FAILED",
        }

    if command == "restore":
        outcome = RestoreService(
            config,
            catalog,
            _compatibility(),
            ReadOnlyRepositorySmokeChecker(),
        ).restore(
            args.backup_id,
            key=key or b"",
            dry_run=bool(args.dry_run),
            first_restore=bool(args.first_restore),
        )
        result = outcome.result
        code = (
            ExitCode.SUCCESS
            if result.status in {RestoreStatus.VALIDATED, RestoreStatus.RESTORED}
            else _reason_exit(result.failure_reason, restore=True)
        )
        return code, {
            "backup_id": str(result.backup_id),
            "dry_run": result.dry_run,
            "reason": _value(result.failure_reason),
            "restore_id": str(result.restore_id),
            "status": result.status.value,
        }

    if command == "drill":
        result = RestoreDrillRunner(
            catalog,
            _MIGRATIONS,
            config.local_root / ".drill-work",
        ).run()
        code = ExitCode.SUCCESS if result.success else ExitCode.RESTORE_OR_DRILL_FAILURE
        return code, {
            "drill_id": str(result.drill_id),
            "failed_stage": result.failed_stage,
            "reason": _value(result.failure_reason),
            "status": "PASS" if result.success else "FAILED",
        }

    if command == "status":
        status = StatusService(catalog, config).rebuild()
        return ExitCode.SUCCESS, {
            "recovery": _json_value(asdict(status)),
            "status": "PASS",
        }

    raise CliInputError("command is invalid")


def _reason_exit(reason: FailureReason | None, *, restore: bool = False) -> ExitCode:
    mapping = {
        FailureReason.CONFIGURATION_INVALID: ExitCode.CONFIGURATION_INVALID,
        FailureReason.SOURCE_UNSUPPORTED: ExitCode.CONFIGURATION_INVALID,
        FailureReason.SOURCE_LOCKED: ExitCode.SOURCE_LOCKED_OR_BACKEND_ACTIVE,
        FailureReason.BACKEND_ACTIVE: ExitCode.SOURCE_LOCKED_OR_BACKEND_ACTIVE,
        FailureReason.DISK_SPACE: ExitCode.FILESYSTEM_FAILURE,
        FailureReason.FILESYSTEM: ExitCode.FILESYSTEM_FAILURE,
        FailureReason.INTERRUPTED: ExitCode.FILESYSTEM_FAILURE,
        FailureReason.OPERATION_TIMEOUT: ExitCode.FILESYSTEM_FAILURE,
        FailureReason.CHECKSUM_MISMATCH: ExitCode.ARTIFACT_FAILURE,
        FailureReason.ARTIFACT_FORMAT: ExitCode.ARTIFACT_FAILURE,
        FailureReason.AUTHENTICATION_FAILED: ExitCode.ENCRYPTION_FAILURE,
        FailureReason.INTEGRITY_FAILED: ExitCode.INTEGRITY_FAILURE,
        FailureReason.REPOSITORY_SMOKE_FAILED: ExitCode.INTEGRITY_FAILURE,
        FailureReason.REVISION_INCOMPATIBLE: ExitCode.REVISION_FAILURE,
        FailureReason.OFFHOST_UNAVAILABLE: ExitCode.OFFHOST_FAILURE,
        FailureReason.OFFHOST_CHECKSUM_MISMATCH: ExitCode.OFFHOST_FAILURE,
        FailureReason.RETENTION_SAFETY: ExitCode.RETENTION_FAILURE,
        FailureReason.INTERNAL_FAILURE: ExitCode.INTERNAL_FAILURE,
    }
    if reason is None:
        return (
            ExitCode.RESTORE_OR_DRILL_FAILURE if restore else ExitCode.INTERNAL_FAILURE
        )
    return mapping.get(
        reason,
        ExitCode.RESTORE_OR_DRILL_FAILURE if restore else ExitCode.INTERNAL_FAILURE,
    )


def _exception_exit(error: BaseException, operation: str) -> ExitCode:
    if isinstance(error, (CliInputError, ValidationError)):
        return ExitCode.CONFIGURATION_INVALID
    if isinstance(error, EncryptionKeyError | ArtifactAuthenticationError):
        return ExitCode.ENCRYPTION_FAILURE
    if isinstance(error, LeaseUnavailableError):
        return ExitCode.LEASE_UNAVAILABLE
    if isinstance(error, OffHostCopyError):
        return ExitCode.OFFHOST_FAILURE
    if isinstance(error, AlembicCompatibilityError):
        return ExitCode.REVISION_FAILURE
    if isinstance(error, RepositorySmokeError):
        return ExitCode.INTEGRITY_FAILURE
    if isinstance(error, BackupVerificationError | ArtifactFormatError | ArtifactError):
        return ExitCode.ARTIFACT_FAILURE
    if isinstance(error, OSError):
        return ExitCode.FILESYSTEM_FAILURE
    if isinstance(error, ValueError):
        return ExitCode.CONFIGURATION_INVALID
    if operation == "retention":
        return ExitCode.RETENTION_FAILURE
    if operation in {"restore", "drill"}:
        return ExitCode.RESTORE_OR_DRILL_FAILURE
    return ExitCode.INTERNAL_FAILURE


def _value(value: Enum | None) -> str | None:
    return value.value if value is not None else None


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _operation(argv: Sequence[str]) -> str:
    return argv[0] if argv and argv[0] in _COMMANDS else "unknown"


def _emit(operation: str, code: ExitCode, details: dict[str, Any]) -> None:
    payload: dict[str, Any] = {
        "exit_code": int(code),
        "operation": operation,
        "success": code is ExitCode.SUCCESS,
    }
    payload.update({key: value for key, value in details.items() if value is not None})
    encoded = json.dumps(
        _json_value(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    if len(encoded.encode("ascii")) > _MAX_SUMMARY_BYTES:
        encoded = json.dumps(
            {
                "exit_code": int(code),
                "operation": operation,
                "status": "TRUNCATED",
                "success": code is ExitCode.SUCCESS,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    sys.stdout.write(encoded + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    operation = _operation(values)
    try:
        args = _parser().parse_args(values)
        operation = args.command
        code, details = _execute(args)
    except BaseException as error:
        code = _exception_exit(error, operation)
        details = {
            "reason": (
                "INPUT_INVALID"
                if code is ExitCode.CONFIGURATION_INVALID
                else "KEY_UNAVAILABLE_OR_INVALID"
                if code is ExitCode.ENCRYPTION_FAILURE
                else "LEASE_UNAVAILABLE"
                if code is ExitCode.LEASE_UNAVAILABLE
                else "OPERATION_FAILED"
            ),
            "status": "FAILED",
        }
    _emit(operation, code, details)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
