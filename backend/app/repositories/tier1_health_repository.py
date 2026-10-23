from app.core.config import settings
from app.core.database import get_connection
from app.core.logging import get_logger


logger = get_logger(__name__)


class Tier1HealthRepository:

    @staticmethod
    def get_latest_run_id():
        conn = get_connection()

        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT MAX(run_id) AS run_id
                    FROM {settings.DB_SCHEMA}.kpi_tier1_health
                    """
                )

                result = cur.fetchone()
                return result["run_id"] if result else None

        finally:
            conn.close()

    @staticmethod
    def get_kpi_data(run_id: int):
        logger.info("Fetching KPIs for run_id=%s", run_id)

        conn = get_connection()

        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        kpi_id,
                        kpi_name,
                        current_value,
                        target,
                        unit,
                        variance_vs_target_pct,
                        health,
                        trend_direction,
                        previous_value
                    FROM {settings.DB_SCHEMA}.kpi_tier1_health
                    WHERE run_id = %s
                    ORDER BY kpi_id
                    """,
                    (run_id,),
                )

                return cur.fetchall()

        finally:
            conn.close()

    @staticmethod
    def get_run_stats(run_id: int):
        conn = get_connection()

        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        run_id,
                        as_of_timestamp,
                        requisitions_total,
                        requisitions_filled
                    FROM {settings.DB_SCHEMA}.kpi_run
                    WHERE run_id = %s
                    """,
                    (run_id,),
                )

                return cur.fetchone() or {}

        finally:
            conn.close()