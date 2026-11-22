from fastapi import APIRouter, Request

from app.auth import current_user
from app.market_data.futures_stream import _streams
from app.market_data.health import build_market_data_health
from app.market_data.open_interest import _collectors
from app.settings import settings


router = APIRouter(prefix="/api/v1/health", tags=["system"])


@router.get("/market-data")
def market_data_health(request: Request) -> dict:
    current_user(request)
    return build_market_data_health(
        settings.futures_market_data_provider,
        _streams,
        _collectors,
    )
