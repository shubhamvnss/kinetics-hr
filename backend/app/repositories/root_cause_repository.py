from app.core.config import settings
from app.core.database import get_connection
from app.core.logging import get_logger


logger = get_logger(__name__)


class RootCauseRepository:

    @staticmethod
    def get_latest_analysis():
        conn = get_connection()

        try:
            logger.info("Fetching latest root cause analysis")

            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        analysis_id,
                        analysis_title,
                        payload,
                        created_at
                    FROM {settings.DB_SCHEMA}.corporate_loan_root_causes
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )

                result = cur.fetchone()

                if not result:
                    logger.warning("No analysis record found")

                return result

        finally:
            conn.close()