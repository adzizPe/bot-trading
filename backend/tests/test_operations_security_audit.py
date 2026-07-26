from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.operations.access_control import (
    AccessMatrix, AclGrant, IdentityRights, IdentityRole, validate_access_matrix,
)
from app.operations.hardening import (
    HardeningException, HostSnapshot, Listener, UpdateState, audit_host,
)
from app.operations.secret_audit import (
    ArtifactCanaryScanner, ArtifactSource, CredentialMetadata, InventoryStatus,
    SyntheticArtifact, missing_credential_state,
)

NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)


def host(**updates: object) -> HostSnapshot:
    values = {
        "observed_at": NOW, "last_review_at": NOW - timedelta(days=1),
        "update": UpdateState(True), "firewall_enabled": True,
        "firewall_default_deny": True, "allowed_public_ports": (80, 443),
        "admin_sources_approved": True,
        "listeners": (Listener(443, "0.0.0.0", "nginx", "https", True, True),
                      Listener(8000, "127.0.0.1", "backend", "api", True, False)),
        "malware_protection": True, "audit_policy": True, "time_sync": True,
        "clock_drift_seconds": 0, "event_log_retention_days": 30,
        "remote_admin_named_accounts": True, "remote_admin_approved_sources": True,
    }
    values.update(updates)
    return HostSnapshot(**values)


def test_approved_synthetic_host_baseline_passes() -> None:
    result = audit_host(host())
    assert result.passed and result.update_completion_allowed


@pytest.mark.parametrize(
    ("updates", "finding"),
    [
        ({"firewall_default_deny": False}, "firewall-baseline"),
        ({"allowed_public_ports": (80, 443, 8000)}, "firewall-public-port"),
        ({"clock_drift_seconds": 61}, "clock-drift"),
        ({"event_log_retention_days": 29}, "eventlog-retention"),
        ({"last_review_at": NOW - timedelta(days=91)}, "review-stale"),
        ({"debug": True}, "debug"), ({"docs": True}, "docs"),
        ({"vite_dev": True}, "vite-dev"), ({"build_watcher": True}, "build-watcher"),
        ({"uvicorn_reload": True}, "uvicorn-reload"),
        ({"container_runtime": True}, "container-runtime"),
        ({"external_queue": True}, "external-queue"),
    ],
)
def test_hardening_failures_are_stable(updates: dict[str, object], finding: str) -> None:
    assert finding in audit_host(host(**updates)).findings


def test_public_backend_status_or_recovery_listener_fails() -> None:
    listeners = (
        Listener(8000, "0.0.0.0", "backend", "api", True, True),
        Listener(9000, "0.0.0.0", "recovery", "restore", False, True),
    )
    findings = audit_host(host(listeners=listeners)).findings
    assert "listener-public-exposure" in findings
    assert "backend-status-public" in findings
    assert "listener-unapproved" in findings


def test_critical_update_windows_and_bounded_exception() -> None:
    update = UpdateState(
        True, NOW - timedelta(days=8), NOW - timedelta(days=6), None, False
    )
    result = audit_host(host(update=update))
    assert "critical-update-assessment" in result.findings
    assert "critical-update-window" in result.findings
    exception = HardeningException(
        "critical-update-window", "owner-a", "isolated", "reviewer-b",
        NOW - timedelta(days=1), NOW + timedelta(days=29),
    )
    result = audit_host(host(update=update), (exception,))
    assert "critical-update-window" in result.exceptions_applied
    assert "critical-update-assessment" in result.findings
    bare_exception = UpdateState(
        True, NOW - timedelta(days=8), NOW - timedelta(days=8), None, True
    )
    assert "critical-update-exception-metadata" in audit_host(
        host(update=bare_exception)
    ).findings


def test_clock_drift_blocks_update_even_when_exception_is_valid() -> None:
    exception = HardeningException(
        "clock-drift", "owner-a", "manual-time-check", "reviewer-b",
        NOW - timedelta(days=1), NOW + timedelta(days=1),
    )
    result = audit_host(host(clock_drift_seconds=61), (exception,))
    assert result.passed
    assert not result.update_completion_allowed


def identities() -> tuple[IdentityRights, ...]:
    return (
        IdentityRights(IdentityRole.BACKEND, "svc-backend", frozenset({"service_logon"})),
        IdentityRights(IdentityRole.EDGE, "svc-edge", frozenset({"service_logon"})),
        IdentityRights(IdentityRole.RECOVERY, "svc-recovery", frozenset({"read", "write_recovery"})),
        IdentityRights(IdentityRole.MONITORING, "svc-monitor", frozenset({"read", "logon_batch"})),
        IdentityRights(IdentityRole.OPERATOR, "operator-a", frozenset({"service_reconfigure"})),
        IdentityRights(IdentityRole.REVIEWER, "reviewer-b", frozenset({"read"})),
    )


def matrix(**updates: object) -> AccessMatrix:
    values = {
        "identities": identities(),
        "grants": (
            AclGrant("release", "svc-backend", frozenset({"read"})),
            AclGrant("secret", "svc-backend", frozenset({"read"})),
            AclGrant("service-definition", "operator-a", frozenset({"write"})),
        ),
        "reviewed_at": NOW - timedelta(days=1),
    }
    values.update(updates)
    return AccessMatrix(**values)


