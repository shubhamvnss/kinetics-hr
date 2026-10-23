import json
import re
import time
from typing import Any

from app.core.logging import get_logger
from app.repositories.root_cause_repository import RootCauseRepository

logger = get_logger(__name__)


def make_root_cause_id(tier1_kpi_id: str | None, rank: int | str | None) -> str:
    kpi = str(tier1_kpi_id or '').strip()
    if not kpi:
        raise ValueError('tier1_kpi_id is required to make a root cause id')
    return f'RC-{kpi}-{int(rank or 1)}'


def normalize_root_cause_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('_', '-')
    if text.upper().startswith('RC-'):
        return text
    if text.upper().startswith('T1-') and '-' in text:
        parts = text.split('-')
        if len(parts) >= 3 and parts[-1].isdigit():
            return f'RC-{parts[0]}-{parts[1]}-{parts[-1]}' if len(parts) == 3 else f'RC-{text}'
    if re.match(r'^[A-Z0-9-]+-\d+$', text, re.IGNORECASE):
        return f'RC-{text}'
    return text


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = value
    elif isinstance(value, str):
        items = [part.strip() for part in value.split(',') if part.strip()]
    else:
        items = [value]
    result: list[str] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, (list, tuple, set)):
            result.extend(_coerce_list(item))
            continue
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _parse_analysis_cause(cause: Any, kpi: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(cause, dict):
        cause = {'root_cause': str(cause)}
    rank = cause.get('rank', index + 1)
    kpi_id = str((kpi or {}).get('kpi_id') or (cause.get('tier1_kpi_id') or '')).strip()
    root_cause_id = normalize_root_cause_id(cause.get('root_cause_id') or f'{kpi_id}-{rank}') or make_root_cause_id(kpi_id or 'T1-01', rank)
    return {
        'root_cause_id': root_cause_id,
        'rank': int(rank) if str(rank).isdigit() else index + 1,
        'tier1_kpi_id': kpi_id,
        'tier1_kpi_name': (kpi or {}).get('kpi_name') or cause.get('tier1_kpi_name'),
        'tier2_kpi_ids': _coerce_list(cause.get('linked_tier2_kpis') or cause.get('tier2_kpi_ids') or cause.get('tier2_kpis')),
        'title': cause.get('root_cause') or cause.get('title') or 'Process issue',
        'description': cause.get('root_cause_summary') or cause.get('description') or cause.get('root_cause') or '',
        'category': cause.get('category') or cause.get('root_cause_type') or 'Process',
        'confidence': cause.get('confidence'),
        'gap_references': _coerce_list(cause.get('gap_references') or cause.get('gaps')),
        'step_ids': _coerce_list(cause.get('step_ids') or (cause.get('activity_evidence') or [])),
        'rework_loop_types': _coerce_list(cause.get('rework_loop_types') or cause.get('rework_evidence') or []),
        'stages': _coerce_list(cause.get('stages') or cause.get('stage_evidence') or []),
        'detail': cause.get('detail') or cause,
        'frontend': cause.get('frontend') or {},
    }


def extract_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(analysis, dict):
        return []
    front_summary = (analysis.get('frontend_summary') or {}).get('analysis_summary') or {}
    rows: list[dict[str, Any]] = []
    for block in analysis.get('tier1_root_causes') or []:
        kpi = (block or {}).get('tier1_kpi') or {}
        for index, cause in enumerate(block.get('root_causes') or []):
            row = _parse_analysis_cause(cause, kpi, index)
            if row.get('tier1_kpi_id') or row.get('root_cause_id'):
                rows.append(row)
    if not rows:
        for kpi in front_summary.get('critical_kpis') or []:
            if not isinstance(kpi, dict):
                continue
            for index, cause in enumerate(kpi.get('top_root_causes') or []):
                row = _parse_analysis_cause(cause, kpi, index)
                if row.get('root_cause_id'):
                    rows.append(row)
    return rows


def selectors(root_cause: dict[str, Any]) -> dict[str, list[str]]:
    return {
        'step_ids': _coerce_list(root_cause.get('step_ids') or root_cause.get('steps')),
        'rework_loop_types': _coerce_list(root_cause.get('rework_loop_types') or root_cause.get('rework_evidence')),
        'gap_references': _coerce_list(root_cause.get('gap_references') or root_cause.get('gaps')),
        'stages': _coerce_list(root_cause.get('stages') or root_cause.get('stage_evidence')),
    }


def has_evidence(root_cause: dict[str, Any]) -> bool:
    sel = selectors(root_cause)
    return any(sel.get(key) for key in ('step_ids', 'rework_loop_types', 'gap_references'))


def get_root_cause(conn: Any, root_cause_id: str, run_id: int | None = None) -> dict[str, Any] | None:
    if not root_cause_id:
        return None
    q = 'SELECT * FROM root_causes WHERE root_cause_id = %s'
    params: list[Any] = [root_cause_id]
    if run_id is not None:
        q += ' AND run_id = %s'
        params.append(run_id)
    q += ' ORDER BY run_id DESC LIMIT 1'
    try:
        with conn.cursor() as cur:
            cur.execute(q, params)
            row = cur.fetchone()
        if row is None:
            return None
    except Exception:
        return None
    out = dict(row) if row else {}
    for key in ('gap_references', 'step_ids', 'rework_loop_types', 'stages', 'tier2_kpi_ids'):
        if key in out:
            value = out.get(key)
            out[key] = _coerce_list(value) if not isinstance(value, list) else value
    if out.get('root_cause_id'):
        out['root_cause_id'] = normalize_root_cause_id(out['root_cause_id']) or out['root_cause_id']
    return out


class RootCauseService:
    @staticmethod
    def get_root_cause_analysis():
        start_time = time.time()
        try:
            logger.info('Root Cause Analysis Request Started')
            result = RootCauseRepository.get_latest_analysis()
            if not result:
                raise RuntimeError('No root cause analysis found')
            payload = result['payload']
            if isinstance(payload, str):
                payload = json.loads(payload)
            logger.info('Analysis Returned in %s sec', round(time.time() - start_time, 2))
            return payload
        except Exception:
            logger.exception('Service Error')
            raise
