from fastapi import APIRouter

from app.api.v1.endpoints import tier1_health
from app.api.v1.endpoints import deployment_packages
from app.api.v1.endpoints import fix_with_ai
from app.api.v1.endpoints import to_be_transformation


api_router = APIRouter()

api_router.include_router(
    tier1_health.router,
    prefix="/kpi"
)


from app.api.v1.endpoints import (
    root_cause_analysis
)

api_router.include_router(
    root_cause_analysis.router,
    prefix="/analysis"
)

api_router.include_router(
    deployment_packages.router,
    prefix="/agents",
)

api_router.include_router(fix_with_ai.router, prefix="/analysis")
api_router.include_router(to_be_transformation.router, prefix="/analysis")
