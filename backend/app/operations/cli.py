"""Offline-first operator CLI.

Exact process exit codes: 0 success; 2 invalid input; 3 policy rejection;
4 execution adapter required; 5 execution failure; 6 execution timeout; and
7 malformed/unsafe adapter output. Execution is possible only when ``main`` is
called with an explicit in-process adapter; this module never starts a process,
opens a shell, or calls a service/trading command.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
import json
from pathlib import Path
import re
import sys
from typing import Any, Protocol

from app.operations.config import OperationalPaths, ProcessManager, canonical_path
from app.operations.models import contains_sensitive_content
from app.operations.releases import (
    BackupValidity,
    ChangeRecord,
    OffHostStatus,
    RecoveryAvailability,
    RecoveryPreflight,
    ReleaseError,
    UpdatePreflight,
)
from app.operations.service_management import (
    LifecycleOwner,
    OwnershipClaim,
    PM2Options,
    ServiceComponent,
    ServiceDefinition,
    canonical_nssm_definitions,
    pm2_equivalent_definitions,
    validate_pm2_alternative,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_JSON_BYTES = 4096


class ExitCode(IntEnum):
    SUCCESS = 0
    INPUT_INVALID = 2
    POLICY_REJECTED = 3
    ADAPTER_REQUIRED = 4
    EXECUTION_FAILED = 5
    EXECUTION_TIMEOUT = 6
    MALFORMED_OUTPUT = 7


class CliInputError(ValueError):
    pass


class PolicyRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class OperationRequest:
    operation: str
    execute: bool
    evidence_id: str
    timeout_seconds: int
    process_manager: ProcessManager
    paths: OperationalPaths
    environment_source: Path
    definitions: tuple[ServiceDefinition, ...]
    identifiers: Mapping[str, str]
    plan: tuple[str, ...]


class OperationsAdapter(Protocol):
    """Explicit process-local mutation boundary supplied by trusted caller code."""

    def execute(self, request: OperationRequest) -> Mapping[str, Any]: ...


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliInputError("command parameters are invalid")


_OPERATIONS = (
    "setup", "preflight", "start", "stop", "restart", "reboot", "update",
    "rollback", "crash-loop", "restore-hold-status", "recovery-handoff",
    "restore-hold-release", "monitoring-check", "certificate-check",
    "capacity-check", "log-check", "hardening-check",
)
_READ_ONLY = frozenset({
    "preflight", "restore-hold-status", "monitoring-check", "certificate-check",
    "capacity-check", "log-check", "hardening-check",
})
_OPERATION_CATEGORIES = {
    "setup": "SERVICE_DEFINITION",
    "preflight": "PREFLIGHT",
    "start": "LIFECYCLE",
    "stop": "LIFECYCLE",
    "restart": "LIFECYCLE",
    "reboot": "LIFECYCLE",
    "update": "RELEASE",
    "rollback": "RELEASE",
    "crash-loop": "CRASH_LOOP",
    "restore-hold-status": "RESTORE_HOLD",
    "recovery-handoff": "RESTORE_HOLD",
    "restore-hold-release": "RESTORE_HOLD",
    "monitoring-check": "MONITORING",
    "certificate-check": "CERTIFICATE",
    "capacity-check": "CAPACITY",
    "log-check": "LOG_ROTATION",
    "hardening-check": "HARDENING",
}


def _category(operation: str) -> str:
    return _OPERATION_CATEGORIES[operation]


def _safe_id(value: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise CliInputError("identifier is invalid")
    return value


def _parser() -> _Parser:
    parser = _Parser(prog="operations", add_help=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in _OPERATIONS:
        command = commands.add_parser(operation, add_help=False)
        command.add_argument("--root", required=True, type=Path)
        command.add_argument("--process-manager", choices=("NSSM", "PM2"), default="NSSM")
        command.add_argument("--timeout-seconds", type=int, default=120)
        command.add_argument("--evidence-id", type=_safe_id)
        command.add_argument("--environment-source", type=Path)
        command.add_argument("--execute", action="store_true")
        command.add_argument("--release-id", type=_safe_id)
        command.add_argument("--change-id", type=_safe_id)
        command.add_argument("--operator-id", type=_safe_id)
        command.add_argument("--reviewer-id", type=_safe_id)
        command.add_argument("--last-known-good", type=_safe_id)
        command.add_argument("--database-revision", type=_safe_id)
        command.add_argument("--restore-id", type=_safe_id)
        command.add_argument("--service", type=_safe_id)
        command.add_argument("--category", type=_safe_id)
    return parser


def _paths(root: Path) -> OperationalPaths:
    base = canonical_path(root)
    return OperationalPaths(
        release_root=base / "releases",
        state_root=base / "state",
        evidence_root=base / "evidence",
        log_root=base / "logs",
        certificate_root=base / "certificates",
        nginx_root=base / "nginx",
        recovery_root=base / "recovery",
        active_reference=base / "active" / "release.json",
        active_sqlite=base / "data" / "application.db",
    )


def _definitions(
    paths: OperationalPaths, release_id: str, manager: ProcessManager
) -> tuple[ServiceDefinition, ...]:
    canonical = canonical_nssm_definitions(
        paths, release_directory=paths.release_root / release_id
    )
    if manager is ProcessManager.NSSM:
        return canonical
    definitions = pm2_equivalent_definitions(canonical)
    backend = next(
        item for item in definitions if item.component is ServiceComponent.BACKEND
    )
    options = tuple(
        PM2Options(
            service_name=item.service_name,
            interpreter=backend.executable if item.component is ServiceComponent.BACKEND else None,
        )
        for item in definitions
    )
    claims = tuple(
        OwnershipClaim(item.service_name, LifecycleOwner.PM2) for item in definitions
    )
    validate_pm2_alternative(
        definitions,
        options,
        selected_process_manager=manager,
        ownership_claims=claims,
    )
    return definitions


def _require(args: argparse.Namespace, *names: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        value = getattr(args, name)
        if value is None:
            raise CliInputError("required identifier is unavailable")
        result[name] = str(value)
    return result


def _validate_update(identifiers: Mapping[str, str]) -> None:
    change = ChangeRecord(
        change_id=identifiers["change_id"],
        operator=identifiers["operator_id"],
        reviewer=identifiers["reviewer_id"],
        maintenance_window=True,
        rollback_approved=True,
        last_known_good=identifiers["last_known_good"],
    )
    preflight = UpdatePreflight(
        tests=True, venv=True, dist=True, nginx=True, configuration=True,
        certificate=True, process_count=True, readiness=True, trading_safe=True,
        capacity=True, writers_offline=True,
        recovery=RecoveryPreflight(
            RecoveryAvailability.AVAILABLE, True, BackupValidity.VALID,
            OffHostStatus.VERIFIED,
        ),
    )
    try:
        preflight.validate(change, migration_required=False)
    except ReleaseError as error:
        raise PolicyRejected("update policy rejected") from error


def _operation_plan(operation: str) -> tuple[str, ...]:
    plans = {
        "setup": ("validate-native-definitions", "write-offline-definition-artifacts"),
        "preflight": ("validate-native-definitions", "validate-private-single-worker"),
        "start": ("preflight", "backend-start", "backend-gate", "edge-validate", "edge-start", "final-gate"),
        "stop": ("trading-safe-gate", "edge-stop", "backend-stop", "offline-proof"),
        "restart": ("planned-stop", "restore-hold-gate", "ordered-start"),
        "reboot": ("planned-stop", "cold-start-gate", "ordered-start"),
        "update": ("change-preflight", "offline-activate", "candidate-gates", "approved-rollback-on-failure"),
        "rollback": ("compatibility-gate", "offline-activate-lkg", "candidate-gates"),
        "crash-loop": ("restart-window-check", "quarantine-on-limit"),
        "restore-hold-status": ("read-hold-fail-closed",),
        "recovery-handoff": ("enter-hold", "edge-stop", "backend-stop", "offline-proof"),
        "restore-hold-release": ("verify-success-evidence", "separated-review", "manual-release"),
        "monitoring-check": ("non-overlap-guard", "read-only-probe", "bounded-result"),
        "certificate-check": ("non-overlap-guard", "read-only-certificate-assessment"),
        "capacity-check": ("non-overlap-guard", "read-only-capacity-assessment"),
        "log-check": ("non-overlap-guard", "read-only-rotation-plan"),
        "hardening-check": ("non-overlap-guard", "read-only-host-audit"),
    }
    return plans[operation]


def _request(args: argparse.Namespace) -> OperationRequest:
    if not 1 <= args.timeout_seconds <= 300:
        raise CliInputError("timeout is outside the bounded policy")
    paths = _paths(args.root)
    manager = ProcessManager(args.process_manager)
    release_id = args.release_id or "current-release"
    definitions = _definitions(paths, release_id, manager)
    default_source = paths.state_root / "protected" / "backend.environment"
    environment_source = canonical_path(args.environment_source or default_source)
    protected_root = canonical_path(paths.state_root / "protected")
    try:
        environment_source.relative_to(protected_root)
    except ValueError as error:
        raise PolicyRejected("environment metadata source is outside protected state") from error
    definitions = tuple(
        definition.model_copy(update={"environment_source": environment_source})
        for definition in definitions
    )

    operation = str(args.operation)
    identifiers: dict[str, str] = {}
    if operation == "update":
        identifiers = _require(
            args, "change_id", "operator_id", "reviewer_id", "release_id",
            "last_known_good",
        )
        _validate_update(identifiers)
    elif operation == "rollback":
        identifiers = _require(
            args, "change_id", "operator_id", "reviewer_id", "release_id",
            "database_revision",
        )
    elif operation == "recovery-handoff":
        identifiers = _require(
            args, "change_id", "operator_id", "reviewer_id", "restore_id",
        )
    elif operation == "restore-hold-release":
        identifiers = _require(
            args, "change_id", "operator_id", "reviewer_id", "restore_id",
        )
    elif operation == "crash-loop":
        identifiers = _require(args, "service")
    elif operation == "monitoring-check":
        identifiers = {"category": args.category or "HOST"}
    elif args.release_id is not None:
        identifiers = {"release_id": args.release_id}

    if (
        "operator_id" in identifiers
        and identifiers["operator_id"] == identifiers.get("reviewer_id")
    ):
        raise PolicyRejected("operator and reviewer separation is required")
    evidence_id = args.evidence_id or f"{operation}-plan"
    return OperationRequest(
        operation=operation,
        execute=bool(args.execute),
        evidence_id=evidence_id,
        timeout_seconds=args.timeout_seconds,
        process_manager=manager,
        paths=paths,
        environment_source=environment_source,
        definitions=definitions,
        identifiers=identifiers,
        plan=_operation_plan(operation),
    )


def _policy_details(request: OperationRequest) -> dict[str, Any]:
    root = request.paths.release_root.parent

    def relative(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.name

    def argument(value: str) -> str:
        candidate = Path(value)
        return relative(candidate) or candidate.name if candidate.is_absolute() else value

    services = []
    for definition in request.definitions:
        services.append({
            "arguments": [argument(value) for value in definition.arguments],
            "component": definition.component.value,
            "dependencies": list(definition.dependencies),
            "environment_source": {
                "kind": "PROTECTED_FILE_METADATA",
                "path": relative(definition.environment_source),
            },
            "executable": relative(definition.executable),
            "identity": definition.identity,
            "process_manager": definition.process_manager.value,
            "restart_policy": {
                "delay_seconds": definition.restart_policy.delay_seconds,
                "max_attempts": definition.restart_policy.max_attempts,
                "window_seconds": definition.restart_policy.window_seconds,
            },
            "service_name": definition.service_name,
            "shutdown_timeout_seconds": definition.shutdown_timeout_seconds,
            "startup_mode": definition.startup_mode.value,
            "static_root": relative(definition.static_root),
            "stderr_log": relative(definition.stderr_log),
            "stdout_log": relative(definition.stdout_log),
            "working_directory": relative(definition.working_directory),
        })
    details: dict[str, Any] = {
        "artifact_schema": 1,
        "mutation": False,
        "plan": list(request.plan),
        "read_only": request.operation in _READ_ONLY,
        "services": services,
        "timeout_seconds": request.timeout_seconds,
    }
    if request.identifiers:
        public_identifiers = {
            key: value
            for key, value in request.identifiers.items()
            if key not in {"operator_id", "reviewer_id"}
        }
        if public_identifiers:
            details["identifiers"] = public_identifiers
    return details


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        raise ValueError("adapter output is too deeply nested")
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Path):
        return value.name
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item, depth=depth + 1) for item in value]
    raise ValueError("adapter output contains an unsupported value")


def _adapter_result(
    adapter: OperationsAdapter, request: OperationRequest
) -> tuple[ExitCode, str, str, dict[str, Any]]:
    category = _category(request.operation)
    try:
        raw = adapter.execute(request)
    except TimeoutError:
        return ExitCode.EXECUTION_TIMEOUT, category, "TIMEOUT", {}
    except Exception:
        return ExitCode.EXECUTION_FAILED, category, "FAILED", {}
    if not isinstance(raw, Mapping) or set(raw) - {
        "exit_code", "category", "status", "details"
    }:
        raise ValueError("adapter output shape is invalid")
    try:
        code = ExitCode(int(raw.get("exit_code", ExitCode.SUCCESS)))
        supplied_category = str(raw.get("category", category))
        status = str(raw["status"])
        details = _json_value(raw.get("details", {}))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("adapter output is invalid") from error
    if supplied_category != category:
        raise ValueError("adapter category is inconsistent")
    if not isinstance(details, dict):
        raise ValueError("adapter details must be an object")
    if not _SAFE_ID.fullmatch(category) or not _SAFE_ID.fullmatch(status):
        raise ValueError("adapter output labels are invalid")
    if contains_sensitive_content(details):
        raise ValueError("adapter output is unsafe")
    if (code is ExitCode.SUCCESS) != (status in {"PASS", "SUCCESS"}):
        raise ValueError("adapter status and exit code disagree")
    return code, category, status, details


def _emit(
    operation: str,
    code: ExitCode,
    category: str,
    status: str,
    evidence_id: str,
    mode: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "category": category,
        "evidence_id": evidence_id,
        "exit_code": int(code),
        "mode": mode,
        "operation": operation,
        "status": status,
        "success": code is ExitCode.SUCCESS,
    }
    if details:
        payload["details"] = _json_value(details)
    if contains_sensitive_content(payload):
        payload = {
            "category": "OUTPUT",
            "evidence_id": "output-rejected",
            "exit_code": int(ExitCode.MALFORMED_OUTPUT),
            "mode": mode,
            "operation": operation if operation in _OPERATIONS else "unknown",
            "status": "UNSAFE",
            "success": False,
        }
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    if len(encoded.encode("ascii")) > _MAX_JSON_BYTES:
        encoded = json.dumps(
            {
                "category": "OUTPUT",
                "evidence_id": "output-rejected",
                "exit_code": int(ExitCode.MALFORMED_OUTPUT),
                "mode": mode,
                "operation": operation if operation in _OPERATIONS else "unknown",
                "status": "OVERSIZE",
                "success": False,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    sys.stdout.write(encoded + "\n")


def main(
    argv: Sequence[str] | None = None,
    *,
    adapter: OperationsAdapter | None = None,
) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    operation = values[0] if values and values[0] in _OPERATIONS else "unknown"
    evidence_id = f"{operation}-failed" if operation != "unknown" else "input-failed"
    mode = "PLAN"
    try:
        args = _parser().parse_args(values)
        operation = str(args.operation)
        request = _request(args)
        evidence_id = request.evidence_id
        mode = "EXECUTE" if request.execute else "PLAN"
        if not request.execute:
            code = ExitCode.SUCCESS
            category, status = _category(operation), "PASS"
            details = _policy_details(request)
        elif adapter is None:
            code = ExitCode.ADAPTER_REQUIRED
            category, status, details = _category(operation), "ADAPTER_REQUIRED", {}
        else:
            try:
                code, category, status, details = _adapter_result(adapter, request)
            except ValueError:
                code = ExitCode.MALFORMED_OUTPUT
                category, status, details = "OUTPUT", "MALFORMED", {}
    except CliInputError:
        code = ExitCode.INPUT_INVALID
        category, status, details = "INPUT", "INVALID", {}
    except (PolicyRejected, ReleaseError, ValueError):
        code = ExitCode.POLICY_REJECTED
        category, status, details = "POLICY", "REJECTED", {}
    except Exception:
        code = ExitCode.EXECUTION_FAILED
        category, status, details = "INTERNAL", "FAILED", {}
    _emit(operation, code, category, status, evidence_id, mode, details)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
