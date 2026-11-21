import asyncio\n\nimport pytest

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


@pytest.mark.asyncio
async def test_cache_single_flight_loads_once_for_concurrent_callers():
    cache = AsyncTTLCache[str]()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "shared"

    first = asyncio.create_task(cache.get_or_create("key", loader))
    await started.wait()
    second = asyncio.create_task(cache.get_or_create("key", loader))
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(first, second) == ["shared", "shared"]
    assert calls == 1
    assert await cache.get("key") == "shared"


@pytest.mark.asyncio
async def test_cache_loader_failure_is_not_cached_and_can_retry():
    cache = AsyncTTLCache[str]()
    calls = 0

    async def failing_loader():
        nonlocal calls
        calls += 1
        raise RuntimeError("temporary failure")

    with pytest.raises(RuntimeError, match="temporary failure"):
        await cache.get_or_create("key", failing_loader)

    async def succeeding_loader():
        nonlocal calls
        calls += 1
        return "recovered"

    assert await cache.get_or_create("key", succeeding_loader) == "recovered"
    assert calls == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_loader():
    cache = AsyncTTLCache[str]()
    started = asyncio.Event()
    release = asyncio.Event()

    async def loader():
        started.set()
        await release.wait()
        return "completed"

    waiter = asyncio.create_task(cache.get_or_create("key", loader))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release.set()
    assert await cache.get_or_create("key", loader) == "completed"
