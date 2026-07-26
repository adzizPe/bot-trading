from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any, TextIO

_ALLOWED_FIELDS = frozenset(
    {
        "backup_id",
        "category",
        "count",
        "elapsed_ms",
        "event",
        "message",
        "operation_id",
        "restore_id",
        "result",
        "status",
    }
)
_TOKEN_FIELDS = frozenset(
    {"backup_id", "category", "event", "operation_id", "restore_id", "result", "status"}
)
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:password|passwd|secret|token|credential|api[_-]?key|encryption[_-]?key)"
    r"|(?:[a-z][a-z0-9+.-]*://)"
    r"|(?:[A-Za-z]:[\\/])"
    r"|(?:\\\\[^\\\s]+\\)"
    r"|(?:/(?:[^\s/]+/)+[^\s]*)"
)
_MAX_VALUE_CHARS = 256


class StructuredEventLogger:
    """Bounded JSONL writer that accepts only explicitly safe fields."""

    def __init__(
        self,
        stream: TextIO,
        *,
        max_line_bytes: int = 2048,
        secret_values: tuple[str, ...] = (),
    ) -> None:
        if max_line_bytes < 256:
            raise ValueError("structured log line bound must be at least 256 bytes")
        self._stream = stream
        self._max_line_bytes = max_line_bytes
        self._secret_values = tuple(value for value in secret_values if value)
        self._lock = Lock()

    def write(self, event: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": self._safe_token(event),
        }
        for key, value in fields.items():
            if key not in _ALLOWED_FIELDS or key == "event" or value is None:
                continue
            safe = self._safe_value(key, value)
            if safe is not None:
                record[key] = safe
        encoded = self._encode(record)
        if len(encoded) > self._max_line_bytes:
            record.pop("message", None)
            record["result"] = "TRUNCATED"
            encoded = self._encode(record)
        if len(encoded) > self._max_line_bytes:
            record = {
                "timestamp": record["timestamp"],
                "event": record["event"],
                "result": "TRUNCATED",
            }
            encoded = self._encode(record)
        with self._lock:
            self._stream.write(encoded.decode("utf-8") + "\n")
            self._stream.flush()

    def write_exception(
        self,
        event: str,
        error: BaseException,
        *,
        operation_id: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "category": exception_category(error),
            "result": "FAILED",
            "message": "operation failed; inspect local diagnostics",
        }
        if operation_id is not None:
            fields["operation_id"] = operation_id
        self.write(event, **fields)

    def _safe_value(self, key: str, value: Any) -> str | int | float | bool | None:
        if key in _TOKEN_FIELDS:
            return self._safe_token(str(value))
        if key in {"count", "elapsed_ms"}:
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or value < 0
            ):
                return None
            return value
        if key == "message":
            text = str(value).replace("\r", " ").replace("\n", " ")
            if self._contains_sensitive(text):
                return "[REDACTED]"
            return text[:_MAX_VALUE_CHARS]
        return None

    def _contains_sensitive(self, value: str) -> bool:
        lowered = value.casefold()
        return bool(_SENSITIVE_TEXT.search(value)) or any(
            secret.casefold() in lowered for secret in self._secret_values
        )

    @staticmethod
    def _safe_token(value: str) -> str:
        if not _SAFE_TOKEN.fullmatch(value):
            return "REDACTED"
        return value

    @staticmethod
    def _encode(record: dict[str, Any]) -> bytes:
        return json.dumps(
            record, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")


def exception_category(error: BaseException) -> str:
    if isinstance(
        error, (FileNotFoundError, PermissionError, IsADirectoryError, OSError)
    ):
        return "FILESYSTEM"
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, ValueError):
        return "VALIDATION"
    return "INTERNAL"


def contains_secret_canary(
    text: str,
    canaries: tuple[str, ...],
) -> bool:
    lowered = text.casefold()
    return any(canary and canary.casefold() in lowered for canary in canaries)


def scan_jsonl_for_secret_canaries(
    path: Path,
    canaries: tuple[str, ...],
) -> None:
    content = path.read_text(encoding="utf-8")
    if contains_secret_canary(content, canaries):
        raise AssertionError("secret canary was present in structured output")
    for line in content.splitlines():
        parsed = json.loads(line)
        if not isinstance(parsed, dict) or not set(parsed).issubset(
            _ALLOWED_FIELDS | {"timestamp"}
        ):
            raise AssertionError("structured output contains non-allowlisted fields")


StructuredJsonLogger = StructuredEventLogger
