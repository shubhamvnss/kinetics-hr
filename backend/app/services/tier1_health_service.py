from app.core.logging import get_logger
from app.repositories.tier1_health_repository import Tier1HealthRepository


logger = get_logger(__name__)


class Tier1HealthService:

    @staticmethod
    def get_tier1_health():
        logger.info("Fetching latest Tier 1 KPI health data")

        run_id = Tier1HealthRepository.get_latest_run_id()

        if run_id is None:
            logger.warning("No KPI run data found")
            return {
                "tier1_all": [],
                "run_stats": {},
            }

        kpis = Tier1HealthRepository.get_kpi_data(run_id)
        run_stats = Tier1HealthRepository.get_run_stats(run_id)

        return {
            "tier1_all": kpis,
            "run_stats": run_stats,
        }