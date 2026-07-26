from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.operations.cli import ExitCode, OperationRequest, main


def invoke(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    operation: str,
    *arguments: str,
    adapter: object | None = None,
) -> tuple[int, dict[str, Any], str]:
    root = tmp_path / "operator state with spaces"
    code = main(
        [operation, "--root", str(root), *arguments],
        adapter=adapter,  # type: ignore[arg-type]
    )
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1
    assert captured.err == ""
    return code, json.loads(lines[0]), lines[0]


class RecordingAdapter:
    def __init__(self, result: object | None = None) -> None:
        self.requests: list[OperationRequest] = []
        self.result = result or {
            "exit_code": 0,
            "status": "PASS",
            "details": {"result": "COMPLETED"},
        }

    def execute(self, request: OperationRequest) -> Any:
        self.requests.append(request)
        return self.result


class TimeoutAdapter:
    def execute(self, request: OperationRequest) -> Any:
        raise TimeoutError


def test_default_plan_is_idempotent_stable_and_never_mutates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = invoke(capsys, tmp_path, "setup", "--release-id", "release-1")
    second = invoke(capsys, tmp_path, "setup", "--release-id", "release-1")
    assert first == second
    code, payload, encoded = first
    assert code == ExitCode.SUCCESS
    assert payload["mode"] == "PLAN"
    assert payload["details"]["mutation"] is False
    assert not (tmp_path / "operator state with spaces").exists()
    backend = payload["details"]["services"][0]
    assert backend["arguments"] == [
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
        "--port", "8000", "--workers", "1",
    ]
    assert backend["environment_source"] == {
        "kind": "PROTECTED_FILE_METADATA",
        "path": "state/protected/backend.environment",
    }
    assert str(tmp_path) not in encoded


def test_execute_requires_explicit_in_process_adapter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, payload, _ = invoke(capsys, tmp_path, "start", "--execute")
    assert code == ExitCode.ADAPTER_REQUIRED
    assert payload["status"] == "ADAPTER_REQUIRED"


def test_injected_adapter_receives_exact_paths_arguments_manager_and_timeout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    adapter = RecordingAdapter()
    code, payload, _ = invoke(
        capsys,
        tmp_path,
        "start",
        "--execute",
        "--process-manager",
        "PM2",
        "--timeout-seconds",
        "37",
        "--release-id",
        "release-2",
        adapter=adapter,
    )
    assert code == ExitCode.SUCCESS
    assert payload["mode"] == "EXECUTE"
    assert payload["details"] == {"result": "COMPLETED"}
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert request.timeout_seconds == 37
    assert request.paths.release_root == (
        tmp_path / "operator state with spaces" / "releases"
    ).resolve()
    assert {item.process_manager.value for item in request.definitions} == {"PM2"}
    backend = request.definitions[0]
    assert backend.arguments[-6:] == (
        "--host", "127.0.0.1", "--port", "8000", "--workers", "1"
    )
    assert request.environment_source == (
        request.paths.state_root / "protected" / "backend.environment"
    )


def test_timeout_and_adapter_failure_exit_codes_propagate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    timeout, payload, _ = invoke(
        capsys, tmp_path, "start", "--execute", adapter=TimeoutAdapter()
    )
    assert timeout == ExitCode.EXECUTION_TIMEOUT
    assert payload["exit_code"] == ExitCode.EXECUTION_TIMEOUT
    failed = RecordingAdapter({
        "exit_code": 5,
        "category": "LIFECYCLE",
        "status": "FAILED",
        "details": {"reason": "GATE_FAILED"},
    })
    code, payload, _ = invoke(
        capsys, tmp_path, "stop", "--execute", adapter=failed
    )
    assert code == ExitCode.EXECUTION_FAILED
    assert payload["details"]["reason"] == "GATE_FAILED"


@pytest.mark.parametrize(
    "result",
    [
        "not-an-object",
        {"category": "EXECUTION", "status": "PASS", "extra": True},
        {"exit_code": 0, "category": "EXECUTION", "status": "FAILED"},
        {
            "exit_code": 0,
            "category": "EXECUTION",
            "status": "PASS",
            "details": {"authorization": "synthetic-canary"},
        },
    ],
)
def test_malformed_or_unsafe_adapter_output_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    result: object,
) -> None:
    code, payload, encoded = invoke(
        capsys,
        tmp_path,
        "start",
        "--execute",
        adapter=RecordingAdapter(result),
    )
    assert code == ExitCode.MALFORMED_OUTPUT
    assert payload["status"] == "MALFORMED"
    assert "synthetic-canary" not in encoded