def test_identity_right_acl_matrix_passes_and_review_is_current() -> None:
    assert validate_access_matrix(matrix(), NOW).passed


@pytest.mark.parametrize(
    ("role", "right", "finding"),
    [
        (IdentityRole.BACKEND, "local_admin", "service-rights-backend"),
        (IdentityRole.EDGE, "interactive_logon", "service-rights-edge"),
        (IdentityRole.MONITORING, "service_start", "monitoring-not-read-only"),
        (IdentityRole.RECOVERY, "service_start", "recovery-auto-start"),
    ],
)
def test_prohibited_identity_rights_fail(role: IdentityRole, right: str, finding: str) -> None:
    changed = tuple(
        IdentityRights(item.role, item.identity, item.rights | {right})
        if item.role is role else item for item in identities()
    )
    assert finding in validate_access_matrix(matrix(identities=changed), NOW).findings


def test_identity_separation_acl_and_quarterly_review_fail_closed() -> None:
    duplicate = list(identities())
    duplicate[-1] = IdentityRights(IdentityRole.REVIEWER, "operator-a", frozenset({"read"}))
    grants = matrix().grants + (
        AclGrant("secret", "ordinary-user", frozenset({"read"})),
        AclGrant("service-definition", "svc-backend", frozenset({"modify"})),
    )
    findings = validate_access_matrix(
        matrix(identities=tuple(duplicate), grants=grants,
               reviewed_at=NOW - timedelta(days=91)), NOW
    ).findings
    assert {"identity-separation", "ordinary-user-protected-access", "sensitive-read",
            "service-self-reconfiguration", "access-review-stale"} <= set(findings)


def metadata(status: InventoryStatus = InventoryStatus.AVAILABLE) -> CredentialMetadata:
    return CredentialMetadata(
        "credential-1", "security-owner", "backend", NOW - timedelta(days=30),
        NOW - timedelta(days=1), None, status,
    )


def test_inventory_contains_metadata_only_and_safe_missing_states() -> None:
    item = metadata(InventoryStatus.MISSING)
    assert set(item.__dataclass_fields__) == {
        "credential_id", "owner", "consumer", "created_at", "rotated_at",
        "revoked_at", "status",
    }
    assert missing_credential_state(item, "startup-required").startswith("NOT_READY")
    assert missing_credential_state(item, "mt5") == "MT5_DISCONNECTED"
    assert missing_credential_state(item, "backup") == "RECOVERY_FAIL_CLOSED_BACKEND_SAFE"


@pytest.mark.parametrize("source", list(ArtifactSource))
def test_canary_scanner_covers_every_artifact_surface(source: ArtifactSource) -> None:
    result = ArtifactCanaryScanner(("SYNTHETIC-CANARY",)).scan(
        (SyntheticArtifact(f"artifact-{source.value}", source, "SYNTHETIC-CANARY"),)
    )
    assert not result.passed
    assert result.quarantined == (f"artifact-{source.value}",)
    assert result.findings[0].category == "canary"


@pytest.mark.parametrize(
    ("content", "category"),
    [
        ("Authorization: synthetic-value", "authorization"),
        ("session_id=synthetic", "session"),
        ("password=synthetic", "credential-assignment"),
        ("PATH=C:\\synthetic", "environment-dump"),
        ("-----BEGIN PRIVATE KEY-----", "private-key"),
    ],
)
def test_scanner_quarantines_sensitive_patterns(content: str, category: str) -> None:
    result = ArtifactCanaryScanner().scan(
        (SyntheticArtifact("artifact-1", ArtifactSource.LOG, content),)
    )
    assert result.findings[0].category == category
    assert result.quarantined == ("artifact-1",)


@pytest.mark.parametrize(
    "content",
    [
        "secret=synthetic",
        "SECRET : 'synthetic value'",
        'client_secret="synthetic value"',
        "client-secret=synthetic",
        "access_key:synthetic",
        "access-key = synthetic",
    ],
)
def test_scanner_rejects_literal_secret_assignments(content: str) -> None:
    result = ArtifactCanaryScanner().scan(
        (SyntheticArtifact("artifact-secret", ArtifactSource.REPOSITORY_ADDITION, content),)
    )
    assert not result.passed
    assert result.findings[0].category == "credential-assignment"
    assert result.quarantined == ("artifact-secret",)


@pytest.mark.parametrize(
    "content",
    [
        "secret lifecycle metadata only",
        "notsecret=value",
        "secret_suffix=value",
        "secret =",
        "client secrecy is required",
    ],
)
def test_scanner_allows_secret_assignment_near_misses(content: str) -> None:
    result = ArtifactCanaryScanner().scan(
        (SyntheticArtifact("clean", ArtifactSource.REPOSITORY_ADDITION, content),)
    )
    assert result.passed and result.quarantined == ()


def test_auditors_use_only_synthetic_inputs_and_never_read_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("filesystem read attempted")

    monkeypatch.setattr(Path, "read_text", forbidden)
    result = ArtifactCanaryScanner().scan(
        (SyntheticArtifact("clean", ArtifactSource.EVIDENCE, "state=HEALTHY"),)
    )
    assert result.passed and result.quarantined == ()
    assert audit_host(host()).passed
    assert validate_access_matrix(matrix(), NOW).passed
