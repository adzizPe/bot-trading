from __future__ import annotations

from base64 import b64encode
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from app.recovery import cli
from app.recovery.types import ExitCode

_KEY = b"K" * 32
_WRONG_KEY = b"W" * 32
_RECOVERY_ENV = (
    "APP_ENV",
    "DATABASE_URL",
    "BACKUP_LOCAL_DIRECTORY",
    "BACKUP_OFFHOST_DIRECTORY",
    "BACKUP_ENCRYPTION_KEY",
)


def _set_generated_environment(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    source = root / "source" / "active.db"
    source.parent.mkdir(parents=True)
    for name in _RECOVERY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{source.as_posix()}")
    monkeypatch.setenv("BACKUP_LOCAL_DIRECTORY", str(root / "catalog"))
    monkeypatch.setenv("BACKUP_OFFHOST_DIRECTORY", str(root / "offhost"))
    monkeypatch.setenv("BACKUP_BUSY_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("BACKUP_OPERATION_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", b64encode(_KEY).decode("ascii"))
    return source


def _create_generated_database(path: Path) -> None:
    revision = cli._compatibility().repository_head
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
        connection.execute(
            "CREATE TABLE roles(id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE signals(id INTEGER PRIMARY KEY, symbol TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE safety_events(id INTEGER PRIMARY KEY, event_type TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE authentication_audit_events("
            "id INTEGER PRIMARY KEY, event_type TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO roles(name) VALUES ('generated')")
        connection.commit()


def _invoke(
    capsys: pytest.CaptureFixture[str], *arguments: str
) -> tuple[int, dict[str, Any], str]:
    code = cli.main(arguments)
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1
    assert captured.err == ""
    assert len(lines[0].encode("utf-8")) <= 4096
    return code, json.loads(lines[0]), lines[0]


@pytest.fixture
def generated_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[Path, str]:
    source = _set_generated_environment(monkeypatch, tmp_path)
    _create_generated_database(source)
    code, summary, _ = _invoke(capsys, "backup")
    assert code == ExitCode.SUCCESS
    assert summary["success"] is True
    return source, str(summary["backup_id"])


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_all_commands_and_parameters_are_registered() -> None:
    parser = cli._parser()
    assert parser.parse_args(["backup"]).command == "backup"
    assert parser.parse_args(["retention", "--dry-run"]).dry_run is True
    restore = parser.parse_args(
        [
            "restore",
            "--backup-id",
            "00000000-0000-0000-0000-000000000001",
            "--dry-run",
            "--first-restore",
        ]
    )
    assert restore.dry_run is True
    assert restore.first_restore is True


@pytest.mark.parametrize("command", ["verify", "copy-offhost", "restore"])
@pytest.mark.parametrize(
    "malformed", ["not-a-uuid", "../active.db", "C:\\production\\active.db", ""]
)
def test_malformed_backup_ids_fail_before_configuration(
    capsys: pytest.CaptureFixture[str], command: str, malformed: str
) -> None:
    code, summary, output = _invoke(capsys, command, "--backup-id", malformed)
    assert code == ExitCode.CONFIGURATION_INVALID
    assert summary == {
        "exit_code": 2,
        "operation": command,
        "reason": "INPUT_INVALID",
        "status": "FAILED",
        "success": False,
    }
    if malformed:
        assert malformed not in output


def test_key_is_not_a_cli_option_or_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = "cli-key-canary"
    code, summary, output = _invoke(capsys, "backup", "--key", canary)
    assert code == ExitCode.CONFIGURATION_INVALID
    assert summary["reason"] == "INPUT_INVALID"
    assert canary not in output


def test_missing_key_is_noninteractive_and_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_generated_environment(monkeypatch, tmp_path)
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY")
    code, summary, _ = _invoke(capsys, "backup")
    assert code == ExitCode.ENCRYPTION_FAILURE
    assert summary["reason"] == "KEY_UNAVAILABLE_OR_INVALID"


def test_verify_wrong_key_is_sanitized(
    generated_backup: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, backup_id = generated_backup
    encoded = b64encode(_WRONG_KEY).decode("ascii")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", encoded)
    code, summary, output = _invoke(capsys, "verify", "--backup-id", backup_id)
    assert code == ExitCode.ENCRYPTION_FAILURE
    assert summary["reason"] == "AUTHENTICATION_FAILED"
    assert encoded not in output
    assert _WRONG_KEY.hex() not in output
    assert "Traceback" not in output


def test_restore_and_retention_dry_runs_do_not_mutate_managed_files(
    generated_backup: tuple[Path, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, backup_id = generated_backup
    source_before = source.read_bytes()
    catalog = tmp_path / "catalog"
    before = _tree_bytes(catalog)
    code, summary, _ = _invoke(capsys, "restore", "--backup-id", backup_id, "--dry-run")
    assert code == ExitCode.SUCCESS
    assert summary["dry_run"] is True
    assert source.read_bytes() == source_before
    assert _tree_bytes(catalog) == before

    code, summary, _ = _invoke(capsys, "retention", "--dry-run")
    assert code == ExitCode.SUCCESS
    assert summary["dry_run"] is True
    assert summary["deleted"] == 0
    assert source.read_bytes() == source_before
    assert _tree_bytes(catalog) == before


def test_copy_and_status_need_no_key(
    generated_backup: tuple[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, backup_id = generated_backup
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY")
    code, copied, _ = _invoke(capsys, "copy-offhost", "--backup-id", backup_id)
    assert code == ExitCode.SUCCESS
    assert copied["status"] == "VERIFIED"
    code, status, _ = _invoke(capsys, "status")
    assert code == ExitCode.SUCCESS
    assert status["recovery"]["availability"] == "AVAILABLE"


def test_unexpected_errors_never_expose_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "secret-canary-at-C:\\production\\active.db"

    def fail(_args: object) -> tuple[ExitCode, dict[str, Any]]:
        raise RuntimeError(secret)

    monkeypatch.setattr(cli, "_execute", fail)
    code, summary, output = _invoke(capsys, "status")
    assert code == ExitCode.INTERNAL_FAILURE
    assert summary["reason"] == "OPERATION_FAILED"
    assert secret not in output
    assert "Traceback" not in output


def test_settings_explicitly_disable_dotenv() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert "Settings(_env_file=None, backup_encryption_key=None)" in source
    assert "get_settings" not in source
