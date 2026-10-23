from pydantic import BaseModel
from typing import List


class KPIItem(BaseModel):
    kpi_id: str
    kpi_name: str
    current_value: float | None
    target: float | None
    unit: str | None
    variance_vs_target_pct: float | None
    health: str | None
    trend_direction: str | None
    previous_value: float | None


class RunStats(BaseModel):
    run_id: int
    as_of_timestamp: str
    cases_total: int
    cases_disbursed: int


class Tier1HealthResponse(BaseModel):
    tier1_all: List[KPIItem]
    run_stats: RunStats