def test_update_and_restore_hold_require_separated_identifiers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    common = (
        "--change-id", "change-1", "--operator-id", "operator-a",
        "--reviewer-id", "reviewer-b",
    )
    code, payload, _ = invoke(
        capsys,
        tmp_path,
        "update",
        *common,
        "--release-id",
        "release-2",
        "--last-known-good",
        "release-1",
    )
    assert code == ExitCode.SUCCESS
    assert payload["details"]["identifiers"]["change_id"] == "change-1"
    rejected, _, _ = invoke(
        capsys,
        tmp_path,
        "recovery-handoff",
        "--change-id",
        "change-1",
        "--operator-id",
        "same-person",
        "--reviewer-id",
        "same-person",
        "--restore-id",
        "recovery-1",
    )
    assert rejected == ExitCode.POLICY_REJECTED


def test_restore_hold_plans_never_start_or_run_recovery(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    identifiers = (
        "--change-id", "change-1", "--operator-id", "operator-a",
        "--reviewer-id", "reviewer-b", "--restore-id", "recovery-1",
    )
    _, handoff, _ = invoke(
        capsys, tmp_path, "recovery-handoff", *identifiers
    )
    _, release, _ = invoke(
        capsys, tmp_path, "restore-hold-release", *identifiers
    )
    assert handoff["details"]["plan"] == [
        "enter-hold", "edge-stop", "backend-stop", "offline-proof"
    ]
    combined = handoff["details"]["plan"] + release["details"]["plan"]
    assert not any("start" in step for step in combined)
    assert not any("run-recovery" in step for step in combined)


def test_invalid_timeout_unknown_arguments_and_unprotected_source_are_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid, _, _ = invoke(
        capsys, tmp_path, "preflight", "--timeout-seconds", "301"
    )
    assert invalid == ExitCode.INPUT_INVALID
    unknown, payload, encoded = invoke(
        capsys, tmp_path, "preflight", "--private-key", "synthetic-canary"
    )
    assert unknown == ExitCode.INPUT_INVALID
    assert payload["operation"] == "preflight"
    assert "synthetic-canary" not in encoded
    outside, _, _ = invoke(
        capsys,
        tmp_path,
        "preflight",
        "--environment-source",
        str(tmp_path / "outside.environment"),
    )
    assert outside == ExitCode.POLICY_REJECTED


def test_protected_source_override_is_metadata_only_and_identities_are_omitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "operator state with spaces"
    source = root / "state" / "protected" / "runtime.environment"
    code, payload, encoded = invoke(
        capsys,
        tmp_path,
        "update",
        "--change-id",
        "change-1",
        "--operator-id",
        "operator-a",
        "--reviewer-id",
        "reviewer-b",
        "--release-id",
        "release-2",
        "--last-known-good",
        "release-1",
        "--environment-source",
        str(source),
    )
    assert code == ExitCode.SUCCESS
    for service in payload["details"]["services"]:
        assert service["environment_source"] == {
            "kind": "PROTECTED_FILE_METADATA",
            "path": "state/protected/runtime.environment",
        }
    assert "operator-a" not in encoded
    assert "reviewer-b" not in encoded
    assert not source.exists()


@pytest.mark.parametrize(
    "operation",
    [
        "monitoring-check",
        "certificate-check",
        "capacity-check",
        "log-check",
        "hardening-check",
    ],
)
def test_one_shot_checks_plan_non_overlap_and_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    code, payload, _ = invoke(capsys, tmp_path, operation)
    assert code == ExitCode.SUCCESS
    expected_category = {
        "monitoring-check": "MONITORING",
        "certificate-check": "CERTIFICATE",
        "capacity-check": "CAPACITY",
        "log-check": "LOG_ROTATION",
        "hardening-check": "HARDENING",
    }[operation]
    assert payload["category"] == expected_category
    assert payload["details"]["read_only"] is True
    assert payload["details"]["plan"][0] == "non-overlap-guard"
