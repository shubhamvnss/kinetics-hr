from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.agents.recommendation_agent import (
    RecommendationError,
    build_fallback_plan,
    deterministic_agents,
    generate as generate_recommendation,
)
from app.agents.to_be_agent import (
    ToBeError,
    build_deterministic_fallback as build_to_be_fallback,
    generate as generate_to_be,
)
from app.core.azure_openai import AzureOpenAIError
from app.core.config import settings
from app.core.database import get_connection
from app.schemas.response import RecommendationLLM
from app.services.impact_service import calculate_impact_from_root_cause
from app.services.process_context import build_process_context
from app.services.representative_case import select_representative_case
from app.services.root_cause_service import (
    RootCauseService,
    extract_rows,
    has_evidence,
    normalize_root_cause_id,
)

logger = logging.getLogger(__name__)


class RootCauseNotFound(LookupError):
    pass


class InsufficientEvidence(ValueError):
    pass


class NoImpactedCases(ValueError):
    pass


class NoRepresentativeCase(ValueError):
    pass


class ToBeGenerationError(RuntimeError):
    pass


def _find_cause(payload: dict[str, Any], requested_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    target = normalize_root_cause_id(requested_id)
    for cause in extract_rows(payload):
        candidate = normalize_root_cause_id(cause.get("root_cause_id"))
        if candidate == target:
            kpi = _find_kpi(payload, cause.get("tier1_kpi_id"))
            return cause, kpi
    raise RootCauseNotFound(f"Root cause '{requested_id}' was not found in the latest analysis.")


def _find_kpi(payload: dict[str, Any], kpi_id: str | None) -> dict[str, Any]:
    if not kpi_id:
        return {}
    summary = (
        (payload.get("frontend_summary") or {}).get("analysis_summary")
        or payload.get("analysis_summary")
        or {}
    )
    for kpi in summary.get("critical_kpis") or []:
        if isinstance(kpi, dict) and str(kpi.get("kpi_id") or "") == kpi_id:
            return kpi
    for block in payload.get("tier1_root_causes") or []:
        if not isinstance(block, dict):
            continue
        kpi = block.get("tier1_kpi") or {}
        if str(kpi.get("kpi_id") or "") == kpi_id:
            return kpi
    return {"kpi_id": kpi_id}


def _event_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in {"Y", "YES", "TRUE", "1"}


def _load_case_activities(case_id: str, stages: list[str]) -> list[dict[str, Any]]:
    schema = settings.DB_SCHEMA.replace('"', '""')
    stage_clause = " AND stage = ANY(%s)" if stages else ""
    params: tuple[Any, ...] = (case_id, stages) if stages else (case_id,)
    query = f'''
         SELECT step_id, activity_name, stage, owner_team, system_used, source_system,
               duration_minutes, waiting_time_minutes, manual_touch_flag, rework_flag,
             exception_flag, handoff_flag, gap_reference, timestamp_start, timestamp_end, event_type
        FROM "{schema}".event_log
        WHERE process_instance_id = %s{stage_clause}
        ORDER BY timestamp_start NULLS LAST, event_id
    '''
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

    if not rows and stages:
        return _load_case_activities(case_id, [])

    activities = []
    for sequence, row in enumerate(rows, start=1):
        step_text = f"{row.get('step_id') or ''} {row.get('activity_name') or ''}".casefold()
        activities.append({
            "sequence": sequence,
            "step_id": str(row.get("step_id") or ""),
            "activity_name": str(row.get("activity_name") or ""),
            "stage": row.get("stage"),
            "owner_team": row.get("owner_team"),
            "duration_minutes": row.get("duration_minutes") or 0,
            "waiting_time_minutes": row.get("waiting_time_minutes") or 0,
            "manual_touch": _event_flag(row.get("manual_touch_flag")),
            "rework": _event_flag(row.get("rework_flag")),
            "exception": _event_flag(row.get("exception_flag")),
            "handoff": _event_flag(row.get("handoff_flag")),
            "approval_step": any(term in step_text for term in ("approval", "approve", "committee")),
            "system_used": _observed_systems_for_row(row),
            "gap_reference": row.get("gap_reference"),
            "timestamp_start": row.get("timestamp_start"),
            "timestamp_end": row.get("timestamp_end"),
            "event_type": row.get("event_type"),
        })
    return activities


def _observed_systems_for_row(row: dict[str, Any]) -> list[str]:
    systems: list[str] = []
    for field in ("system_used", "source_system"):
        value = row.get(field)
        values = value if isinstance(value, (list, tuple, set)) else str(value or "").split("/")
        for item in values:
            system = str(item or "").strip()
            if system and system.upper() != "N/A" and system not in systems:
                systems.append(system)
    return systems


def _case_systems(current_state: list[dict[str, Any]]) -> list[str]:
    return sorted({system for row in current_state for system in row["system_used"]})


def _target_stages(root_cause: dict[str, Any], case: dict[str, Any]) -> list[str]:
    stages = root_cause.get("stages") or []
    if stages:
        return [str(stage) for stage in stages if str(stage).strip()]
    stage = case.get("process_stage") or case.get("stage")
    return [str(stage)] if stage else []


def _section1(
    root_cause: dict[str, Any],
    kpi: dict[str, Any],
    tier2: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    case: dict[str, Any],
    agents: list[Any],
    target_stages: list[str],
) -> dict[str, Any]:
    return {
        "representative_case_id": case["case_id"],
        "stage_being_transformed": target_stages,
        "tier_1_kpi": {
            "kpi_id": kpi.get("kpi_id"),
            "kpi_name": kpi.get("kpi_name"),
            "current_value": kpi.get("current_value"),
            "target": kpi.get("target"),
            "unit": kpi.get("unit"),
            "variance_vs_target_pct": kpi.get("variance_vs_target_pct"),
            "health": kpi.get("health"),
        },
        "tier_2_kpis": tier2,
        "gap_references": [gap.get("gap_reference") for gap in gaps],
        "root_causes": [{
            "root_cause_id": root_cause.get("root_cause_id"),
            "title": root_cause.get("title") or "",
            "description": root_cause.get("description") or "",
            "confidence": root_cause.get("confidence"),
        }],
        "recommended_agents": [agent.name for agent in agents],
    }


def _to_be_rows(
    current_state: list[dict[str, Any]],
    to_be_plan: Any,
    name_to_agent_id: dict[str, str],
) -> list[dict[str, Any]]:
    by_sequence = {activity.current_sequence: activity for activity in to_be_plan.activities}
    rows = []
    for current in current_state:
        activity = by_sequence[current["sequence"]]
        rows.append({
            "to_be_sequence": activity.to_be_sequence,
            "current_sequence": current["sequence"],
            "current_step_id": current["step_id"],
            "current_activity": current["activity_name"],
            "to_be_activity": activity.to_be_activity,
            "change_type": activity.change_type,
            "responsible_agent": activity.responsible_agent,
            "responsible_agent_id": name_to_agent_id.get(activity.responsible_agent) if activity.responsible_agent else None,
            "deterministic_automation": activity.deterministic_automation,
            "human_in_the_loop": activity.human_in_the_loop,
            "system_used": activity.system_used,
            "trigger": activity.trigger,
            "action": activity.action,
            "expected_outcome": activity.expected_outcome,
            "reason_for_change": activity.reason_for_change,
            "kpi_gap_addressed": activity.kpi_gap_addressed,
        })
    return sorted(rows, key=lambda row: (row["to_be_sequence"], row["current_sequence"]))


def _actor(row: dict[str, Any]) -> str:
    if row["change_type"] == "AI Agent":
        return row.get("responsible_agent") or "AI Agent"
    if row["change_type"] == "Automated (Deterministic)":
        return "Deterministic Automation"
    return "Human"


def _section4(current_state: list[dict[str, Any]], to_be_rows: list[dict[str, Any]]) -> dict[str, Any]:
    current_by_sequence = {row["sequence"]: row for row in current_state}
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in to_be_rows:
        if row["change_type"] != "Eliminated":
            groups.setdefault(row["to_be_sequence"], []).append(row)
    ordered_sequences = sorted(groups)
    human_controlled = [
        {"to_be_sequence": sequence, "to_be_activity": groups[sequence][0]["to_be_activity"]}
        for sequence in ordered_sequences
        if any(row["change_type"] in ("Retained", "Human") for row in groups[sequence])
    ]
    eliminated = [
        {
            "current_sequence": row["current_sequence"],
            "current_activity": row["current_activity"],
            "reason_for_change": row["reason_for_change"],
        }
        for row in to_be_rows
        if row["change_type"] == "Eliminated"
    ]
    automated = [
        {"to_be_sequence": sequence, "to_be_activity": groups[sequence][0]["to_be_activity"]}
        for sequence in ordered_sequences
        if any(row["change_type"] == "Automated (Deterministic)" for row in groups[sequence])
    ]
    consolidated = [
        {
            "to_be_sequence": sequence,
            "to_be_activity": groups[sequence][0]["to_be_activity"],
            "current_activities": [row["current_activity"] for row in groups[sequence]],
        }
        for sequence in ordered_sequences
        if len(groups[sequence]) > 1
    ]
    actors = [_actor(groups[sequence][0]) for sequence in ordered_sequences]
    current_systems = sorted({system for row in current_state for system in row["system_used"]})
    to_be_systems = sorted({system for row in to_be_rows for system in row["system_used"]})
    comparison = {
        "number_of_activities": {"current": len(current_state), "to_be": len(ordered_sequences)},
        "manual_activities": {"current": sum(bool(row["manual_touch"]) for row in current_state), "to_be": len(human_controlled)},
        "ai_agent_activities": {"current": 0, "to_be": sum(any(row["change_type"] == "AI Agent" for row in groups[seq]) for seq in ordered_sequences)},
        "deterministic_automation_activities": {"current": 0, "to_be": sum(any(row["change_type"] == "Automated (Deterministic)" for row in groups[seq]) for seq in ordered_sequences)},
        "human_approval_activities": {"current": sum(bool(row["approval_step"]) for row in current_state), "to_be": sum(any(row["human_in_the_loop"] for row in groups[seq]) for seq in ordered_sequences)},
        "rework_activities": {"current": sum(bool(row["rework"]) for row in current_state), "to_be": sum(all(row["change_type"] == "Retained" for row in groups[seq]) and any(current_by_sequence[row["current_sequence"]]["rework"] for row in groups[seq]) for seq in ordered_sequences)},
        "waiting_activities": {"current": sum(float(row["waiting_time_minutes"] or 0) > 0 for row in current_state), "to_be": sum(all(row["change_type"] == "Retained" for row in groups[seq]) and any(float(current_by_sequence[row["current_sequence"]]["waiting_time_minutes"] or 0) > 0 for row in groups[seq]) for seq in ordered_sequences)},
        "handoffs": {"current": sum(bool(row["handoff"]) for row in current_state), "to_be": sum(first != second for first, second in zip(actors, actors[1:]))},
        "systems_touched": {"current": current_systems, "to_be": to_be_systems},
    }
    return {
        "comparison": comparison,
        "reduced_steps": len(current_state) - len(ordered_sequences),
        "activities_eliminated": eliminated,
        "activities_automated": automated,
        "activities_consolidated": consolidated,
        "activities_newly_introduced": [],
        "activities_remaining_human_controlled": human_controlled,
    }


def _section5(agents: list[Any], name_to_agent_id: dict[str, str]) -> list[dict[str, Any]]:
    ordered = sorted(agents, key=lambda agent: agent.sequence_order)
    workflow = []
    for index, agent in enumerate(ordered):
        workflow.append({
            "agent_id": name_to_agent_id.get(agent.name, agent.agent_id),
            "agent_name": agent.name,
            "sequence_order": agent.sequence_order,
            "trigger": agent.trigger,
            "input": "Systems/data: " + ", ".join(agent.systems) if agent.systems else "Case and KPI evidence",
            "action": agent.action,
            "systems_accessed": agent.systems,
            "output": agent.expected_outcome,
            "validation": agent.human_in_loop_detail if agent.human_in_loop else "Automated validation before system update",
            "human_in_the_loop": agent.human_in_loop,
            "human_in_the_loop_detail": agent.human_in_loop_detail,
            "next_step": ordered[index + 1].name if index + 1 < len(ordered) else "Process completion",
        })
    return workflow


def _section7(
    current_state: list[dict[str, Any]],
    to_be_rows: list[dict[str, Any]],
    root_cause: dict[str, Any],
    kpi: dict[str, Any],
    tier2: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_sequence = {row["current_sequence"]: row for row in to_be_rows}
    tier1_label = f"{kpi.get('kpi_id', '')} {kpi.get('kpi_name', '')}".strip() or None
    tier2_label = ", ".join(f"{item.get('kpi_id')} {item.get('kpi_name', '')}".strip() for item in tier2) or None
    return [
        {
            "current_activity": current["activity_name"],
            "problem_root_cause": root_cause.get("title") or root_cause.get("root_cause_id"),
            "tier_1_kpi": tier1_label,
            "tier_2_kpi": tier2_label,
            "gap": current.get("gap_reference"),
            "recommended_agent": by_sequence[current["sequence"]]["responsible_agent"],
            "to_be_activity": by_sequence[current["sequence"]]["to_be_activity"],
            "change_type": by_sequence[current["sequence"]]["change_type"],
        }
        for current in current_state
    ]


def _summary(section4: dict[str, Any], target_stages: list[str], case: dict[str, Any], model: str) -> dict[str, Any]:
    comparison = section4["comparison"]
    current_count = comparison["number_of_activities"]["current"]
    to_be_count = comparison["number_of_activities"]["to_be"]
    narrative = (
        f"{current_count} current-state activities in {', '.join(target_stages) if target_stages else 'this case'} "
        f"become {to_be_count} to-be activities ({section4['reduced_steps']} fewer steps): "
        f"{comparison['ai_agent_activities']['to_be']} agent-driven, "
        f"{comparison['deterministic_automation_activities']['to_be']} deterministically automated, "
        f"{len(section4['activities_eliminated'])} eliminated, "
        f"{len(section4['activities_consolidated'])} consolidated."
    )
    return {
        "case_id": case["case_id"],
        "stages_transformed": target_stages,
        "current_activity_count": current_count,
        "to_be_activity_count": to_be_count,
        "reduced_steps": section4["reduced_steps"],
        "narrative": narrative,
        "model": model,
    }


def run(
    root_cause_id: str,
    client: Any = None,
    on_case_selected: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create a To-Be transformation using real root-cause and event-log evidence."""
    payload = RootCauseService.get_root_cause_analysis()
    if isinstance(payload, str):
        payload = json.loads(payload)
    root_cause, kpi = _find_cause(payload, root_cause_id)
    canonical_id = normalize_root_cause_id(root_cause.get("root_cause_id")) or root_cause_id
    root_cause["root_cause_id"] = canonical_id
    if not has_evidence(root_cause):
        raise InsufficientEvidence("The root cause has no activity, rework, or gap evidence.")

    tier2 = [{"kpi_id": item} for item in root_cause.get("tier2_kpi_ids") or []]
    gaps = [{"gap_reference": item} for item in root_cause.get("gap_references") or []]
    impact_context = {
        "impact_statistics": (root_cause.get("detail") or {}).get("impact_statistics") or [],
        "impact_percentage": root_cause.get("impact_percentage"),
        "total_cases": root_cause.get("total_cases"),
    }
    impact = calculate_impact_from_root_cause(root_cause, impact_context)
    representative = select_representative_case(root_cause)
    if representative is None or not representative.get("case_id"):
        raise NoRepresentativeCase("No representative event-log case could be selected.")
    if on_case_selected is not None:
        on_case_selected(str(representative["case_id"]))

    target_stages = _target_stages(root_cause, representative)
    current_state = _load_case_activities(representative["case_id"], target_stages)
    if not current_state:
        raise NoRepresentativeCase("The representative case has no event-log activities to transform.")
    observed_systems = _case_systems(current_state)

    process_context = build_process_context(root_cause, kpi, tier2, gaps)
    process_context["allowed_systems"] = observed_systems
    process_context["systems_in_scope"] = observed_systems
    recommendation_context = {
        "tier1_kpi": kpi,
        "tier2_kpis": tier2,
        "gaps": gaps,
        "root_cause": root_cause,
        "impact": impact,
        "representative_case": representative,
        "process_context": process_context,
    }

    generation_warnings = []
    try:
        recommendation, model = generate_recommendation(recommendation_context, client=client)
    except (AzureOpenAIError, RecommendationError) as error:
        logger.warning("Using deterministic recommendation fallback: %s", error)
        fallback = build_fallback_plan(recommendation_context, root_cause)
        for agent in fallback["agents"]:
            agent["systems"] = observed_systems
        recommendation = RecommendationLLM.model_validate(fallback)
        model = "deterministic_fallback"
        generation_warnings.append("Azure recommendation output was unavailable or invalid; deterministic routing was used.")

    routing = deterministic_agents(recommendation_context)
    name_to_agent_id = {agent["name"]: agent["agent_id"] for agent in routing["agents"]}
    transformation_context = {
        "case_id": representative["case_id"],
        "stages": target_stages,
        "current_state": current_state,
        "tier1_kpi": kpi,
        "tier2_kpis": tier2,
        "gaps": gaps,
        "root_cause_summary": {
            "root_cause_id": canonical_id,
            "title": root_cause.get("title"),
            "description": root_cause.get("description"),
            "confidence": root_cause.get("confidence"),
            "gap_references": root_cause.get("gap_references") or [],
            "impact_statistics": impact.get("metrics"),
        },
        "recommended_agents": [agent.model_dump() for agent in recommendation.agents],
        "allowed_agent_names": [agent.name for agent in recommendation.agents],
        "case_systems": observed_systems,
    }
    try:
        to_be_plan, to_be_model = generate_to_be(transformation_context, client=client)
    except (AzureOpenAIError, ToBeError) as error:
        logger.warning("Using deterministic To-Be fallback: %s", error)
        to_be_plan = build_to_be_fallback(transformation_context)
        to_be_model = "deterministic_fallback"
        generation_warnings.append("Azure To-Be output was unavailable or invalid; observed activities were preserved.")

    to_be_rows = _to_be_rows(current_state, to_be_plan, name_to_agent_id)
    section4 = _section4(current_state, to_be_rows)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": to_be_model or model,
        "generation_warnings": generation_warnings,
        # "representative_case": _section1(
        #     root_cause, kpi, tier2, gaps, representative, recommendation.agents, target_stages,
        # ),
        # "kpi_context": {
        #     "tier_1_kpi": kpi,
        #     "tier_2_kpis": tier2,
        #     "gaps": gaps,
        #     "root_cause": {
        #         "root_cause_id": canonical_id,
        #         "title": root_cause.get("title"),
        #         "description": root_cause.get("description"),
        #     },
        # },
        "to_be_state": to_be_rows,
        "to_be_summary": to_be_plan.stage_summary,
        "current_vs_to_be": section4,
        "agentic_workflow": _section5(recommendation.agents, name_to_agent_id),
        # "traceability": _section7(current_state, to_be_rows, root_cause, kpi, tier2),
        "summary": _summary(section4, target_stages, representative, to_be_model or model),
    }