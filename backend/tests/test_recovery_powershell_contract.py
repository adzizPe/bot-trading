from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
SCRIPT_COMMANDS = {
    "Backup-Database.ps1": "backup",
    "Verify-Backup.ps1": "verify",
    "Restore-Database.ps1": "restore",
    "Copy-BackupOffHost.ps1": "copy-offhost",
    "Invoke-RestoreDrill.ps1": "drill",
    "Invoke-BackupRetention.ps1": "retention",
    "Get-BackupStatus.ps1": "status",
}


def _powershell() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _generated_environment(root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": (
                "sqlite+aiosqlite:///" + (root / "source" / "active.db").as_posix()
            ),
            "BACKUP_LOCAL_DIRECTORY": str(root / "catalog"),
            "BACKUP_OFFHOST_DIRECTORY": str(root / "offhost"),
            "BACKUP_BUSY_TIMEOUT_SECONDS": "1",
            "BACKUP_OPERATION_TIMEOUT_SECONDS": "30",
        }
    )
    environment.pop("BACKUP_ENCRYPTION_KEY", None)
    return environment


def test_exact_wrapper_set_and_static_security_contracts() -> None:
    for filename, command in SCRIPT_COMMANDS.items():
        path = SCRIPTS / filename
        assert path.is_file(), filename
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        assert text.startswith("[CmdletBinding()]")
        assert "Set-StrictMode -Version 2.0" in text
        assert "$ErrorActionPreference = 'Stop'" in text
        assert "$PSScriptRoot" in text
        assert "Test-Path -LiteralPath" in text
        assert "Push-Location -LiteralPath" in text
        assert "$cliArguments = @(" in text
        assert "$LASTEXITCODE" in text
        assert "exit $code" in text
        assert f"'app.recovery.cli', '{command}'" in text
        assert "--key" not in lowered
        assert "credential" not in lowered
        assert "start-service" not in lowered
        assert "stop-service" not in lowered
        assert "app.main" not in lowered
        assert "mt5" not in lowered
        assert "order" not in lowered
        assert "demo" not in lowered


def test_wrapper_parameters_are_typed_and_dry_run_is_forwarded() -> None:
    verify = (SCRIPTS / "Verify-Backup.ps1").read_text(encoding="utf-8")
    copy = (SCRIPTS / "Copy-BackupOffHost.ps1").read_text(encoding="utf-8")
    restore = (SCRIPTS / "Restore-Database.ps1").read_text(encoding="utf-8")
    retention = (SCRIPTS / "Invoke-BackupRetention.ps1").read_text(encoding="utf-8")
    assert "[string]$BackupId" in verify
    assert "[string]$BackupId" in copy
    assert "[string]$BackupId" in restore
    assert "[switch]$DryRun" in restore
    assert "[switch]$FirstRestore" in restore
    assert "[switch]$DryRun" in retention
    assert "$cliArguments += '--dry-run'" in restore
    assert "$cliArguments += '--dry-run'" in retention


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is unavailable")
def test_all_wrappers_parse_with_windows_powershell_51() -> None:
    shell = _powershell()
    assert shell is not None
    quoted = ",".join(
        "'" + str(SCRIPTS / name).replace("'", "''") + "'" for name in SCRIPT_COMMANDS
    )
    command = (
        "$errors = @(); "
        f"$files = @({quoted}); "
        "foreach ($file in $files) { "
        "$tokens = $null; $parseErrors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile("
        "$file, [ref]$tokens, [ref]$parseErrors); "
        "if ($parseErrors.Count -ne 0) { $errors += $parseErrors } }; "
        "if ($errors.Count -ne 0) { exit 1 }; exit 0"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is unavailable")
def test_status_wrapper_is_cwd_independent_and_uses_generated_paths(
    tmp_path: Path,
) -> None:
    shell = _powershell()
    assert shell is not None
    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS / "Get-BackupStatus.ps1"),
        ],
        cwd=unrelated,
        env=_generated_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    assert summary["operation"] == "status"
    assert summary["success"] is True
    assert str(tmp_path) not in result.stdout


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is unavailable")
def test_wrapper_propagates_cli_exit_code_for_malformed_id(
    tmp_path: Path,
) -> None:
    shell = _powershell()
    assert shell is not None
    canary = "..\\production\\active.db"
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPTS / "Verify-Backup.ps1"),
            "-BackupId",
            canary,
        ],
        cwd=tmp_path,
        env=_generated_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    assert summary["exit_code"] == 2
    assert summary["operation"] == "verify"
    assert canary not in result.stdout
