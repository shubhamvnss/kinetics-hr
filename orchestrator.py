from __future__ import annotations

import datetime as _dt
import os
from typing import List, Optional, Tuple

from .. import config, db
from ..services import event_log_service, impact_service, process_context, representative_case, \
    root_cause_service
from ..services import to_be_diagram
from .fix_plan_orchestrator import InsufficientEvidence, RootCauseNotFound
from .recommendation_agent import RecommendationError, _deterministic_agents, generate as generate_recommendation
from .to_be_agent import ToBeError, generate as generate_to_be

try:                                                        # pragma: no cover - env dependent
    from kpi_core import APPROVAL_STEPS
except Exception:                                           # pragma: no cover
    APPROVAL_STEPS = set()

HERE = os.path.dirname(os.path.abspath(__file__))
DIAGRAM_DIR = os.path.join(HERE, "..", "generated", "to_be_diagrams")


class NoImpactedCases(RuntimeError):
    """This root cause matches no cases in the event log, so there is nothing to transform."""


class NoRepresentativeCase(RuntimeError):
    """No case could be selected to represent this root cause."""


class ToBeGenerationError(RuntimeError):
    """The transformation view could not be generated or validated."""


def _now() -> str:
    return _dt.datetime.now().isoformat(sep=" ", timespec="seconds")


def _target_stages(root_cause: dict, case: dict) -> List[str]:
    stages = root_cause.get("stages") or []
    if stages:
        return stages
    if case.get("process_stage"):
        return [case["process_stage"]]
    return []


def _current_state(conn, case_id: str, stages: List[str]) -> List[dict]:
    rows = event_log_service.stage_activities(conn, case_id, stages or None)
    if not rows and stages:                                 # stage names didn't match this case; fall back
        rows = event_log_service.stage_activities(conn, case_id, None)
    return rows


# ============================================================================
# SECTION BUILDERS - all deterministic, no LLM involvement
# ============================================================================

def _section1(root_cause: dict, kpi: dict, tier2: List[dict], gaps: List[dict], case: dict,
             agents: List, target_stages: List[str]) -> dict:
    return {
        "representative_case_id": case["case_id"],
        "application_id": case.get("application_id"),
        "stage_being_transformed": target_stages or [case.get("process_stage")],
        "tier_1_kpi": {
            "kpi_id": kpi.get("kpi_id"), "kpi_name": kpi.get("kpi_name"),
            "current_value": kpi.get("current_value"), "target": kpi.get("target"),
            "unit": kpi.get("unit"), "variance_vs_target_pct": kpi.get("variance_vs_target_pct"),
            "health": kpi.get("health"),
        },
        "tier_2_kpis": [
            {"kpi_id": t.get("kpi_id"), "kpi_name": t.get("kpi_name"),
             "current_value": t.get("current_value"), "target": t.get("target"),
             "variance_vs_target_pct": t.get("variance_vs_target_pct"), "health": t.get("health")}
            for t in tier2
        ],
        "gap_references": [g["gap_reference"] for g in gaps] or (root_cause.get("gap_references") or []),
        "root_causes": [{
            "root_cause_id": root_cause.get("root_cause_id"),
            "title": root_cause.get("title") or "",
            "description": root_cause.get("description") or "",
            "confidence": root_cause.get("confidence"),
        }],
        "recommended_agents": [a.name for a in agents],
        "kpi_evidence_from_case": {
            "cycle_time_days": case.get("cycle_time"),
            "sla_days": case.get("sla"),
            "sla_breach_days": case.get("sla_breach"),
            "rework_loops": case.get("rework_loops"),
            "handoffs": case.get("handoffs"),
            "manual_touches": case.get("manual_touches"),
            "systems_touched": case.get("systems_touched"),
        },
    }


def _to_be_rows(current_state: List[dict], to_be_plan, name_to_agent_id: dict) -> List[dict]:
    by_seq = {a.current_sequence: a for a in to_be_plan.activities}
    rows = []
    for cs in current_state:
        act = by_seq[cs["sequence"]]
        rows.append({
            "to_be_sequence": act.to_be_sequence,
            "current_sequence": cs["sequence"],
            "current_step_id": cs["step_id"],
            "current_activity": cs["activity_name"],
            "to_be_activity": act.to_be_activity,
            "change_type": act.change_type,
            "responsible_agent": act.responsible_agent,
            "responsible_agent_id": name_to_agent_id.get(act.responsible_agent) if act.responsible_agent else None,
            "deterministic_automation": act.deterministic_automation,
            "human_in_the_loop": act.human_in_the_loop,
            "system_used": act.system_used,
            "trigger": act.trigger,
            "action": act.action,
            "expected_outcome": act.expected_outcome,
            "reason_for_change": act.reason_for_change,
            "kpi_gap_addressed": act.kpi_gap_addressed,
        })
    rows.sort(key=lambda r: (r["to_be_sequence"], r["current_sequence"]))
    return rows


