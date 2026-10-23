from fastapi import APIRouter

from app.services.tier1_health_service import (
    Tier1HealthService
)

router = APIRouter()


@router.get(
    "/tier1-health",
    tags=["Tier1 Health"]
)
async def get_tier1_health():

    return Tier1HealthService.get_tier1_health()