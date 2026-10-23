from __future__ import annotations

from typing import Any


def _numeric(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    parsed = _numeric(value, default)
    return int(parsed) if parsed is not None else default


def calculate_impact_from_root_cause(root_cause: dict[str, Any] | None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    root_cause = root_cause or {}
    context = context or {}
    affected = root_cause.get('affected_cases') or context.get('impacted_cases') or {}
    total_cases_raw = root_cause.get('total_cases') or context.get('total_cases') or context.get('cases_total')
    if isinstance(affected, dict):
        impacted_cases = _int(affected.get('count'))
    else:
        impacted_cases = _int(affected)
    if isinstance(total_cases_raw, dict):
        total_cases = _int(total_cases_raw.get('total') or total_cases_raw.get('count'))
    else:
        total_cases = _int(total_cases_raw or context.get('cases_total'))
    if total_cases <= 0 and impacted_cases > 0:
        total_cases = impacted_cases
    pct = root_cause.get('impact_percentage') or context.get('impact_percentage')
    if pct is None and total_cases:
        pct = round((impacted_cases / total_cases) * 100, 1)
    return {
        'impacted_cases': impacted_cases,
        'total_cases': total_cases,
        'impact_percentage': pct,
        'average_cycle_time': _numeric(root_cause.get('average_cycle_time') or context.get('average_cycle_time')),
        'target_cycle_time': _numeric(root_cause.get('target_cycle_time') or context.get('target_cycle_time')),
        'average_sla_breach': _numeric(root_cause.get('average_sla_breach') or context.get('average_sla_breach')),
        'sla_breach_rate': _numeric(root_cause.get('sla_breach_rate') or context.get('sla_breach_rate')),
        'rework_rate': _numeric(root_cause.get('rework_rate') or context.get('rework_rate')),
        'average_handoffs': _numeric(root_cause.get('average_handoffs') or context.get('average_handoffs')),
        'average_manual_touches': _numeric(root_cause.get('average_manual_touches') or context.get('average_manual_touches')),
        'average_waiting_days': _numeric(root_cause.get('average_waiting_days') or context.get('average_waiting_days')),
        'additional_cycle_time_vs_unimpacted': _numeric(root_cause.get('additional_cycle_time_vs_unimpacted') or context.get('additional_cycle_time_vs_unimpacted')),
        'metrics': [{
            'label': item.get('metric', 'Diagnostic'),
            'value': _numeric(item.get('value')),
            'unit': item.get('unit', ''),
            'detail': item.get('detail', ''),
        } for item in (root_cause.get('impact_statistics') or context.get('impact_statistics') or []) if isinstance(item, dict)],
    }
