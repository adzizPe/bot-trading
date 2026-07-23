from typing import Any

SENSITIVE_KEYS = {"password", "token", "secret", "credential", "comment", "error"}


def sanitize_audit(value: Any) -> Any:
    """Return JSON-safe audit data without vendor text or credentials."""
    if isinstance(value, dict):
        return {
            str(key): sanitize_audit(item)
            for key, item in value.items()
            if str(key).lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_audit(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return type(value).__name__


class ExecutionAuditService:
    """Central sanitizer facade for persisted demo execution audit data."""

    sanitize = staticmethod(sanitize_audit)
