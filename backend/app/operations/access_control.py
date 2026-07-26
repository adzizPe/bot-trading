from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


class IdentityRole(str, Enum):
    BACKEND = "BACKEND"
    EDGE = "EDGE"
    RECOVERY = "RECOVERY"
    MONITORING = "MONITORING"
    OPERATOR = "OPERATOR"
    REVIEWER = "REVIEWER"


@dataclass(frozen=True)
class IdentityRights:
    role: IdentityRole
    identity: str
    rights: frozenset[str]


@dataclass(frozen=True)
class AclGrant:
    resource: str
    identity: str
    rights: frozenset[str]


@dataclass(frozen=True)
class AccessMatrix:
    identities: tuple[IdentityRights, ...]
    grants: tuple[AclGrant, ...]
    reviewed_at: datetime


@dataclass(frozen=True)
class AccessValidation:
    passed: bool
    findings: tuple[str, ...]


_PROTECTED_RESOURCES = frozenset(
    {"release", "config", "data", "logs", "recovery", "evidence",
     "certificate", "secret", "service-definition"}
)
_SERVICE_ROLES = frozenset({IdentityRole.BACKEND, IdentityRole.EDGE})


def validate_access_matrix(matrix: AccessMatrix, now: datetime) -> AccessValidation:
    _utc(now)
    findings: set[str] = set()
    by_role = {item.role: item for item in matrix.identities}
    if set(by_role) != set(IdentityRole) or len({item.identity for item in matrix.identities}) != len(IdentityRole):
        findings.add("identity-separation")
    for item in matrix.identities:
        if item.role in _SERVICE_ROLES and item.rights & {
            "local_admin", "interactive_logon", "service_reconfigure"
        }:
            findings.add(f"service-rights-{item.role.value.lower()}")
        if item.role is IdentityRole.MONITORING and item.rights - {"read", "logon_batch"}:
            findings.add("monitoring-not-read-only")
        if item.role is IdentityRole.RECOVERY and "service_start" in item.rights:
            findings.add("recovery-auto-start")
        if "service_reconfigure" in item.rights and item.role not in {
            IdentityRole.OPERATOR, IdentityRole.REVIEWER
        }:
            findings.add("service-definition-mutable")

    known = {item.identity for item in matrix.identities}
    for grant in matrix.grants:
        if grant.resource not in _PROTECTED_RESOURCES:
            findings.add("acl-resource-unknown")
        if grant.identity not in known:
            if grant.rights & {"read", "write", "modify", "full_control"}:
                findings.add("ordinary-user-protected-access")
        if grant.resource in {"secret", "certificate"} and grant.identity not in known:
            if "read" in grant.rights:
                findings.add("sensitive-read")
        if grant.resource == "service-definition" and grant.identity in known:
            role = next(item.role for item in matrix.identities if item.identity == grant.identity)
            if role in _SERVICE_ROLES and grant.rights & {"write", "modify", "full_control"}:
                findings.add("service-self-reconfiguration")
    if now - matrix.reviewed_at > timedelta(days=90):
        findings.add("access-review-stale")
    return AccessValidation(not findings, tuple(sorted(findings)))


def _utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("access review timestamps must be UTC")
