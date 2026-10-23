from __future__ import annotations

from typing import Any

from app.core.database import get_connection
from app.services.root_cause_service import selectors

SELECTION_VERSION = 'issue_match_v2'
WEIGHTS = {
    'issue_match_score': 5.0,
    'matched_events': 2.5,
    'matched_waiting': 2.0,
    'matched_rework': 1.5,
    'selector_coverage': 1.0,
    'sla_breached': 1.0,
    'exceptions': 0.5,
    'evidence_volume': 0.5,
    'atypicality': -1.5,
}
MAX_KEY_EVENTS = 12


def _median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def score_candidates(features: list[dict[str, Any]], top_n: int = 5) -> list[dict[str, Any]]:
    if not features:
        return []

    def norm(key: str):
        top = max((float(f.get(key) or 0) for f in features), default=1.0) or 1.0
        return lambda f: (float(f.get(key) or 0) / top) if top else 0.0

    issue_norm = norm('issue_match_score')
    event_norm = norm('matched_events')
    wait_norm = norm('matched_waiting_minutes')
    rework_norm = norm('matched_rework_events')
    exc_norm = norm('matched_exception_events')
    volume_norm = norm('event_count')
    max_kinds = max((int(f.get('selector_kinds') or 0) for f in features), default=1) or 1
    median_cycle = _median([float(f.get('total_cycle_time_days')) for f in features if f.get('total_cycle_time_days') is not None]) or 0.0
    ranked = []
    for f in features:
        cycle = f.get('total_cycle_time_days')
        atypicality = 0.0
        if median_cycle and cycle is not None:
            atypicality = min(abs(float(cycle) - median_cycle) / median_cycle, 2.0)
        components = {
            'issue_match_score': issue_norm(f),
            'matched_events': event_norm(f),
            'matched_waiting': wait_norm(f),
            'matched_rework': rework_norm(f),
            'selector_coverage': (float(f.get('selector_kinds') or 0) / max_kinds),
            'sla_breached': 1.0 if f.get('sla_breached') == 'Y' else 0.0,
            'exceptions': exc_norm(f),
            'evidence_volume': volume_norm(f),
            'atypicality': atypicality,
        }
        score = sum(WEIGHTS[k] * v for k, v in components.items())
        ranked.append({**f, 'selection_score': round(score, 3), 'score_components': {k: round(v, 3) for k, v in components.items()}})
    ranked.sort(key=lambda r: (-r['selection_score'], str(r.get('case_id') or '')))
    return ranked[:top_n]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get('name') or item.get('description') or item.get('text')
        text = str(item or '').strip()
        if text and text not in result:
            result.append(text)
    return result


def _root_cause_text(root_cause: dict[str, Any]) -> str:
    parts: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple, set)):
            for nested in value:
                visit(nested)
        elif value is not None:
            parts.append(str(value))

    visit(root_cause)
    return ' '.join(parts).lower()


def _add_unique(values: list[str], *items: str) -> None:
    for item in items:
        if item and item not in values:
            values.append(item)


