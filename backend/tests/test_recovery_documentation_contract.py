from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_PATH = ROOT / "docs" / "deployment" / "windows-sqlite-recovery.md"
README_PATH = ROOT / "README.md"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
SETTINGS_PATH = ROOT / "backend" / "app" / "config" / "settings.py"
SCRIPTS = (
    "Backup-Database.ps1",
    "Verify-Backup.ps1",
    "Restore-Database.ps1",
    "Copy-BackupOffHost.ps1",
    "Invoke-RestoreDrill.ps1",
    "Invoke-BackupRetention.ps1",
    "Get-BackupStatus.ps1",
)
DEFAULTS = {
    "BACKUP_RPO_HOURS": "24",
    "BACKUP_RTO_HOURS": "2",
    "BACKUP_INTERVAL_HOURS": "24",
    "BACKUP_RETENTION_DAILY": "7",
    "BACKUP_RETENTION_WEEKLY": "4",
    "BACKUP_RETENTION_MONTHLY": "3",
}


def _environment_values(text: str) -> dict[str, str]:
    return {
        name.strip(): value.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
        for name, value in (line.split("=", 1),)
    }


def test_group9_documentation_and_all_recovery_wrappers_exist() -> None:
    assert RUNBOOK_PATH.is_file()
    assert README_PATH.is_file()
    for script in SCRIPTS:
        assert (ROOT / "scripts" / script).is_file(), script


def test_config_reference_has_exact_policy_defaults_and_no_key_default() -> None:
    environment = _environment_values(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert {name: environment[name] for name in DEFAULTS} == DEFAULTS
    assert environment["BACKUP_ENCRYPTION_REQUIRED"] == "true"
    assert environment["BACKUP_ENCRYPTION_KEY"] == ""
    assert environment["BACKUP_ENCRYPTION_KEY_ENV"] == "BACKUP_ENCRYPTION_KEY"
    assert environment["BACKUP_LOCAL_DIRECTORY"] == ""
    assert environment["BACKUP_OFFHOST_DIRECTORY"] == ""

    settings = SETTINGS_PATH.read_text(encoding="utf-8")
    expected_fields = {
        "backup_rpo_hours": 24,
        "backup_rto_hours": 2,
        "backup_interval_hours": 24,
        "backup_retention_daily": 7,
        "backup_retention_weekly": 4,
        "backup_retention_monthly": 3,
    }
    for field, value in expected_fields.items():
        assert re.search(
            rf"^    {field}: int = Field\(default={value},", settings, re.M
        )
    assert "backup_encryption_key: SecretStr | None = None" in settings


def test_runbook_covers_complete_recovery_and_forensic_safety_contract() -> None:
    text = RUNBOOK_PATH.read_text(encoding="utf-8")
    lowered = text.casefold()
    required = (
        "daily backup, verification, off-host copy, and retention",
        "restore dry-run",
        "normal restore",
        "suspected corruption",
        "disk full",
        "off-host unavailable",
        "encryption-key lifecycle",
        "gfs retention",
        "restore drill",
        "operator completion checklist",
        "forensic db/wal/shm",
        "best effort",
        "restrictive ntfs acls",
        "encrypted windows volumes",
        "forensic_not_verified_backup",
    )
    assert all(item in lowered for item in required)
    assert "never raw-copy the active database as a backup" in lowered
    assert "manifest.json` is the source of truth" in lowered
    assert "do not restart the backend, demo subsystem, or mt5" in lowered
    assert "do not restart anything until all post-restore checks" in lowered


def test_task_scheduler_flow_is_native_bounded_and_never_restores() -> None:
    text = RUNBOOK_PATH.read_text(encoding="utf-8")
    scheduler = text.split("## Windows PowerShell 5.1 Task Scheduler flow", 1)[1]
    scheduler = scheduler.split("## Operator completion checklist", 1)[0]
    expected_order = (
        "Backup-Database.ps1",
        "Verify-Backup.ps1",
        "Copy-BackupOffHost.ps1",
        "Invoke-BackupRetention.ps1",
    )
    positions = tuple(scheduler.index(name) for name in expected_order)
    assert positions == tuple(sorted(positions))
    assert "Restore-Database.ps1" not in scheduler
    for contract in (
        "dedicated least privilege account",
        "IgnoreNew",
        "no overlap",
        "execution timeout",
        "working directory",
        "Microsoft-Windows-TaskScheduler/Operational",
        "LastTaskResult",
        "watchdog",
        "rpo_met=false",
        "backup_age_seconds >= 86400",
        "Never create a scheduled restore task",
    ):
        assert contract.casefold() in scheduler.casefold()
    assert "key/credential" in scheduler


def test_docs_do_not_recommend_disallowed_platforms_or_service_control() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8").casefold()
    forbidden = (
        "docker",
        "kubernetes",
        "podman",
        "helm install",
        "terraform apply",
        "start-service",
        "stop-service",
        "nssm start",
        "nssm stop",
    )
    assert not any(item in runbook for item in forbidden)
    assert not re.search(r"backup_encryption_key\s*=\s*\S+", runbook)


def test_readme_summarizes_safe_operator_contract_and_links_runbook() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    section = readme.split("## Status Milestone 10.7", 1)[1]
    section = section.split("## Status Milestone 7", 1)[0]
    assert "docs/deployment/windows-sqlite-recovery.md" in section
    assert "manifest.json" in section and "source of truth" in section
    assert "-DryRun" in section and "Get-BackupStatus.ps1" in section
    assert "Raw copy active database bukan backup yang valid" in section
    assert "tepat satu worker Uvicorn" in section
    assert all(str(value) in section for value in (24, 2, 7, 4, 3))
