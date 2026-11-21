from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.settings import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


def database_enabled() -> bool:
    return bool(settings.database_url)


def get_session_factory() -> async_sessionmaker | None:
    global _engine, _session_factory
    if not settings.database_url:
        return None
    if _session_factory is None:
        url = settings.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        _engine = create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _session_factory


async def close_database() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
