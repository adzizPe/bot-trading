from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from app.operations.models import contains_sensitive_content, utc_text

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_FIELDS = frozenset(
    {"component", "state", "release_id", "result", "duration_ms", "event_ref"}
)
_ALLOWED_SOURCES = frozenset(
    {"NGINX_ACCESS", "NGINX_WEBSOCKET", "NGINX_ERROR", "BACKEND_STDIO",
     "WINDOWS_EVENT", "TASK_SCHEDULER", "RECOVERY_JSONL"}
)


@dataclass(frozen=True)
class OperationalLogRecord:
    occurred_at: datetime
    category: str
    event_id: str
    change_id: str | None
    source: str
    fields: tuple[tuple[str, str], ...] = ()

    @classmethod
    def build(
        cls, *, occurred_at: datetime, category: str, event_id: str,
        source: str, change_id: str | None = None,
        fields: dict[str, object] | None = None,
    ) -> "OperationalLogRecord":
        if occurred_at.tzinfo is None or occurred_at.utcoffset() != timezone.utc.utcoffset(occurred_at):
            raise ValueError("operational log timestamp must be UTC")
        labels = (category, event_id) + ((change_id,) if change_id else ())
        if any(not _LABEL.fullmatch(value) for value in labels):
            raise ValueError("operational log label is invalid")
        if source not in _ALLOWED_SOURCES:
            raise ValueError("operational log source is not allowlisted")
        values = fields or {}
        if not set(values) <= _ALLOWED_FIELDS:
            raise ValueError("operational log field is not allowlisted")
        normalized = tuple(sorted((key, str(value)) for key, value in values.items()))
        if len(normalized) > 12 or any(len(value) > 128 for _, value in normalized):
            raise ValueError("operational log fields are not bounded")
        record = cls(occurred_at, category, event_id, change_id, source, normalized)
        if contains_sensitive_content(record.as_dict()):
            raise ValueError("operational log contains sensitive content")
        return record

    def as_dict(self) -> dict[str, object]:
        return {
            "occurred_at": utc_text(self.occurred_at),
            "category": self.category,
            "event_id": self.event_id,
            "change_id": self.change_id,
            "source": self.source,
            "fields": dict(self.fields),
        }

    def json_line(self) -> bytes:
        return (
            json.dumps(self.as_dict(), ensure_ascii=True, separators=(",", ":"),
                       sort_keys=True) + "\n"
        ).encode("ascii")


@dataclass(frozen=True)
class LogEvidenceReference:
    source: str
    first_event_id: str
    last_event_id: str
    started_at: datetime
    finished_at: datetime

    def __post_init__(self) -> None:
        if self.source not in _ALLOWED_SOURCES:
            raise ValueError("log reference source is not allowlisted")
        if not _LABEL.fullmatch(self.first_event_id) or not _LABEL.fullmatch(self.last_event_id):
            raise ValueError("log reference event is invalid")
        if self.started_at > self.finished_at:
            raise ValueError("log reference range is invalid")
