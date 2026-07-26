from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re


class InventoryStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    ROTATION_DUE = "ROTATION_DUE"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class CredentialMetadata:
    credential_id: str
    owner: str
    consumer: str
    created_at: datetime
    rotated_at: datetime | None
    revoked_at: datetime | None
    status: InventoryStatus

    def __post_init__(self) -> None:
        if not self.credential_id or not self.owner or not self.consumer:
            raise ValueError("inventory metadata is incomplete")
        for value in (self.created_at, self.rotated_at, self.revoked_at):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value)
            ):
                raise ValueError("inventory timestamps must be UTC")


class ArtifactSource(str, Enum):
    REPOSITORY_ADDITION = "REPOSITORY_ADDITION"
    RELEASE_ARTIFACT = "RELEASE_ARTIFACT"
    PROCESS_ARGV = "PROCESS_ARGV"
    SERVICE_DEFINITION = "SERVICE_DEFINITION"
    TASK_DEFINITION = "TASK_DEFINITION"
    LOG = "LOG"
    ERROR = "ERROR"
    MONITORING = "MONITORING"
    CRASH_OUTPUT = "CRASH_OUTPUT"
    EVIDENCE = "EVIDENCE"


@dataclass(frozen=True)
class SyntheticArtifact:
    artifact_id: str
    source: ArtifactSource
    content: str


@dataclass(frozen=True)
class ScanFinding:
    artifact_id: str
    source: ArtifactSource
    category: str


@dataclass(frozen=True)
class ScanResult:
    passed: bool
    findings: tuple[ScanFinding, ...]
    quarantined: tuple[str, ...]


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY", re.IGNORECASE)),
    ("authorization", re.compile(r"(?:authorization\s*:\s*|bearer\s+)[^\s]+", re.IGNORECASE)),
    ("session", re.compile(r"(?:session(?:_id)?|cookie)\s*[:=]", re.IGNORECASE)),
    ("credential-assignment", re.compile(
        r"(?<![A-Z0-9_])(?:password|passwd|token|api[_-]?key|credential|"
        r"secret|client[_-]?secret|access[_-]?key)(?![A-Z0-9_])"
        r"\s*[:=]\s*(?:\"[^\"\r\n]+\"|'[^'\r\n]+'|[^\s,;]+)",
        re.IGNORECASE,
    )),
    ("environment-dump", re.compile(
        r"(?:^|\n)(?:PATH|HOME|USERPROFILE|DATABASE_URL|MT5_[A-Z_]+)\s*=", re.IGNORECASE
    )),
)


@dataclass(frozen=True)
class ArtifactCanaryScanner:
    canaries: tuple[str, ...] = ()

    def scan(self, artifacts: tuple[SyntheticArtifact, ...]) -> ScanResult:
        findings: list[ScanFinding] = []
        for artifact in artifacts:
            category = self._category(artifact.content)
            if category is not None:
                findings.append(ScanFinding(artifact.artifact_id, artifact.source, category))
        findings.sort(key=lambda item: (item.artifact_id, item.source.value, item.category))
        quarantined = tuple(sorted({item.artifact_id for item in findings}))
        return ScanResult(not findings, tuple(findings), quarantined)

    def _category(self, content: str) -> str | None:
        lowered = content.casefold()
        if any(canary and canary.casefold() in lowered for canary in self.canaries):
            return "canary"
        for category, pattern in _PATTERNS:
            if pattern.search(content):
                return category
        return None


def missing_credential_state(metadata: CredentialMetadata, purpose: str) -> str:
    if metadata.status is not InventoryStatus.MISSING:
        return "AVAILABLE"
    if purpose == "startup-required":
        return "NOT_READY_MISSING_REQUIRED_CONFIGURATION"
    if purpose == "mt5":
        return "MT5_DISCONNECTED"
    if purpose == "backup":
        return "RECOVERY_FAIL_CLOSED_BACKEND_SAFE"
    raise ValueError("credential purpose is not allowlisted")
