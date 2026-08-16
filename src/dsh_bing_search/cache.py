from __future__ import annotations

import time
from collections import OrderedDict
from threading import RLock
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Small process-local LRU cache with monotonic TTL expiry."""

    def __init__(self, *, maxsize: int, ttl_seconds: float) -> None:
        self.maxsize = max(1, maxsize)
        self.ttl_seconds = max(0.0, ttl_seconds)
        self._data: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = RLock()

    def get(self, key: str) -> T | None:
        if self.ttl_seconds <= 0:
            return None
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= time.monotonic():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: T) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            self._data[key] = (time.monotonic() + self.ttl_seconds, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
