from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE = re.compile(
    r"(?i)(password|passwd|secret|token|cookie|credential|private[ _-]?key|"
    r"session[ _-]?id|authorization|bearer\s+[a-z0-9._-]+|BEGIN [A-Z ]*PRIVATE KEY)"
)


class LifecycleStatus(str, Enum):
    STAGED = "STAGED"
    ACTIVE = "ACTIVE"
    LAST_KNOWN_GOOD = "LAST_KNOWN_GOOD"
    RETIRED = "RETIRED"


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("timestamp must be UTC")
    return value.astimezone(timezone.utc)


def hash_identity(identity: str) -> str:
    normalized = identity.strip()
    if not normalized:
        raise ValueError("identity is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def contains_sensitive_content(value: object, canaries: tuple[str, ...] = ()) -> bool:
    serialized = json.dumps(value, default=str, sort_keys=True)
    lowered = serialized.casefold()
    return bool(_SENSITIVE.search(serialized)) or any(
        canary and canary.casefold() in lowered for canary in canaries
    )

class ReleaseManifest(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    release_id: str
    application_id: str
    application_version: str
    source_identity: str
    alembic_revision: str
    frontend_identity: str
    backend_sha256: str
    frontend_sha256: str
    nginx_sha256: str
    created_at: datetime
    lifecycle_status: LifecycleStatus = LifecycleStatus.STAGED
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator(
        "release_id", "application_id", "application_version", "source_identity",
        "alembic_revision", "frontend_identity",
    )
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("manifest identity is not a bounded safe identifier")
        return value

    @field_validator("backend_sha256", "frontend_sha256", "nginx_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("manifest digest must be lowercase SHA-256")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def reject_sensitive_content(self) -> "ReleaseManifest":
        if contains_sensitive_content(self.model_dump(mode="json")):
            raise ValueError("manifest contains prohibited sensitive content")
        return self

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        payload["created_at"] = utc_text(self.created_at)
        return json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")

    @classmethod
    def parse_bytes(cls, payload: bytes) -> "ReleaseManifest":
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("manifest must be UTF-8 JSON") from error
        return cls.model_validate_json(decoded)


class GateDecision(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ServiceGateResult(BaseModel):
    gate: str
    decision: GateDecision
    category: str
    checked_at: datetime
    observations: tuple[tuple[str, str], ...] = ()
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("gate", "category")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("gate labels must be bounded safe identifiers")
        return value

    @field_validator("checked_at")
    @classmethod
    def validate_checked_at(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("observations", mode="before")
    @classmethod
    def canonicalize_observations(cls, value: Any) -> Any:
        items = value.items() if isinstance(value, dict) else value
        normalized = tuple(sorted((str(key), str(item)) for key, item in items))
        if len(normalized) > 32 or any(
            not _SAFE_ID.fullmatch(key) or len(item) > 128
            for key, item in normalized
        ):
            raise ValueError("gate observations are not bounded/allowlisted")
        return normalized

class PathCategory(str, Enum):
    RELEASE = "RELEASE"
    STATE = "STATE"
    EVIDENCE = "EVIDENCE"
    LOG = "LOG"
    CERTIFICATE = "CERTIFICATE"
    RECOVERY = "RECOVERY"


class MutationCounters(BaseModel):
    mt5_connect: int = Field(default=0, ge=0)
    demo_start: int = Field(default=0, ge=0)
    paper_start: int = Field(default=0, ge=0)
    order_check: int = Field(default=0, ge=0)
    order_send: int = Field(default=0, ge=0)
    close: int = Field(default=0, ge=0)
    modify: int = Field(default=0, ge=0)
    cancel: int = Field(default=0, ge=0)
    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class OperatorEvidencePackage(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    event_id: str
    event_type: str
    started_at: datetime
    finished_at: datetime
    operator_identity_hash: str
    reviewer_identity_hash: str
    release_id: str
    revision: str
    process_manager: str
    gate_results: tuple[ServiceGateResult, ...]
    process_state: str
    listener_state: str
    lease_state: str
    trading_safe: bool
    mutation_counters: MutationCounters = Field(default_factory=MutationCounters)
    certificate_summary: tuple[tuple[str, str], ...] = ()
    capacity_summary: tuple[tuple[str, str], ...] = ()
    recovery_summary: tuple[tuple[str, str], ...] = ()
    monitoring_summary: tuple[tuple[str, str], ...] = ()
    path_categories: tuple[PathCategory, ...] = ()
    final_decision: GateDecision
    signed_off_at: datetime
    retain_until: datetime
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("event_id", "event_type", "release_id", "revision")
    @classmethod
    def validate_evidence_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("evidence identifier is invalid")
        return value

    @field_validator("operator_identity_hash", "reviewer_identity_hash")
    @classmethod
    def validate_identity_hash(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("identity must be represented by a SHA-256 hash")
        return value

    @field_validator("started_at", "finished_at", "signed_off_at", "retain_until")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator(
        "certificate_summary", "capacity_summary", "recovery_summary",
        "monitoring_summary", mode="before",
    )
    @classmethod
    def canonicalize_summary(cls, value: Any) -> Any:
        items = value.items() if isinstance(value, dict) else value
        result = tuple(sorted((str(key), str(item)) for key, item in items))
        if len(result) > 32 or any(
            not _SAFE_ID.fullmatch(key) or len(item) > 128 for key, item in result
        ):
            raise ValueError("evidence summary is not bounded/allowlisted")
        return result

    @field_validator("gate_results", mode="before")
    @classmethod
    def canonicalize_gates(cls, value: Any) -> Any:
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.gate if isinstance(item, ServiceGateResult)
                    else str(item.get("gate", ""))
                ),
            )
        )

    @field_validator("path_categories", mode="before")
    @classmethod
    def canonicalize_paths(cls, value: Any) -> Any:
        return tuple(sorted(set(value), key=str))

    @model_validator(mode="after")
    def validate_signoff(self) -> "OperatorEvidencePackage":
        if self.operator_identity_hash == self.reviewer_identity_hash:
            raise ValueError("operator and reviewer must be different identities")
        if not self.started_at <= self.finished_at <= self.signed_off_at:
            raise ValueError("evidence timestamps are inconsistent")
        if (self.retain_until - self.signed_off_at).days < 180:
            raise ValueError("evidence retention must be at least 180 days")
        decisions = {gate.decision for gate in self.gate_results}
        expected = GateDecision.FAIL if GateDecision.FAIL in decisions else GateDecision.PASS
        if self.final_decision is not expected:
            raise ValueError("final decision disagrees with gate results")
        if self.trading_safe and self.mutation_counters.total != 0:
            raise ValueError("trading-safe evidence requires zero lifecycle mutation")
        if contains_sensitive_content(self.model_dump(mode="json")):
            raise ValueError("evidence contains prohibited sensitive content")
        return self

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        for field in ("started_at", "finished_at", "signed_off_at", "retain_until"):
            payload[field] = utc_text(getattr(self, field))
        return json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
