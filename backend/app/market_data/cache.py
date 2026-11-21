import asyncio
from collections import OrderedDict
from time import monotonic


class AsyncTTLCache[T]:
    """Small process-local async TTL cache with bounded LRU eviction."""

    def __init__(self, *, ttl_seconds: float = 15.0, max_entries: int = 256):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= monotonic():
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return value

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            self._items[key] = (monotonic() + self._ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()