def _actor(row: dict) -> str:
    if row["change_type"] == "AI Agent":
        return row.get("responsible_agent") or "AI Agent"
    if row["change_type"] == "Automated (Deterministic)":
        return "Deterministic Automation"
    return "Human"


def _section4(current_state: List[dict], to_be_rows: List[dict]) -> dict:
    cur_by_seq = {r["sequence"]: r for r in current_state}
    cur_n = len(current_state)
    cur_manual = sum(1 for r in current_state if r["manual_touch"])
    cur_rework = sum(1 for r in current_state if r["rework"])
    cur_waiting = sum(1 for r in current_state if (r["waiting_time_minutes"] or 0) > 0)
    cur_handoffs = sum(1 for r in current_state if r["handoff"])
    cur_approvals = sum(1 for r in current_state if r["step_id"] in APPROVAL_STEPS)
    cur_systems = sorted({p.strip() for r in current_state if r["system_used"] and r["system_used"] != "N/A"
                          for p in str(r["system_used"]).split("/") if p.strip()})

    active = [r for r in to_be_rows if r["change_type"] != "Eliminated"]
    groups: dict = {}
    order: List[int] = []
    for r in active:
        seq = r["to_be_sequence"]
        if seq not in groups:
            groups[seq] = []
            order.append(seq)
        groups[seq].append(r)

    tobe_n = len(order)
    tobe_agent = sum(1 for seq in order if any(x["change_type"] == "AI Agent" for x in groups[seq]))
    tobe_deterministic = sum(1 for seq in order
                             if any(x["change_type"] == "Automated (Deterministic)" for x in groups[seq]))
    tobe_human_approval = sum(1 for seq in order if any(x["human_in_the_loop"] for x in groups[seq]))
    tobe_rework = sum(1 for seq in order
                      if all(x["change_type"] == "Retained" for x in groups[seq])
                      and any(cur_by_seq[x["current_sequence"]]["rework"] for x in groups[seq]))
    tobe_waiting = sum(1 for seq in order
                       if all(x["change_type"] == "Retained" for x in groups[seq])
                       and any((cur_by_seq[x["current_sequence"]]["waiting_time_minutes"] or 0) > 0
                               for x in groups[seq]))
    tobe_systems = sorted({s for seq in order for x in groups[seq] for s in x["system_used"]})

    ordered_groups = [groups[seq] for seq in order]
    actors = [_actor(g[0]) for g in ordered_groups]
    tobe_handoffs = sum(1 for a, b in zip(actors[:-1], actors[1:]) if a != b)

    eliminated = [{"current_sequence": r["current_sequence"], "current_activity": r["current_activity"],
                  "reason_for_change": r["reason_for_change"]}
                 for r in to_be_rows if r["change_type"] == "Eliminated"]
    automated = [{"to_be_sequence": seq, "to_be_activity": groups[seq][0]["to_be_activity"]}
                for seq in order if any(x["change_type"] == "Automated (Deterministic)" for x in groups[seq])]
    consolidated = [{"to_be_sequence": seq, "to_be_activity": groups[seq][0]["to_be_activity"],
                    "current_activities": [x["current_activity"] for x in groups[seq]]}
                   for seq in order if len(groups[seq]) > 1]
    human_controlled = [{"to_be_sequence": seq, "to_be_activity": groups[seq][0]["to_be_activity"]}
                       for seq in order
                       if any(x["change_type"] in ("Retained", "Human") for x in groups[seq])]

    return {
        "comparison": {
            "number_of_activities": {"current": cur_n, "to_be": tobe_n},
            "manual_activities": {"current": cur_manual, "to_be": len(human_controlled)},
            "ai_agent_activities": {"current": 0, "to_be": tobe_agent},
            "deterministic_automation_activities": {"current": 0, "to_be": tobe_deterministic},
            "human_approval_activities": {"current": cur_approvals, "to_be": tobe_human_approval},
            "rework_activities": {"current": cur_rework, "to_be": tobe_rework},
            "waiting_activities": {"current": cur_waiting, "to_be": tobe_waiting},
            "handoffs": {"current": cur_handoffs, "to_be": tobe_handoffs},
            "systems_touched": {"current": cur_systems, "to_be": tobe_systems},
        },
        "reduced_steps": cur_n - tobe_n,
        "activities_eliminated": eliminated,
        "activities_automated": automated,
        "activities_consolidated": consolidated,
        "activities_newly_introduced": [],
        "activities_remaining_human_controlled": human_controlled,
    }


