import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from time import monotonic

class AsyncTTLCache[T]:
    """Process-local async TTL/LRU cache with bounded single-flight loading."""

    def __init__(self, *, ttl_seconds: float = 15.0, max_entries: int = 256):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[T]] = {}
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
            self._store_locked(key, value)

    def _store_locked(self, key: str, value: T) -> None:
        self._items[key] = (monotonic() + self._ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max_entries:
            self._items.popitem(last=False)

    async def get_or_create(self, key: str, loader: Callable[[], Awaitable[T]]) -> T:
        """Return a cached value or share one shielded loader task per key."""
        async with self._lock:
            item = self._items.get(key)
            if item is not None:
                expires_at, value = item
                if expires_at > monotonic():
                    self._items.move_to_end(key)
                    return value
                del self._items[key]

            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._load_and_store(key, loader))
                self._inflight[key] = task

        # One cancelled request must not cancel work shared by other waiters.
        return await asyncio.shield(task)

    async def _load_and_store(self, key: str, loader: Callable[[], Awaitable[T]]) -> T:
        task = asyncio.current_task()
        try:
            value = await loader()
            async with self._lock:
                self._store_locked(key, value)
            return value
        finally:
            async with self._lock:
                if self._inflight.get(key) is task:
                    del self._inflight[key]

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()
