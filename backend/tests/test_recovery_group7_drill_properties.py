from __future__ import annotations

import ast
from base64 import b64encode
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from uuid import uuid4

from hypothesis import HealthCheck, given, settings, strategies as st

from app.recovery.catalog import FilesystemCatalog
from app.recovery.drill import DRILL_STAGES, DrillStageStatus, RestoreDrillRunner
from app.recovery.logging import StructuredEventLogger
from app.recovery.status import StatusService
from app.recovery.types import ExitCode, RestoreStatus
from tests.test_recovery_group7_status_properties import _config

PROPERTY_SETTINGS = settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
FORBIDDEN_IMPORT_PARTS = {
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


def _scripts(root: Path) -> Path:
    scripts = root / "migrations"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    (versions / "head.py").write_text(
        "revision = 'head'\n"
        "down_revision = None\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "def upgrade():\n"
        "    pass\n",
        encoding="utf-8",
    )
    return scripts


def test_restore_drill_runs_all_13_stages_and_persists_safe_evidence(
    tmp_path: Path,
) -> None:
    results = FilesystemCatalog((tmp_path / "results").resolve())
    temp_root = (tmp_path / "temporary-drills").resolve()
    runner = RestoreDrillRunner(results, _scripts(tmp_path).resolve(), temp_root)

    result = runner.run()

    assert result.success
    assert result.exit_code == int(ExitCode.SUCCESS)
    assert tuple(item.name for item in result.stages) == DRILL_STAGES
    assert len(result.stages) == 13
    assert all(item.status is DrillStageStatus.PASS for item in result.stages)
    assert result.integrity_check == "ok"
    assert result.revision == "head"
    assert result.baseline_fingerprint == result.restored_fingerprint
    assert result.offhost_checksum_verified
    assert result.backup_seconds is not None
    assert result.restore_seconds is not None
    assert result.rpo_actual_seconds is not None
    assert result.rpo_met and result.rto_met
    assert result.trading_guard_calls == 0
    assert temp_root.is_dir() and not tuple(temp_root.iterdir())
    receipt = results.operations_root / f"drill-{result.drill_id}.json"
    persisted = json.loads(receipt.read_text(encoding="ascii"))
    assert persisted == result.as_dict()
    serialized = receipt.read_text(encoding="ascii").lower()
    assert "database_url" not in serialized and "temporary-drills" not in serialized


def test_restore_drill_failure_is_nonzero_cleaned_and_visible_to_status(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    results = FilesystemCatalog(config.local_root)
    temp_root = (tmp_path / "temporary-drills").resolve()
    runner = RestoreDrillRunner(results, _scripts(tmp_path).resolve(), temp_root)

    def fail_verify(stage: str) -> None:
        if stage == "VERIFY":
            raise RuntimeError("generated failure with no sensitive content")

    result = runner.run(fault=fail_verify)
    status = StatusService(results, config).rebuild()

    assert not result.success
    assert result.exit_code == int(ExitCode.RESTORE_OR_DRILL_FAILURE)
    assert result.failed_stage == "VERIFY"
    assert result.stages[3].status is DrillStageStatus.FAIL
    assert result.stages[-1].status is DrillStageStatus.PASS
    assert all(item.status is DrillStageStatus.NOT_RUN for item in result.stages[4:-1])
    assert temp_root.is_dir() and not tuple(temp_root.iterdir())
    assert status.latest_restore_status is RestoreStatus.FAILED
    assert status.latest_failure_category == result.failure_reason


@PROPERTY_SETTINGS
@given(
    secret=st.text(
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"),
            min_codepoint=48,
            max_codepoint=122,
        ),
        min_size=12,
        max_size=32,
    )
)
def test_property_15_logs_and_metadata_are_secret_non_interfering(
    tmp_path: Path, secret: str
) -> None:
    """Design Property 15: exact and encoded secrets never reach outputs."""
    case = tmp_path / str(uuid4())
    case.mkdir()
    config = _config(case)
    catalog = FilesystemCatalog(config.local_root)
    StatusService(catalog, config, utcnow=lambda: datetime.now(timezone.utc)).rebuild()
    encoded = b64encode(secret.encode("utf-8")).decode("ascii")
    hexadecimal = secret.encode("utf-8").hex()
    stdout = io.StringIO()
    stderr = io.StringIO()
    log_path = case / "events.jsonl"
    with log_path.open("w", encoding="utf-8") as stream:
        logger = StructuredEventLogger(
            stream,
            secret_values=(secret, encoded, hexadecimal),
        )
        logger.write_exception("drill_failed", RuntimeError(secret))
    with redirect_stdout(stdout), redirect_stderr(stderr):
        StructuredEventLogger(stdout, secret_values=(secret, encoded)).write_exception(
            "status_failed", ValueError(secret)
        )
        StructuredEventLogger(stderr, secret_values=(secret, encoded)).write_exception(
            "verify_failed", RuntimeError(encoded)
        )
    content = "\n".join(
        [
            stdout.getvalue(),
            stderr.getvalue(),
            log_path.read_text(encoding="utf-8"),
            *(
                path.read_text(encoding="ascii")
                for path in catalog.root.rglob("*.json")
            ),
        ]
    )
    assert secret not in content
    assert encoded not in content
    assert hexadecimal not in content


@PROPERTY_SETTINGS
@given(
    filename=st.sampled_from(
        (
            "backup.py",
            "verification.py",
            "offhost.py",
            "retention.py",
            "restore.py",
            "drill.py",
            "status.py",
        )
    )
)
def test_property_16_recovery_has_no_trading_import_capability(
    filename: str,
) -> None:
    """Design Property 16: recovery imports no trading or broker subsystem."""
    path = Path(__file__).parents[1] / "app" / "recovery" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    prohibited = [
        name
        for name in imported
        if name == "app.main"
        or (
            name.startswith("app.")
            and any(part in FORBIDDEN_IMPORT_PARTS for part in name.split("."))
        )
    ]
    assert prohibited == []
