from __future__ import annotations

import ctypes
from dataclasses import dataclass
import platform
from typing import Protocol

from app.observability.models import AlertCategory, MetricState
from app.operations.models import contains_sensitive_content


class EventLogAdapter(Protocol):
    def write(self, source: str, event_id: int, level: int, message: str) -> bool: ...


class UnavailableEventLog:
    def write(self, source: str, event_id: int, level: int, message: str) -> bool:
        _ = (source, event_id, level, message)
        return False


class NativeWindowsEventLog:
    """Best-effort native Event Log writer; it never installs/registers a source."""

    def write(self, source: str, event_id: int, level: int, message: str) -> bool:
        if platform.system() != "Windows":
            return False
        advapi = ctypes.windll.advapi32  # type: ignore[attr-defined]
        advapi.RegisterEventSourceW.restype = ctypes.c_void_p
        advapi.ReportEventW.restype = ctypes.c_int
        handle = advapi.RegisterEventSourceW(None, source)
        if not handle:
            return False
        strings = (ctypes.c_wchar_p * 1)(message)
        try:
            return bool(advapi.ReportEventW(
                handle, level, 0, event_id, None, 1, 0, strings, None
            ))
        finally:
            advapi.DeregisterEventSource(handle)


@dataclass(frozen=True)
class WindowsEventLogSink:
    adapter: EventLogAdapter
    source: str = "TradingBotObservability"

    def emit(
        self, category: AlertCategory, state: str, severity: MetricState
    ) -> bool:
        event_ids = {
            "OPEN": 10901,
            "ESCALATED": 10902,
            "RECOVERED": 10903,
        }
        levels = {
            MetricState.HEALTHY: 4,
            MetricState.WARNING: 2,
            MetricState.CRITICAL: 1,
            MetricState.UNKNOWN: 2,
        }
        event_id = event_ids.get(state)
        if event_id is None:
            return False
        message = f"category={category.value};state={state};severity={severity.value}"
        if (
            len(message) > 256
            or contains_sensitive_content({"source": self.source, "message": message})
            or not self.source.isascii()
            or len(self.source) > 64
        ):
            return False
        try:
            return bool(self.adapter.write(
                self.source, event_id, levels[severity], message
            ))
        except Exception:
            return False
