from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from hypothesis import given, strategies as st
from pydantic import ValidationError

from app.operations import (
    EvidenceAlreadyPublishedError,
    EvidenceQuarantinedError,
    EvidenceStore,
    GateDecision,
    MutationCounters,
    OperationalPaths,
    OperationalPolicy,
    OperatorEvidencePackage,
    PathCategory,
    ReleaseManifest,
    RestoreHoldStatus,
    RestoreHoldStore,
    ServiceGateResult,
    hash_identity,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
DIGEST = "a" * 64


def manifest(**updates: object) -> ReleaseManifest:
    values = {
        "release_id": "release-1", "application_id": "trading-bot",
        "application_version": "1.2.3", "source_identity": "commit-abc",
        "alembic_revision": "rev-1", "frontend_identity": "vite-1",
        "backend_sha256": DIGEST, "frontend_sha256": "b" * 64,
        "nginx_sha256": "c" * 64, "created_at": NOW,
    }
    values.update(updates)
    return ReleaseManifest(**values)


def operational_paths(root: Path, **updates: Path) -> OperationalPaths:
    values = {
        "release_root": root / "releases", "state_root": root / "state",
        "evidence_root": root / "evidence", "log_root": root / "logs",
        "certificate_root": root / "certificates", "nginx_root": root / "nginx",
        "recovery_root": root / "recovery", "active_reference": root / "current",
        "active_sqlite": root / "data" / "app.db",
    }
    values.update(updates)
    return OperationalPaths(**values)

def gate(name: str, decision: GateDecision = GateDecision.PASS) -> ServiceGateResult:
    return ServiceGateResult(
        gate=name, decision=decision, category="synthetic", checked_at=NOW,
        observations={"lease": "ACQUIRED", "database": "READABLE"},
    )


def evidence(*, gates: tuple[ServiceGateResult, ...] | None = None) -> OperatorEvidencePackage:
    return OperatorEvidencePackage(
        event_id="event-1", event_type="COLD_BOOT", started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        operator_identity_hash=hash_identity("operator-a"),
        reviewer_identity_hash=hash_identity("reviewer-b"),
        release_id="release-1", revision="rev-1", process_manager="NSSM",
        gate_results=gates or (gate("backend"), gate("edge")),
        process_state="ONE_BACKEND", listener_state="LOOPBACK_ONLY",
        lease_state="ACQUIRED", trading_safe=True,
        mutation_counters=MutationCounters(),
        certificate_summary={"state": "VALID"},
        capacity_summary={"state": "AVAILABLE"},
        recovery_summary={"state": "AVAILABLE"},
        monitoring_summary={"state": "HEALTHY"},
        path_categories=(PathCategory.LOG, PathCategory.RELEASE),
        final_decision=GateDecision.PASS,
        signed_off_at=NOW + timedelta(seconds=2),
        retain_until=NOW + timedelta(days=181),
    )


def test_paths_are_canonical_and_roles_cannot_alias_or_overlap_database(
    tmp_path: Path,
) -> None:
    paths = operational_paths(tmp_path)
    assert paths.release_root.is_absolute()
    with pytest.raises(ValidationError, match="alias"):
        operational_paths(tmp_path, state_root=tmp_path / "releases")
    with pytest.raises(ValidationError, match="active SQLite"):
        operational_paths(tmp_path, state_root=tmp_path / "data")
    with pytest.raises(ValidationError, match="traversal"):
        operational_paths(tmp_path, log_root=tmp_path / "logs" / ".." / "other")


def test_paths_reject_symlink_ambiguity(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable for this test identity")
    with pytest.raises(ValidationError, match="link or reparse"):
        operational_paths(tmp_path / "layout", state_root=link / "state")


def test_policy_defaults_are_bounded_and_nssm_canonical() -> None:
    policy = OperationalPolicy()
    assert policy.selected_process_manager.value == "NSSM"
    assert policy.backend_readiness_timeout_seconds == 120
    assert policy.restart_delay_seconds == 30
    assert policy.restart_max_attempts == 3

def test_manifest_is_strict_deterministic_and_secret_free() -> None:
    first = manifest()
    second = ReleaseManifest.parse_bytes(first.canonical_bytes())
    assert second == first
    assert first.canonical_bytes() == second.canonical_bytes()
    with pytest.raises(ValidationError):
        manifest(extra="not-allowed")
    with pytest.raises(ValidationError, match="sensitive"):
        manifest(source_identity="secret-value")
    malformed = json.loads(first.canonical_bytes())
    malformed["backend_sha256"] = "not-a-digest"
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(malformed)


def test_restore_hold_missing_is_absent_but_partial_and_malformed_fail_closed(
    tmp_path: Path,
) -> None:
    store = RestoreHoldStore(tmp_path / "state" / "restore-hold.json")
    assert store.read() is None
    store.partial_path.parent.mkdir(parents=True)
    store.partial_path.write_text("interrupted", encoding="utf-8")
    interrupted = store.read()
    assert interrupted is not None
    assert interrupted.status is RestoreHoldStatus.HELD and interrupted.ambiguous
    store.partial_path.unlink()
    store.path.write_text("{bad", encoding="utf-8")
    malformed = store.read()
    assert malformed is not None
    assert malformed.status is RestoreHoldStatus.HELD and malformed.ambiguous


def test_restore_hold_atomic_round_trip_and_two_person_release(tmp_path: Path) -> None:
    store = RestoreHoldStore(tmp_path / "state" / "restore-hold.json")
    held = store.enter(
        change_id="change-1", reason="offline-restore",
        operator_identity="operator-a", reviewer_identity="reviewer-b",
        restore_id="restore-1", clock=lambda: NOW,
    )
    assert store.read() == held
    assert not store.partial_path.exists()
    with pytest.raises(ValidationError, match="reviewer separation"):
        store.release(
            held=held, operator_identity="same", reviewer_identity="same",
            restore_result="SUCCESS", evidence_valid=True, clock=lambda: NOW,
        )
    released = store.release(
        held=held, operator_identity="operator-a", reviewer_identity="reviewer-b",
        restore_result="SUCCESS", evidence_valid=True, clock=lambda: NOW,
    )
    assert released.status is RestoreHoldStatus.RELEASED

def test_evidence_requires_separation_retention_and_consistent_decision() -> None:
    package = evidence()
    assert package.mutation_counters.total == 0
    values = package.model_dump()
    values["reviewer_identity_hash"] = values["operator_identity_hash"]
    with pytest.raises(ValidationError, match="different identities"):
        OperatorEvidencePackage.model_validate(values)
    values = package.model_dump()
    values["retain_until"] = package.signed_off_at + timedelta(days=179)
    with pytest.raises(ValidationError, match="retention"):
        OperatorEvidencePackage.model_validate(values)
    with pytest.raises(ValidationError, match="disagrees"):
        evidence(gates=(gate("backend", GateDecision.FAIL),))


def test_evidence_publication_is_atomic_immutable_and_quarantines_canary(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    package = evidence()
    target = store.publish(package)
    assert target.read_bytes() == package.canonical_bytes()
    assert not target.with_name(f"{target.name}.partial").exists()
    with pytest.raises(EvidenceAlreadyPublishedError):
        store.publish(package)
    other = package.model_copy(update={"event_id": "event-2"})
    with pytest.raises(EvidenceQuarantinedError):
        store.publish(other, secret_canaries=("event-2",))
    assert not (tmp_path / "evidence" / "event-2.json").exists()


@given(st.permutations((gate("backend"), gate("edge"), gate("release"))))
def test_property_evidence_is_deterministic_for_enumeration_order(
    gates: list[ServiceGateResult],
) -> None:
    reference = evidence(gates=(gate("backend"), gate("edge"), gate("release")))
    assert evidence(gates=tuple(gates)).canonical_bytes() == reference.canonical_bytes()


@given(st.text(min_size=1).filter(lambda value: bool(value.strip())))
def test_property_identity_hash_does_not_preserve_raw_identity(value: str) -> None:
    digest = hash_identity(value)
    assert len(digest) == 64
    assert digest != value