def _section5(agents: List, name_to_agent_id: dict) -> List[dict]:
    workflow = []
    ordered = sorted(agents, key=lambda a: a.sequence_order)
    for i, agent in enumerate(ordered):
        next_step = ordered[i + 1].name if i + 1 < len(ordered) else "Process completion"
        workflow.append({
            "agent_id": name_to_agent_id.get(agent.name, agent.agent_id),
            "agent_name": agent.name,
            "sequence_order": agent.sequence_order,
            "trigger": agent.trigger,
            "input": f"Systems/data: {', '.join(agent.systems)}" if agent.systems else "Case and KPI evidence",
            "action": agent.action,
            "systems_accessed": agent.systems,
            "output": agent.expected_outcome,
            "validation": (agent.human_in_loop_detail if agent.human_in_loop
                          else "Automated rule-based validation before system update"),
            "human_in_the_loop": agent.human_in_loop,
            "human_in_the_loop_detail": agent.human_in_loop_detail,
            "next_step": next_step,
        })
    return workflow


def _section7(current_state: List[dict], to_be_rows: List[dict], root_cause: dict, kpi: dict,
             tier2: List[dict]) -> List[dict]:
    by_seq = {r["current_sequence"]: r for r in to_be_rows}
    tier1_label = f"{kpi.get('kpi_id', '')} {kpi.get('kpi_name', '')}".strip() or None
    tier2_label = ", ".join(f"{t.get('kpi_id')} {t.get('kpi_name')}" for t in tier2) or None
    rows = []
    for cs in current_state:
        tb = by_seq.get(cs["sequence"])
        rows.append({
            "current_activity": cs["activity_name"],
            "problem_root_cause": root_cause.get("title") or root_cause.get("root_cause_id"),
            "tier_1_kpi": tier1_label,
            "tier_2_kpi": tier2_label,
            "gap": cs.get("gap_reference"),
            "recommended_agent": tb["responsible_agent"] if tb else None,
            "to_be_activity": tb["to_be_activity"] if tb else None,
            "change_type": tb["change_type"] if tb else None,
        })
    return rows


def _summary(section4: dict, target_stages: List[str], case: dict, model_label: str) -> dict:
    cmp = section4["comparison"]
    narrative = (
        f"{cmp['number_of_activities']['current']} current-state activities in "
        f"{', '.join(target_stages) if target_stages else 'this case'} become "
        f"{cmp['number_of_activities']['to_be']} to-be activities "
        f"({section4['reduced_steps']} fewer steps): "
        f"{cmp['ai_agent_activities']['to_be']} agent-driven, "
        f"{cmp['deterministic_automation_activities']['to_be']} deterministically automated, "
        f"{len(section4['activities_eliminated'])} eliminated, "
        f"{len(section4['activities_consolidated'])} consolidated."
    )
    return {
        "case_id": case["case_id"],
        "stages_transformed": target_stages,
        "current_activity_count": cmp["number_of_activities"]["current"],
        "to_be_activity_count": cmp["number_of_activities"]["to_be"],
        "reduced_steps": section4["reduced_steps"],
        "narrative": narrative,
        "model": model_label,
    }


