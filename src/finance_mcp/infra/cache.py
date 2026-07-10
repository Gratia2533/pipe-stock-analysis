from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _CacheEntry(Generic[T]):
    expires_at: float
    value: T


class AsyncTTLCache(Generic[T]):
    """Small in-memory TTL cache with LRU-style eviction."""

    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must not be negative")
        if max_entries <= 0:
            raise ValueError("max_entries must be greater than zero")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _CacheEntry[T]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        if self._ttl_seconds == 0:
            return None
        now = time.monotonic()
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return deepcopy(entry.value)

    async def set(self, key: str, value: T) -> None:
        if self._ttl_seconds == 0:
            return
        async with self._lock:
            self._entries[key] = _CacheEntry(
                expires_at=time.monotonic() + self._ttl_seconds,
                value=deepcopy(value),
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()

    async def size(self) -> int:
        async with self._lock:
            return len(self._entries)