def _issue_profile(root_cause: dict[str, Any]) -> dict[str, Any]:
    """Turn a root-cause description into event-log selectors when explicit selectors are absent."""
    explicit = selectors(root_cause)
    profile = {
        'step_ids': _as_list(explicit.get('step_ids')),
        'rework_loop_types': _as_list(explicit.get('rework_loop_types')),
        'gap_references': _as_list(explicit.get('gap_references')),
        'stages': _as_list(explicit.get('stages')),
        'activity_patterns': [],
        'match_rework': False,
        'match_exception': False,
    }
    text = _root_cause_text(root_cause)

    stage_terms = (
        (('credit review', 'credit analysis'), '2. Credit Review & Analysis'),
        (('monitoring', 'approval stage'), '3. Monitoring & Approval'),
        (('intake', 'customer document', 'early-stage document'), '1. Intake & Screening'),
        (('operations', 'booking setup', 'loan setup'), '4. Operations Execution'),
        (('post-disbursement', 'status control', 'indicator'), '5. Post-Disbursement & Updates'),
    )
    for terms, stage in stage_terms:
        if any(term in text for term in terms):
            _add_unique(profile['stages'], stage)

    loop_terms = (
        (('rm information', 'information request', 'incomplete submission', 'reconstruction'), 'RM information loop'),
        (('customer document', 'document collection', 'document loop'), 'Customer document loop'),
        (('financial spreading', 'spreading error'), 'Spreading rework'),
        (('conditional approval', 'condition reassessment', 'conditions not cleared'), 'Conditional approval rework'),
        (('collateral',), 'Collateral rework'),
        (('guarantor', 'duplicate records'), 'Guarantor dedup'),
        (('allocation', 'workload imbalance', 'case reassignment', 'queue time'), 'Allocation loop'),
        (('contract correction', 'contract errors'), 'Contract correction loop'),
        (('booking discrepancy', 'booking setup'), 'Booking correction loop'),
        (('ops clarification', 'clarification'), 'Ops clarification loop'),
        (('status correction', 'status update'), 'Status correction'),
        (('indicator reset', 'balance limit'), 'Indicator reset'),
    )
    for terms, loop in loop_terms:
        if any(term in text for term in terms):
            _add_unique(profile['rework_loop_types'], loop)

    activity_terms = (
        (('approval',), '%approval%'),
        (('legal', 'contract'), '%legal%'),
        (('document', 'submission', 'information'), '%document%'),
        (('information', 'clarification'), '%information%'),
        (('collateral',), '%collateral%'),
        (('guarantor',), '%guarantor%'),
        (('spreading',), '%spreading%'),
        (('allocation', 'workload', 'reassignment', 'queue'), '%assign%'),
        (('booking',), '%booking%'),
        (('status',), '%status%'),
    )
    for terms, pattern in activity_terms:
        if any(term in text for term in terms):
            _add_unique(profile['activity_patterns'], pattern)

    profile['match_rework'] = bool(profile['rework_loop_types']) or any(
        term in text for term in ('rework', 'correction loop', 'repeated correction', 'repeated loops')
    )
    profile['match_exception'] = any(term in text for term in ('exception', 'control gap', 'control exception'))
    return profile


def _event_score_sql(event_alias: str = 'e', params_alias: str = 'p') -> str:
    return f'''(
        CASE WHEN {event_alias}.rework_loop_type = ANY({params_alias}.rework_loop_types) THEN 3 ELSE 0 END +
        CASE WHEN {event_alias}.step_id = ANY({params_alias}.step_ids) THEN 2 ELSE 0 END +
        CASE WHEN string_to_array(COALESCE({event_alias}.gap_reference, ''), ';') && {params_alias}.gap_references THEN 1 ELSE 0 END +
        CASE WHEN {event_alias}.stage = ANY({params_alias}.stages) THEN 1 ELSE 0 END +
        CASE WHEN lower(COALESCE({event_alias}.activity_name, '')) LIKE ANY({params_alias}.activity_patterns) THEN 2 ELSE 0 END +
        CASE WHEN {params_alias}.match_rework AND {event_alias}.rework_flag = 'Y' THEN 1 ELSE 0 END +
        CASE WHEN {params_alias}.match_exception AND {event_alias}.exception_flag = 'Y' THEN 1 ELSE 0 END
    )::numeric'''


def _params_sql() -> str:
    return '''
        SELECT %s::text[] AS step_ids, %s::text[] AS rework_loop_types,
               %s::text[] AS gap_references, %s::text[] AS stages,
               %s::text[] AS activity_patterns, %s::boolean AS match_rework,
               %s::boolean AS match_exception
    '''


def _profile_params(profile: dict[str, Any]) -> tuple[Any, ...]:
    return (
        profile.get('step_ids') or [],
        profile.get('rework_loop_types') or [],
        profile.get('gap_references') or [],
        profile.get('stages') or [],
        profile.get('activity_patterns') or [],
        bool(profile.get('match_rework')),
        bool(profile.get('match_exception')),
    )


