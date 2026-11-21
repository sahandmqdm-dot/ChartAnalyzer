import pytest

from app.market_data.cache import AsyncTTLCache


@pytest.mark.asyncio
async def test_cache_returns_value_before_expiration():
    cache = AsyncTTLCache[str](ttl_seconds=10)
    await cache.set("key", "value")
    assert await cache.get("key") == "value"


@pytest.mark.asyncio
async def test_cache_expires_values(monkeypatch):
    import app.market_data.cache as cache_module

    ticks = iter([10.0, 21.0])
    monkeypatch.setattr(cache_module, "monotonic", lambda: next(ticks))
    cache = AsyncTTLCache[str](ttl_seconds=5)
    await cache.set("key", "value")
    assert await cache.get("key") is None


@pytest.mark.asyncio
async def test_cache_evicts_least_recently_used_entry():
    cache = AsyncTTLCache[str](ttl_seconds=10, max_entries=2)
    await cache.set("a", "A")
    await cache.set("b", "B")
    assert await cache.get("a") == "A"
    await cache.set("c", "C")
    assert await cache.get("b") is None
    assert await cache.get("a") == "A"
    assert await cache.get("c") == "C"


@pytest.mark.asyncio
async def test_cache_clear_removes_all_entries():
    cache = AsyncTTLCache[str]()
    await cache.set("key", "value")
    await cache.clear()
    assert await cache.get("key") is None
