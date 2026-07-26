from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, strategies as st

from app.operations.capacity import (
    GIB, CapacityLevel, VolumeObservation, VolumeRole, assess_capacity,
    validate_inventory,
)
from app.operations.certificates import (
    CertificateLevel, CertificateObservation, CertificateTransaction,
    assess_certificate,
)
from app.operations.log_rotation import (
    LogQuotaLevel, ManagedLogFile, ManagedLogPolicy,
)

NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)


def certificate(**updates: object) -> CertificateObservation:
    values = {
        "hostname": "example.invalid", "expected_hostname": "example.invalid",
        "not_before": NOW - timedelta(days=1), "not_after": NOW + timedelta(days=90),
        "chain_valid": True, "approved_fingerprint": True,
        "key_pair_matches": True, "certificate_readable": True,
        "private_key_readable": True, "acl_valid": True,
        "nginx_config_valid": True, "external_valid": True,
        "ocsp_observed": True, "fingerprint": "AA11",
    }
    values.update(updates)
    return CertificateObservation(**values)


@pytest.mark.parametrize(
    ("days", "level"),
    [(31, CertificateLevel.HEALTHY), (30, CertificateLevel.WARNING),
     (15, CertificateLevel.WARNING), (14, CertificateLevel.CRITICAL),
     (0, CertificateLevel.CRITICAL)],
)
def test_certificate_expiry_exact_boundaries(days: int, level: CertificateLevel) -> None:
    observed = certificate(not_after=NOW + timedelta(days=days))
    assert assess_certificate(observed, NOW).level is level


@pytest.mark.parametrize(
    "updates",
    [
        {"hostname": "wrong.invalid"}, {"chain_valid": False},
        {"key_pair_matches": False}, {"certificate_readable": False},
        {"private_key_readable": False}, {"acl_valid": False},
        {"nginx_config_valid": False}, {"external_valid": False},
    ],
)
def test_invalid_certificate_conditions_are_critical(updates: dict[str, object]) -> None:
    assert assess_certificate(certificate(**updates), NOW).level is CertificateLevel.CRITICAL


@dataclass
class FakeNginx:
    test_results: list[bool] = field(default_factory=lambda: [True])
    external_ok: bool = True
    actions: list[tuple[str, object]] = field(default_factory=list)

    async def backup_active(self) -> str:
        self.actions.append(("backup", "active"))
        return "backup-1"

    async def stage_candidate(self, fingerprint: str) -> None:
        self.actions.append(("stage", fingerprint))

    async def nginx_test(self) -> bool:
        self.actions.append(("test", None))
        return self.test_results.pop(0)

    async def reload(self) -> None:
        self.actions.append(("reload", None))

    async def external_observation(self, fingerprint: str, timeout_seconds: int) -> bool:
        self.actions.append(("external", (fingerprint, timeout_seconds)))
        return self.external_ok

    async def restore_active(self, backup_id: str) -> None:
        self.actions.append(("restore", backup_id))


@pytest.mark.asyncio
async def test_invalid_candidate_preserves_active_without_reload() -> None:
    nginx = FakeNginx()
    result = await CertificateTransaction(nginx).apply(
        active=certificate(fingerprint="OLD"),
        candidate=certificate(fingerprint="NEW", chain_valid=False), now=NOW,
    )
    assert not result.committed and not result.rolled_back
    assert nginx.actions == [("backup", "active")]


@pytest.mark.asyncio
async def test_candidate_success_uses_bounded_external_check() -> None:
    nginx = FakeNginx()
    result = await CertificateTransaction(nginx).apply(
        active=certificate(fingerprint="OLD"),
        candidate=certificate(fingerprint="NEW"), now=NOW,
    )
    assert result.committed and not result.rolled_back
    assert nginx.actions[-1] == ("external", ("NEW", 5))
    assert [action for action, _ in nginx.actions].count("reload") == 1


@pytest.mark.asyncio
async def test_post_reload_failure_restores_tests_and_reloads_active() -> None:
    nginx = FakeNginx(test_results=[True, True], external_ok=False)
    result = await CertificateTransaction(nginx).apply(
        active=certificate(fingerprint="OLD"),
        candidate=certificate(fingerprint="NEW"), now=NOW,
    )
    assert not result.committed and result.rolled_back
    assert [action for action, _ in nginx.actions] == [
        "backup", "stage", "test", "reload", "external", "restore", "test", "reload"
    ]


@pytest.mark.parametrize(
    ("free", "total", "level"),
    [
        (21 * GIB, 100 * GIB, CapacityLevel.HEALTHY),
        (20 * GIB, 100 * GIB, CapacityLevel.WARNING),
        (10 * GIB, 80 * GIB, CapacityLevel.WARNING),
        (10 * GIB, 100 * GIB, CapacityLevel.CRITICAL),
        (5 * GIB, 200 * GIB, CapacityLevel.CRITICAL),
    ],
)
def test_capacity_exact_boundaries(free: int, total: int, level: CapacityLevel) -> None:
    result = assess_capacity(VolumeObservation("volume-1", total, free, (VolumeRole.RELEASE,)))
    assert result.level is level
    assert result.update_blocked is (level is CapacityLevel.CRITICAL)


def test_capacity_inventory_requires_every_operational_role() -> None:
    complete = tuple(
        VolumeObservation(f"volume-{index}", 100 * GIB, 50 * GIB, (role,))
        for index, role in enumerate(VolumeRole)
    )
    validate_inventory(complete)
    with pytest.raises(ValueError, match="missing roles"):
        validate_inventory(complete[:-1])


def test_log_quota_rotation_is_dry_run_deterministic_and_safe() -> None:
    policy = ManagedLogPolicy("operations")
    files = (
        ManagedLogFile("archive-b", "BACKEND_ARCHIVE", "operations", 2 * GIB, 31),
        ManagedLogFile("archive-a", "NGINX_ACCESS_ARCHIVE", "operations", 2 * GIB, 31),
        ManagedLogFile("active", "ACTIVE_LOG", "operations", GIB, 99, True),
        ManagedLogFile("database", "DB", "operations", GIB, 99),
        ManagedLogFile("unknown", "UNKNOWN", "operations", GIB, 99),
        ManagedLogFile("unowned", "BACKEND_ARCHIVE", "other", GIB, 99),
    )
    plan = policy.plan(files)
    assert plan.level is LogQuotaLevel.CRITICAL and plan.dry_run
    assert plan.candidates == ("archive-a", "archive-b")
    changed = tuple(replace(item, active=True) if item.file_id == "archive-a" else item for item in files)
    assert policy.recheck(plan, changed) == ("archive-b",)


def test_log_warning_and_two_failures_critical() -> None:
    policy = ManagedLogPolicy("operations")
    files = (ManagedLogFile("archive", "BACKEND_ARCHIVE", "operations", 4 * GIB, 1),)
    assert policy.plan(files).level is LogQuotaLevel.WARNING
    assert policy.plan(files, consecutive_failures=2).level is LogQuotaLevel.CRITICAL


@given(st.sampled_from(["ACTIVE_LOG", "DB", "WAL", "SHM", "BACKUP", "FORENSIC", "CERTIFICATE", "SECRET"]))
def test_property_protected_or_active_content_is_never_rotation_candidate(kind: str) -> None:
    policy = ManagedLogPolicy("operations")
    protected = ManagedLogFile("protected", kind, "operations", 5 * GIB, 999)
    assert "protected" not in policy.plan((protected,)).candidates