def _candidate_features(root_cause: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    profile = _issue_profile(root_cause)
    conn = get_connection()
    try:
        from app.core.config import settings
        schema = settings.DB_SCHEMA.replace('"', '""')
        score_sql = _event_score_sql()
        sql = f'''
            WITH params AS ({_params_sql()}),
            scored_events AS (
                SELECT e.*, {score_sql} AS issue_match_score
                FROM "{schema}".event_log e
                CROSS JOIN params p
            ),
            matched AS (
                SELECT process_instance_id,
                       SUM(issue_match_score) AS issue_match_score,
                       COUNT(*) AS matched_events,
                       SUM(COALESCE(waiting_time_minutes, 0)) AS matched_waiting_minutes,
                       COUNT(*) FILTER (WHERE rework_flag = 'Y') AS matched_rework_events,
                       COUNT(*) FILTER (WHERE exception_flag = 'Y') AS matched_exception_events,
                       COUNT(*) FILTER (WHERE manual_touch_flag = 'Y') AS matched_manual_events,
                       COUNT(DISTINCT CASE WHEN rework_loop_type = ANY(p.rework_loop_types) THEN 'loop'
                                           WHEN step_id = ANY(p.step_ids) THEN 'step'
                                           WHEN string_to_array(COALESCE(gap_reference, ''), ';') && p.gap_references THEN 'gap'
                                           WHEN stage = ANY(p.stages) THEN 'stage'
                                           WHEN lower(COALESCE(activity_name, '')) LIKE ANY(p.activity_patterns) THEN 'activity'
                                           WHEN p.match_rework AND rework_flag = 'Y' THEN 'rework'
                                           WHEN p.match_exception AND exception_flag = 'Y' THEN 'exception'
                                      END) AS selector_kinds
                FROM scored_events
                CROSS JOIN params p
                WHERE issue_match_score > 0
                GROUP BY process_instance_id
            )
            SELECT m.process_instance_id AS case_id, m.issue_match_score, m.matched_events,
                   m.matched_waiting_minutes, m.matched_rework_events,
                   m.matched_exception_events, m.matched_manual_events, m.selector_kinds,
                   c.application_id, c.customer_name, c.requested_amount, c.current_stage,
                   c.current_status, c.total_cycle_time_days, c.sla_target_days, c.sla_breached,
                   c.event_count, c.rework_loops_count, c.handoff_count, c.manual_touch_count,
                   c.systems_touched_count
            FROM matched m
            JOIN "{schema}".case_master c ON c.process_instance_id = m.process_instance_id
            WHERE c.final_outcome = 'Disbursed'
            ORDER BY m.issue_match_score DESC, m.matched_waiting_minutes DESC,
                     m.matched_events DESC
            LIMIT %s;
        '''
        with conn.cursor() as cur:
            cur.execute(sql, (*_profile_params(profile), limit))
            rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _key_events(case_id: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        from app.core.config import settings
        schema = settings.DB_SCHEMA.replace('"', '""')
        score_sql = _event_score_sql()
        with conn.cursor() as cur:
            cur.execute(f'''
                WITH params AS ({_params_sql()}),
                event_rows AS (
                    SELECT e.step_id, e.activity_name, e.stage, e.owner_team, e.timestamp_start,
                           e.duration_minutes, e.waiting_time_minutes, e.rework_flag, e.exception_flag,
                           e.exception_reason, e.manual_touch_flag, e.handoff_flag,
                           {score_sql} AS issue_match_score,
                           ROW_NUMBER() OVER (ORDER BY e.timestamp_start) AS rn,
                           COUNT(*) OVER () AS total
                    FROM "{schema}".event_log e
                    CROSS JOIN params p
                    WHERE e.process_instance_id = %s
                )
                SELECT * FROM event_rows ORDER BY timestamp_start;
            ''', (*_profile_params(profile), case_id))
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return []
        keep = [r for r in rows if float(r.get('issue_match_score') or 0) > 0 or r['rn'] == 1 or r['rn'] == r['total']]
        if len(keep) > MAX_KEY_EVENTS:
            keep = keep[:MAX_KEY_EVENTS - 1] + [keep[-1]]
        out: list[dict[str, Any]] = []
        for i, r in enumerate(keep, start=1):
            out.append({
                'sequence': i,
                'step_id': r.get('step_id'),
                'activity': r.get('activity_name') or '',
                'stage': r.get('stage'),
                'owner_team': r.get('owner_team'),
                'timestamp': r.get('timestamp_start'),
                'duration_minutes': r.get('duration_minutes'),
                'waiting_minutes': r.get('waiting_time_minutes'),
                'is_rework': r.get('rework_flag') == 'Y',
                'is_exception': r.get('exception_flag') == 'Y',
                'is_manual': r.get('manual_touch_flag') == 'Y',
                'is_handoff': r.get('handoff_flag') == 'Y',
                'matched_root_cause': float(r.get('issue_match_score') or 0) > 0,
            })
        return out
    finally:
        conn.close()


def _case_evidence(case_id: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        from app.core.config import settings
        schema = settings.DB_SCHEMA.replace('"', '""')
        score_sql = _event_score_sql()
        with conn.cursor() as cur:
            cur.execute(f'''
                WITH params AS ({_params_sql()}),
                matched_events AS (
                    SELECT e.*, {score_sql} AS issue_match_score
                    FROM "{schema}".event_log e
                    CROSS JOIN params p
                    WHERE e.process_instance_id = %s
                )
                SELECT activity_name, stage, rework_loop_type, exception_reason,
                       COUNT(*) AS events,
                       SUM(COALESCE(waiting_time_minutes, 0)) / 480.0 AS waiting_days,
                       COUNT(*) FILTER (WHERE rework_flag = 'Y') AS rework_events,
                       COUNT(*) FILTER (WHERE exception_flag = 'Y') AS exception_events,
                       COUNT(*) FILTER (WHERE manual_touch_flag = 'Y') AS manual_events
                FROM matched_events
                WHERE issue_match_score > 0
                GROUP BY 1, 2, 3, 4
                ORDER BY waiting_days DESC NULLS LAST, events DESC
                LIMIT 8;
            ''', (*_profile_params(profile), case_id))
            rows = [dict(r) for r in cur.fetchall()]
        evidence: list[dict[str, Any]] = []
        for r in rows:
            bits = [f"{r['events']} event(s)"]
            if r.get('rework_events'):
                bits.append(f"{r['rework_events']} rework")
            if r.get('exception_events'):
                bits.append(f"{r['exception_events']} exception")
            if r.get('manual_events'):
                bits.append(f"{r['manual_events']} manual")
            if r.get('waiting_days'):
                bits.append(f"{round(r['waiting_days'], 2)} days waiting")
            etype = (
                'rework' if r.get('rework_events') else
                'exception' if r.get('exception_events') else
                'waiting' if r.get('waiting_days') else 'activity'
            )
            text = f"{r.get('activity_name')} ({r.get('stage')}): " + ", ".join(bits)
            if r.get('rework_loop_type'):
                text += f" - {r['rework_loop_type']}"
            evidence.append({
                'evidence_type': etype,
                'evidence_text': text,
                'evidence_score': r.get('waiting_days') or r.get('events'),
            })
        return evidence
    finally:
        conn.close()


def select_representative_case(root_cause: dict[str, Any]) -> dict[str, Any] | None:
    profile = _issue_profile(root_cause)
    features = _candidate_features(root_cause)
    ranked = score_candidates(features, top_n=5)
    if not ranked:
        return None

    best = ranked[0]
    case_id = best.get('case_id')
    cycle = best.get('total_cycle_time_days')
    sla = best.get('sla_target_days')
    case = {
        'case_id': case_id,
        'application_id': best.get('application_id'),
        'customer_name': best.get('customer_name'),
        'selection_version': SELECTION_VERSION,
        'selection_score': best.get('selection_score'),
        'selection_score_components': best.get('score_components'),
        'issue_match_score': best.get('issue_match_score'),
        'process_stage': best.get('current_stage'),
        'current_status': best.get('current_status'),
        'cycle_time': cycle,
        'sla': sla,
        'sla_breach': round(cycle - sla, 1) if cycle is not None and sla is not None else None,
        'rework_loops': best.get('rework_loops_count'),
        'handoffs': best.get('handoff_count'),
        'manual_touches': best.get('manual_touch_count'),
        'systems_touched': best.get('systems_touched_count'),
        'requested_amount': best.get('requested_amount'),
        'matched_events': best.get('matched_events'),
        'matched_waiting_minutes': best.get('matched_waiting_minutes'),
        'matched_rework_events': best.get('matched_rework_events'),
        'matched_exception_events': best.get('matched_exception_events'),
        'selector_kinds': best.get('selector_kinds'),
        'event_count': best.get('event_count'),
        'sla_breached': best.get('sla_breached'),
        'total_cycle_time_days': cycle,
        'key_events': _key_events(case_id, profile),
        'evidence': _case_evidence(case_id, profile),
        'reason_selected': 'Selected using root-cause-specific event-log matches, waiting, rework, exception, SLA, and cycle-time evidence.',
    }
    return case

