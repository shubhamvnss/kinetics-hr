from __future__ import annotations

from typing import Any

DEFAULT_EVENT_SYSTEMS: list[str] = [
    "ICS BANKS",
    "Bankinfo",
    "Outlook",
    "Excel",
    "PMA",
    "Credit System",
    "BI System",
    "Core DB",
    "PowerCard",
    "Manual Register",
]


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, str):
        items = [value]
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def build_process_context(root_cause: dict[str, Any] | None = None, kpi: dict[str, Any] | None = None, tier2: list[dict[str, Any]] | None = None, gaps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    root_cause = root_cause or {}
    systems: set[str] = set()
    for item in root_cause.get("systems") or []:
        systems.update(_list(item))
    for gap in gaps or []:
        if isinstance(gap, dict):
            for key in ("system_name", "system", "systems"):
                if key in gap:
                    systems.update(_list(gap.get(key)))
    for item in tier2 or []:
        if isinstance(item, dict):
            systems.update(_list(item.get("systems")))

    if not systems:
        systems.update(DEFAULT_EVENT_SYSTEMS)

    allowed = sorted(systems)
    stages = _list(root_cause.get("stages"))
    return {
        "systems_in_scope": sorted(systems),
        "allowed_systems": allowed,
        "hotspot_activities": [
            {"step_id": step, "activity_name": step, "total_waiting_days": 0}
            for step in _list(root_cause.get("step_ids"))[:10]
        ],
        "manual_activities": [],
        "stage_profile": [{"stage": stage, "avg_waiting_days": 0, "health": "Unknown"} for stage in stages[:10]],
    }
