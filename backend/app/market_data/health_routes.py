from fastapi import APIRouter, Request

from app.auth import current_user
from app.market_data import futures_stream, health, open_interest
from app.settings import settings


router = APIRouter(prefix="/api/v1/health", tags=["system"])


@router.get("/market-data")
def market_data_health(request: Request) -> dict:
    current_user(request)
    return health.build_market_data_health(
        settings.futures_market_data_provider,
        futures_stream._streams,
        open_interest._collectors,
    )
