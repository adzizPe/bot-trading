from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol


class CertificateLevel(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class CertificateObservation:
    hostname: str
    expected_hostname: str
    not_before: datetime
    not_after: datetime
    chain_valid: bool
    approved_fingerprint: bool
    key_pair_matches: bool
    certificate_readable: bool
    private_key_readable: bool
    acl_valid: bool
    nginx_config_valid: bool
    external_valid: bool
    ocsp_observed: bool
    fingerprint: str

    def __post_init__(self) -> None:
        for value in (self.not_before, self.not_after):
            if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
                raise ValueError("certificate dates must be UTC")
        if not self.fingerprint or len(self.fingerprint) > 128:
            raise ValueError("certificate fingerprint must be bounded")


@dataclass(frozen=True)
class CertificateAssessment:
    level: CertificateLevel
    reason: str
    days_remaining: int


def assess_certificate(observation: CertificateObservation, now: datetime) -> CertificateAssessment:
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
        raise ValueError("assessment time must be UTC")
    remaining = int((observation.not_after - now).total_seconds() // 86400)
    invalid = {
        "hostname": observation.hostname.casefold() != observation.expected_hostname.casefold(),
        "date": now < observation.not_before or now >= observation.not_after,
        "chain": not observation.chain_valid,
        "fingerprint": not observation.approved_fingerprint,
        "key_pair": not observation.key_pair_matches,
        "certificate_readability": not observation.certificate_readable,
        "private_key_readability": not observation.private_key_readable,
        "acl": not observation.acl_valid,
        "nginx_config": not observation.nginx_config_valid,
        "external": not observation.external_valid,
    }
    for reason, failed in invalid.items():
        if failed:
            return CertificateAssessment(CertificateLevel.CRITICAL, reason, remaining)
    if remaining <= 14:
        return CertificateAssessment(CertificateLevel.CRITICAL, "expiry", remaining)
    if remaining <= 30:
        return CertificateAssessment(CertificateLevel.WARNING, "expiry", remaining)
    return CertificateAssessment(CertificateLevel.HEALTHY, "valid", remaining)


class CertificateAdapter(Protocol):
    async def backup_active(self) -> str: ...

    async def stage_candidate(self, fingerprint: str) -> None: ...

    async def nginx_test(self) -> bool: ...

    async def reload(self) -> None: ...

    async def external_observation(
        self, fingerprint: str, timeout_seconds: int
    ) -> bool: ...

    async def restore_active(self, backup_id: str) -> None: ...


@dataclass(frozen=True)
class CertificateTransactionResult:
    committed: bool
    rolled_back: bool
    reason: str
    old_fingerprint: str
    new_fingerprint: str


@dataclass
class CertificateTransaction:
    adapter: CertificateAdapter
    external_timeout_seconds: int = 5

    def __post_init__(self) -> None:
        if not 1 <= self.external_timeout_seconds <= 5:
            raise ValueError("external certificate check must be at most 5 seconds")

    async def apply(
        self, *, active: CertificateObservation, candidate: CertificateObservation,
        now: datetime,
    ) -> CertificateTransactionResult:
        backup_id = await self.adapter.backup_active()
        assessment = assess_certificate(candidate, now)
        if assessment.level is CertificateLevel.CRITICAL:
            return self._result(False, False, f"candidate-{assessment.reason}", active, candidate)
        await self.adapter.stage_candidate(candidate.fingerprint)
        if not await self.adapter.nginx_test():
            await self.adapter.restore_active(backup_id)
            return self._result(False, True, "candidate-nginx-config", active, candidate)
        await self.adapter.reload()
        verified = await self.adapter.external_observation(
            candidate.fingerprint, self.external_timeout_seconds
        )
        if verified and candidate.ocsp_observed:
            return self._result(True, False, "committed", active, candidate)
        await self.adapter.restore_active(backup_id)
        if not await self.adapter.nginx_test():
            return self._result(False, True, "rollback-config-failed", active, candidate)
        await self.adapter.reload()
        return self._result(False, True, "external-verification-failed", active, candidate)

    @staticmethod
    def _result(
        committed: bool, rolled_back: bool, reason: str,
        active: CertificateObservation, candidate: CertificateObservation,
    ) -> CertificateTransactionResult:
        return CertificateTransactionResult(
            committed, rolled_back, reason, active.fingerprint, candidate.fingerprint
        )
