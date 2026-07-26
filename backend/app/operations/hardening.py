from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class HardeningException:
    finding: str
    owner: str
    compensating_control: str
    reviewer: str
    approved_at: datetime
    expires_at: datetime

    def valid(self, now: datetime) -> bool:
        return (
            bool(self.finding and self.owner and self.compensating_control and self.reviewer)
            and self.owner != self.reviewer
            and self.approved_at <= now < self.expires_at
            and self.expires_at - self.approved_at <= timedelta(days=30)
        )


@dataclass(frozen=True)
class Listener:
    port: int
    address: str
    owner: str
    purpose: str
    approved: bool
    public: bool


@dataclass(frozen=True)
class UpdateState:
    supported: bool
    critical_identified_at: datetime | None = None
    assessed_at: datetime | None = None
    applied_at: datetime | None = None
    approved_exception: bool = False


@dataclass(frozen=True)
class HostSnapshot:
    observed_at: datetime
    last_review_at: datetime
    update: UpdateState
    firewall_enabled: bool
    firewall_default_deny: bool
    allowed_public_ports: tuple[int, ...]
    admin_sources_approved: bool
    listeners: tuple[Listener, ...]
    malware_protection: bool
    audit_policy: bool
    time_sync: bool
    clock_drift_seconds: float
    event_log_retention_days: int
    remote_admin_named_accounts: bool
    remote_admin_approved_sources: bool
    debug: bool = False
    docs: bool = False
    vite_dev: bool = False
    build_watcher: bool = False
    uvicorn_reload: bool = False
    container_runtime: bool = False
    external_queue: bool = False


@dataclass(frozen=True)
class HardeningResult:
    passed: bool
    findings: tuple[str, ...]
    exceptions_applied: tuple[str, ...]
    update_completion_allowed: bool


def audit_host(
    snapshot: HostSnapshot, exceptions: tuple[HardeningException, ...] = ()
) -> HardeningResult:
    now = snapshot.observed_at
    _require_utc(now)
    findings: set[str] = set()
    if now - snapshot.last_review_at > timedelta(days=90):
        findings.add("review-stale")
    if not snapshot.update.supported:
        findings.add("update-unsupported")
    _audit_update(snapshot.update, now, findings)
    if not snapshot.firewall_enabled or not snapshot.firewall_default_deny:
        findings.add("firewall-baseline")
    if not set(snapshot.allowed_public_ports) <= {80, 443}:
        findings.add("firewall-public-port")
    if not snapshot.admin_sources_approved:
        findings.add("admin-source")
    for listener in snapshot.listeners:
        if not listener.owner or not listener.purpose or not listener.approved:
            findings.add("listener-unapproved")
        if listener.public and listener.port not in {80, 443}:
            findings.add("listener-public-exposure")
        if listener.public and listener.port in {8000, 8080}:
            findings.add("backend-status-public")
    controls = {
        "malware": snapshot.malware_protection,
        "audit-policy": snapshot.audit_policy,
        "time-sync": snapshot.time_sync,
    }
    findings.update(name for name, enabled in controls.items() if not enabled)
    if abs(snapshot.clock_drift_seconds) > 60:
        findings.add("clock-drift")
    if snapshot.event_log_retention_days < 30:
        findings.add("eventlog-retention")
    if not snapshot.remote_admin_named_accounts or not snapshot.remote_admin_approved_sources:
        findings.add("remote-admin-scope")
    prohibited = {
        "debug": snapshot.debug, "docs": snapshot.docs, "vite-dev": snapshot.vite_dev,
        "build-watcher": snapshot.build_watcher, "uvicorn-reload": snapshot.uvicorn_reload,
        "container-runtime": snapshot.container_runtime,
        "external-queue": snapshot.external_queue,
    }
    findings.update(name for name, enabled in prohibited.items() if enabled)

    valid_exceptions = {
        exception.finding for exception in exceptions if exception.valid(now)
    }
    applied = tuple(sorted(findings & valid_exceptions))
    remaining = tuple(sorted(findings - valid_exceptions))
    return HardeningResult(
        not remaining, remaining, applied, "clock-drift" not in findings
    )


def _audit_update(update: UpdateState, now: datetime, findings: set[str]) -> None:
    identified = update.critical_identified_at
    if identified is None:
        return
    if update.assessed_at is None or update.assessed_at - identified > timedelta(hours=24):
        findings.add("critical-update-assessment")
    if update.approved_exception and update.applied_at is None:
        findings.add("critical-update-exception-metadata")
    completed = update.applied_at is not None
    if not completed and now - identified > timedelta(days=7):
        findings.add("critical-update-window")
    if update.applied_at is not None and update.applied_at - identified > timedelta(days=7):
        findings.add("critical-update-window")


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("hardening timestamps must be UTC")
