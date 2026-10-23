import psycopg2
from psycopg2.extras import RealDictCursor

from app.core.config import settings


def get_connection():
    return psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        sslmode=settings.DB_SSLMODE,
        connect_timeout=settings.DB_CONNECT_TIMEOUT,
        cursor_factory=RealDictCursor,
    )