def run(conn, root_cause_id: str, run_id: Optional[int] = None, client=None) -> dict:
    """Execute the flow and return the Section 8 structured output."""
    print("run executed-------------------")
    run_id = run_id or db.latest_run_id(conn)
    print("1"*10)
    root_cause_service.ensure_populated(conn, run_id, config.ANALYSIS_FILE)
    print("2"*10)
    root_cause = root_cause_service.get_root_cause(conn, root_cause_id, run_id)
    print("root_cause:", root_cause)
    print("3"*10)
    if not root_cause:
        print("4"*10)
        raise RootCauseNotFound(f"Root cause '{root_cause_id}' was not found in KPI run {run_id}.")
    
    if not root_cause_service.has_evidence(root_cause):
        print("5"*10)
        raise InsufficientEvidence(
            f"Root cause '{root_cause_id}' carries no activity, rework or gap evidence, so its "
            f"impacted cases cannot be identified from the event log."
        )
    print("6"*10)
    kpi = root_cause_service.kpi_context(conn, root_cause.get("tier1_kpi_id"), root_cause["run_id"])
    print("7"*10)
    tier2 = root_cause_service.tier2_context(conn, root_cause.get("tier2_kpi_ids") or [], root_cause["run_id"])
    print("8"*10)
    gaps = root_cause_service.gap_context(conn, root_cause.get("gap_references") or [])
    print("9"*10)
    selectors = root_cause_service.selectors(root_cause)
    print("10"*10)
    try:
        print("A"*10)
        impact = impact_service.calculate(conn, selectors, root_cause["run_id"])
        print("B"*10)
        if not impact["impacted_cases"]:
            raise NoImpactedCases(
                f"Root cause '{root_cause_id}' matches no cases in the event log, so there is no "
                f"representative case to transform."
            )
        print("C"*10)
        selection = representative_case.select(conn, selectors, top_n=config.CANDIDATE_CASES)
        print("D"*10)
        case = selection["case"]
        print("E"*10)
        if case is None:
            raise NoRepresentativeCase(
                f"No representative case could be selected for root cause '{root_cause_id}'."
            )
        print("F"*10)
        rec_context = {
            "tier1_kpi": kpi,
            "tier2_kpis": tier2,
            "gaps": gaps,
            "root_cause": root_cause,
            "impact": impact,
            "representative_case": case,
            "process_context": process_context.build(conn, selectors, root_cause["run_id"]),
        }
        target_stages = _target_stages(root_cause, case)
        print("target_stages:", target_stages)
        print("G"*10)
        print("K"*10)
        current_state = _current_state(conn, case["case_id"], target_stages)
        print("current_state:", current_state)
        # current_state = [current_state[-1]]
        plan, model_label = generate_recommendation(rec_context, client=client)
        print("H"*10)
        routing = _deterministic_agents(rec_context)
        print("I"*10)
        name_to_agent_id = {a["name"]: a["agent_id"] for a in routing["agents"]}
        print("J"*10)
        
        
        print("L"*10)
        if not current_state:
            raise NoRepresentativeCase(
                f"Representative case '{case['case_id']}' has no event-log activity to build a "
                f"current-state view from."
            )
        print("M"*10)
        case_systems = event_log_service.case_systems(conn, case["case_id"])
        print("N"*10)

        detail = root_cause.get("detail") or {}
        print("O"*10)
        to_be_context = {
            "case_id": case["case_id"],
            "stages": target_stages,
            "current_state": current_state,
            "tier1_kpi": kpi,
            "tier2_kpis": tier2,
            "gaps": gaps,
            "root_cause_summary": {
                "root_cause_id": root_cause.get("root_cause_id"),
                "title": root_cause.get("title"),
                "description": root_cause.get("description"),
                "confidence": root_cause.get("confidence"),
                "gap_references": root_cause.get("gap_references") or [],
                "evidence_chain": detail.get("evidence_chain"),
                "impact_statistics": detail.get("impact_statistics"),
            },
            "recommended_agents": [a.model_dump() for a in plan.agents],
            "allowed_agent_names": [a.name for a in plan.agents],
            "case_systems": case_systems,
        }
        print("generate to be stared-----------------")
        to_be_plan, _ = generate_to_be(to_be_context, client=client)
        print("generated to be ended-----------------")

        to_be_rows = _to_be_rows(current_state, to_be_plan, name_to_agent_id)
        section4 = _section4(current_state, to_be_rows)

        os.makedirs(DIAGRAM_DIR, exist_ok=True)
        # diagram_path = os.path.join(DIAGRAM_DIR, f"to_be_{root_cause_id}_{case['case_id']}.png")
        # to_be_diagram.render(
        #     to_be_rows, diagram_path,
        #     title=f"To-Be State - {root_cause.get('title') or root_cause_id} ({case['case_id']})",
        # )

        return {
            "generated_at": _now(),
            "model": model_label,
            # "representative_case": _section1(root_cause, kpi, tier2, gaps, case, plan.agents, target_stages),
            # "kpi_context": {
            #     "tier_1_kpi": kpi, "tier_2_kpis": tier2, "gaps": gaps,
            #     "root_cause": {"root_cause_id": root_cause.get("root_cause_id"),
            #                   "title": root_cause.get("title"),
            #                   "description": root_cause.get("description")},
            # },
            "to_be_state": to_be_rows,
            "to_be_summary": to_be_plan.stage_summary,
            "current_vs_to_be": section4,
            "agentic_workflow": _section5(plan.agents, name_to_agent_id),
            # "traceability": _section7(current_state, to_be_rows, root_cause, kpi, tier2),
            # "diagram_path": diagram_path,
            "summary": _summary(section4, target_stages, case, model_label),
        }

    except (RecommendationError, ToBeError) as exc:
        raise ToBeGenerationError(str(exc)) from exc
