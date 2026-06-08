"""Simple LRU cache with TTL for query results."""
import time
import hashlib
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, max_entries: int = 1000, ttl_seconds: float = 60):
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl_seconds

    def _make_key(self, *args) -> str:
        raw = "|".join(str(a) for a in args)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, *key_parts) -> Any | None:
        key = self._make_key(*key_parts)
        if key not in self._cache:
            return None
        ts, value = self._cache[key]
        if time.time() - ts > self._ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, value: Any, *key_parts):
        key = self._make_key(*key_parts)
        self._cache[key] = (time.time(), value)
        self._cache.move_to_end(key)
        if len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def invalidate(self, *key_parts):
        key = self._make_key(*key_parts)
        self._cache.pop(key, None)

    def clear(self):
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)
