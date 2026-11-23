from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import SecretStr

from app.auth import current_user
from app.market_data.cache import AsyncTTLCache
from app.market_data.coinalyze import CoinalyzeClient, CoinalyzeError, normalize_symbols
from app.settings import settings

router = APIRouter(prefix="/api/v1/derivatives/coinalyze", tags=["derivatives"])
_snapshot_cache: AsyncTTLCache = AsyncTTLCache(ttl_seconds=10, max_entries=128)


@router.get("/snapshot")
async def coinalyze_snapshot(
    request: Request,
    symbols: str = Query(min_length=2, max_length=1400),
):
    """Return current Coinalyze futures open-interest and funding snapshots."""
    current_user(request)
    try:
        requested = normalize_symbols(symbols.split(","))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    configured_key: SecretStr | None = settings.coinalyze_api_key
    api_key = configured_key.get_secret_value() if configured_key else ""
    if not api_key.strip():
        raise HTTPException(status_code=503, detail="Coinalyze API key is not configured")

    cache_key = ",".join(requested)

    async def load():
        try:
            client = CoinalyzeClient(api_key)
            return await client.fetch_snapshot(requested)
        except CoinalyzeError as exc:
            raise HTTPException(
                status_code=502,
                detail="Coinalyze market-data provider unavailable or returned invalid data",
            ) from exc

    result = await _snapshot_cache.get_or_create(cache_key, load)
    return {
        "provider": "coinalyze",
        "market_type": "futures",
        "data": [item.model_dump(mode="json") for item in result],
    }
