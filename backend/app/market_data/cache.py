from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Hashable


@dataclass
class _CacheEntry:
    expires_at: float
    value: Any


class TTLCache:
    """Small bounded in-memory cache using monotonic time."""

    def __init__(
        self,
        max_entries: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, _CacheEntry] = OrderedDict()

    def get(self, key: Hashable) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return deepcopy(entry.value)

    def set(self, key: Hashable, value: Any, ttl_seconds: float) -> None:
        self._entries[key] = _CacheEntry(self._clock() + ttl_seconds, deepcopy(value))
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
