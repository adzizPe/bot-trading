from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
WORKFLOWS = {
    "Initialize-NativeOperations.ps1": "setup",
    "Test-NativePreflight.ps1": "preflight",
    "Start-NativeOperations.ps1": "start",
    "Stop-NativeOperations.ps1": "stop",
    "Restart-NativeOperations.ps1": "restart",
    "Invoke-NativeReboot.ps1": "reboot",
    "Update-NativeOperations.ps1": "update",
    "Rollback-NativeOperations.ps1": "rollback",
    "Test-CrashLoop.ps1": "crash-loop",
    "Get-RestoreHoldStatus.ps1": "restore-hold-status",
    "Enter-RecoveryHandoff.ps1": "recovery-handoff",
    "Release-RestoreHold.ps1": "restore-hold-release",
    "Invoke-MonitoringCheck.ps1": "monitoring-check",
    "Test-CertificateHealth.ps1": "certificate-check",
    "Test-CapacityHealth.ps1": "capacity-check",
    "Test-LogHealth.ps1": "log-check",
    "Test-HostHardening.ps1": "hardening-check",
}


def powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


def test_named_wrappers_are_thin_strict_and_have_no_native_mutation_commands() -> None:
    forbidden = (
        "start-service", "stop-service", "restart-service", "set-service",
        "new-service", "remove-service", "restart-computer", "shutdown.exe",
        "nssm start", "nssm stop", "pm2 start", "pm2 stop", "schtasks",
        "invoke-restmethod", "invoke-webrequest", "/api/trading", "metatrader",
    )
    for filename, operation in WORKFLOWS.items():
        text = (SCRIPTS / filename).read_text(encoding="utf-8")
        lowered = text.casefold()
        assert text.startswith("[CmdletBinding(SupportsShouldProcess = $true)]")
        assert "Set-StrictMode -Version 2.0" in text
        assert "$ErrorActionPreference = 'Stop'" in text
        assert "Operations.Common.ps1" in text
        assert f"-Operation '{operation}'" in text
        assert "-WhatIfMode ([bool]$WhatIfPreference)" in text
        assert not any(item in lowered for item in forbidden)


def test_common_helper_uses_exact_array_invocation_and_fail_closed_json() -> None:
    text = (SCRIPTS / "Operations.Common.ps1").read_text(encoding="utf-8")
    assert "Set-StrictMode -Version 2.0" in text
    assert "Test-Path -LiteralPath $PythonPath -PathType Leaf" in text
    assert "Push-Location -LiteralPath $backendRoot" in text
    assert "$cliArguments = @(" in text
    assert "& $PythonPath @cliArguments" in text
    assert "$LASTEXITCODE" in text
    assert "ConvertFrom-Json -ErrorAction Stop" in text
    assert "exit $code" in text
    assert '"exit_code":7' in text
    assert "--execute" in text
    assert "Start-Process" not in text
    assert "Get-Content" not in text


@pytest.mark.skipif(powershell() is None, reason="Windows PowerShell is unavailable")
def test_all_operations_scripts_parse_with_powershell_51() -> None:
    shell = powershell()
    assert shell is not None
    paths = [SCRIPTS / "Operations.Common.ps1"] + [
        SCRIPTS / name for name in WORKFLOWS
    ]
    quoted = ",".join("'" + str(path).replace("'", "''") + "'" for path in paths)
    command = (
        "$errors = @(); "
        f"$files = @({quoted}); "
        "foreach ($file in $files) { $tokens = $null; $parseErrors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        "$file, [ref]$tokens, [ref]$parseErrors); "
        "if ($parseErrors.Count -ne 0) { $errors += $parseErrors } }; "
        "if ($errors.Count -ne 0) { $errors | Out-String | Write-Error; exit 1 }; exit 0"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(powershell() is None, reason="Windows PowerShell is unavailable")
def test_whatif_plan_handles_paths_with_spaces_and_is_one_line(tmp_path: Path) -> None:
    shell = powershell()
    assert shell is not None
    root = tmp_path / "state root with spaces"
    python = PROJECT_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS / "Start-NativeOperations.ps1"),
            "-Root",
            str(root),
            "-PythonPath",
            str(python),
            "-Execute",
            "-WhatIf",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    payload = json.loads(result.stdout)
    assert payload["mode"] == "PLAN"
    assert payload["details"]["services"][0]["arguments"][-2:] == ["--workers", "1"]
    assert not root.exists()


@pytest.mark.skipif(powershell() is None, reason="Windows PowerShell is unavailable")
def test_wrapper_rejects_malformed_child_output(tmp_path: Path) -> None:
    shell = powershell()
    assert shell is not None
    fake = tmp_path / "fake python.cmd"
    fake.write_text("@echo off\r\necho malformed-output\r\nexit /b 0\r\n", encoding="ascii")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS / "Test-NativePreflight.ps1"),
            "-Root",
            str(tmp_path / "state with spaces"),
            "-PythonPath",
            str(fake),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 7
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == 7
    assert payload["status"] == "MALFORMED"


@pytest.mark.skipif(powershell() is None, reason="Windows PowerShell is unavailable")
def test_fake_managers_are_never_invoked_and_child_exit_is_exact(tmp_path: Path) -> None:
    shell = powershell()
    assert shell is not None
    root = tmp_path / "offline topology with spaces"
    fake_bin = root / "fake bin"
    nginx = root / "nginx"
    fake_bin.mkdir(parents=True)
    nginx.mkdir(parents=True)
    marker = root / "forbidden invocation.marker"
    for name in ("sc.exe", "nssm.exe", "pm2.cmd"):
        (fake_bin / name).write_text(
            "@echo off\r\n" + f">\"{marker}\" echo invoked\r\nexit /b 99\r\n",
            encoding="ascii",
        )
    (nginx / "nginx.exe").write_text(
        "@echo off\r\n" + f">\"{marker}\" echo invoked\r\nexit /b 99\r\n",
        encoding="ascii",
    )
    fake_python = fake_bin / "fake python.cmd"
    fake_python.write_text(
        "@echo off\r\n"
        "echo {\"category\":\"LIFECYCLE\",\"evidence_id\":\"fake-1\","
        "\"exit_code\":5,\"mode\":\"EXECUTE\",\"operation\":\"stop\","
        "\"status\":\"FAILED\",\"success\":false}\r\n"
        "exit /b 5\r\n",
        encoding="ascii",
    )
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS / "Stop-NativeOperations.ps1"),
            "-Root",
            str(root),
            "-PythonPath",
            str(fake_python),
            "-Execute",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 5, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["exit_code"] == 5
    assert not marker.exists()


@pytest.mark.skipif(powershell() is None, reason="Windows PowerShell is unavailable")
def test_wrapper_rejects_incomplete_json_even_when_exit_matches(tmp_path: Path) -> None:
    shell = powershell()
    assert shell is not None
    fake = tmp_path / "incomplete python.cmd"
    fake.write_text("@echo off\r\necho {\"exit_code\":0}\r\nexit /b 0\r\n", encoding="ascii")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS / "Test-NativePreflight.ps1"),
            "-Root",
            str(tmp_path / "state"),
            "-PythonPath",
            str(fake),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 7
    assert json.loads(result.stdout)["status"] == "MALFORMED"
