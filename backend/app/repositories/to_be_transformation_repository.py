from __future__ import annotations

import json
import threading
from typing import Any

from app.core.config import settings
from app.core.database import get_connection


_ready = False
_ready_lock = threading.Lock()


def _table_name() -> str:
    schema = settings.DB_SCHEMA.replace('"', '""')
    return f'"{schema}"."to_be_transformation_jobs"'


def ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if _ready:
            return
        schema = settings.DB_SCHEMA.replace('"', '""')
        table = _table_name()
        conn = get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                cursor.execute(
                    f'''CREATE TABLE IF NOT EXISTS {table} (
                        root_cause_id TEXT PRIMARY KEY,
                        case_id TEXT,
                        status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
                        result JSONB,
                        error_code TEXT,
                        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        finished_at TIMESTAMPTZ
                    )'''
                )
                cursor.execute(
                    f'''CREATE INDEX IF NOT EXISTS to_be_transformation_jobs_case_idx
                        ON {table} (case_id)'''
                )
            conn.commit()
            _ready = True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_job(root_cause_id: str) -> dict[str, Any] | None:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f'''SELECT root_cause_id, case_id, status, result, error_code,
                           started_at, updated_at, finished_at
                    FROM {_table_name()}
                    WHERE root_cause_id = %s''',
                (root_cause_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def claim_job(root_cause_id: str, retry_failed: bool = False) -> bool:
    """Atomically start a job, reclaiming only explicit failures or stale work."""
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f'''INSERT INTO {_table_name()} AS current_job (root_cause_id, status)
                    VALUES (%s, 'running')
                    ON CONFLICT (root_cause_id) DO UPDATE SET
                        case_id = NULL,
                        status = 'running',
                        result = NULL,
                        error_code = NULL,
                        started_at = NOW(),
                        updated_at = NOW(),
                        finished_at = NULL
                    WHERE (current_job.status = 'failed' AND %s)
                       OR (current_job.status = 'running'
                           AND current_job.updated_at < NOW() - INTERVAL '30 minutes')
                    RETURNING root_cause_id''',
                (root_cause_id, retry_failed),
            )
            claimed = cursor.fetchone() is not None
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_case_id(root_cause_id: str, case_id: str) -> None:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f'''UPDATE {_table_name()}
                    SET case_id = %s, updated_at = NOW()
                    WHERE root_cause_id = %s AND status = 'running' ''',
                (case_id, root_cause_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_job(root_cause_id: str, case_id: str | None, result: dict[str, Any]) -> None:
    ensure_table()
    conn = get_connection()
    try:
        cache_version = settings.CACHE_VERSION
        payload = dict(result)
        payload.setdefault("cache_version", cache_version)
        with conn.cursor() as cursor:
            cursor.execute(
                f'''UPDATE {_table_name()}
                    SET case_id = COALESCE(%s, case_id), status = 'completed',
                        result = %s::jsonb, error_code = NULL,
                        updated_at = NOW(), finished_at = NOW()
                    WHERE root_cause_id = %s''',
                (case_id, json.dumps(payload, default=str), root_cause_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fail_job(root_cause_id: str, error_code: str) -> None:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f'''UPDATE {_table_name()}
                    SET status = 'failed', error_code = %s,
                        updated_at = NOW(), finished_at = NOW()
                    WHERE root_cause_id = %s''',
                (error_code[:100], root_cause_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()