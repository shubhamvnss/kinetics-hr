from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.config import settings
from app.core.database import get_connection


def new_execution_id() -> str:
    return f'FIX-{uuid.uuid4().hex[:8].upper()}'


def _schema() -> str:
    return (settings.DB_SCHEMA or 'public').replace('"', '""')


def _ensure_tables() -> None:
    conn = get_connection()
    try:
        schema = _schema()
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'''CREATE TABLE IF NOT EXISTS "{schema}".ai_fix_audit (
                execution_id TEXT PRIMARY KEY,
                root_cause_id TEXT NOT NULL,
                run_id INTEGER,
                model TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                response JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                error_message TEXT,
                impacted_case_count INTEGER,
                representative_case_id TEXT
            )''')
    finally:
        conn.close()


def start(conn: Any, execution_id: str, root_cause: dict[str, Any], run_id: int | None, model: str) -> None:
    _ensure_tables()
    with conn.cursor() as cur:
        cur.execute(
            f'''INSERT INTO "{_schema()}".ai_fix_audit (execution_id, root_cause_id, run_id, model, status) VALUES (%s, %s, %s, %s, 'running') ON CONFLICT (execution_id) DO NOTHING''',
            (execution_id, root_cause.get('root_cause_id') or root_cause.get('id'), run_id, model),
        )


def complete(conn: Any, execution_id: str, status: str, impacted: int, representative_case_id: str | None, response: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f'''UPDATE "{_schema()}".ai_fix_audit SET status = %s, impacted_case_count = %s, representative_case_id = %s, response = %s::jsonb, completed_at = NOW() WHERE execution_id = %s''',
            (status, impacted, representative_case_id, json.dumps(response), execution_id),
        )


def fail(conn: Any, execution_id: str, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f'''UPDATE "{_schema()}".ai_fix_audit SET status = 'failed', error_message = %s, completed_at = NOW() WHERE execution_id = %s''',
            (error[:2000], execution_id),
        )


def save_agents(conn: Any, execution_id: str, agents: list[dict[str, Any]]) -> None:
    return None


def save_evidence(conn: Any, execution_id: str, case_id: str, evidence: list[dict[str, Any]]) -> None:
    return None


def latest_completed(conn: Any, root_cause_id: str, run_id: int | None = None) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        sql = f'''SELECT * FROM "{_schema()}".ai_fix_audit WHERE root_cause_id = %s AND status IN ('completed', 'no_impact') ORDER BY created_at DESC LIMIT 1'''
        params: list[Any] = [root_cause_id]
        if run_id is not None:
            sql = f'''SELECT * FROM "{_schema()}".ai_fix_audit WHERE root_cause_id = %s AND run_id = %s AND status IN ('completed', 'no_impact') ORDER BY created_at DESC LIMIT 1'''
            params = [root_cause_id, run_id]
        cur.execute(sql, params)
        row = cur.fetchone()
    return dict(row) if row else None


def history(conn: Any, root_cause_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(f'''SELECT * FROM "{_schema()}".ai_fix_audit WHERE root_cause_id = %s ORDER BY created_at DESC LIMIT %s''', (root_cause_id, limit))
        return [dict(row) for row in cur.fetchall()]
