"""
backend.agents.recommendation_agent - deterministic agent selection + LLM plan design.

IMPORTANT DESIGN CHANGE
-----------------------
Agent selection is deterministic in this module. The LLM no longer decides which agents exist.
The selected agents are derived from:

    Tier 1 KPI + Tier 2 diagnostic KPI + Gap reference

The LLM is used only to describe how those deterministic agents should operate together:
trigger, actions, systems, human-in-the-loop, sequencing and future-state flow.

This keeps the recommendation stable even when a representative case is unavailable.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .. import config
from ..schemas import LLM_RESPONSE_FORMAT, RecommendationLLM


class RecommendationError(RuntimeError):
    """The recommendation could not be produced or validated."""


# ============================================================================
# DETERMINISTIC AGENT CATALOG
# ============================================================================
# These are intentionally kept in this file so that KPI -> agent changes only
# require updating recommendation_agent.py.

TIER1_AGENT_MAP: Dict[str, Dict[str, str]] = {
    "T1-01": {"name": "Loan Cycle Orchestration Agent", "capability": "End-to-end cycle-time orchestration"},
    "T1-02": {"name": "SLA Monitoring and Escalation Agent", "capability": "SLA monitoring, breach detection and escalation"},
    "T1-03": {"name": "First-Time-Right Validation Agent", "capability": "Prevent errors and missing information before downstream processing"},
    "T1-04": {"name": "Rework Prevention Agent", "capability": "Detect and prevent avoidable rework loops"},
    "T1-05": {"name": "Workflow Handoff Optimization Agent", "capability": "Reduce unnecessary handoffs and improve routing"},
    "T1-06": {"name": "Process Queue Optimization Agent", "capability": "Identify, prioritize and reduce process waiting"},
    "T1-07": {"name": "Process Automation Agent", "capability": "Automate repetitive manual processing"},
    "T1-08": {"name": "Exception Detection and Resolution Agent", "capability": "Detect, classify and resolve process exceptions"},
    "T1-09": {"name": "Loan Cost Optimization Agent", "capability": "Identify avoidable processing effort and cost drivers"},
    "T1-10": {"name": "Disbursement Acceleration Agent", "capability": "Remove delays between final approval and disbursement"},
    "T1-11": {"name": "Stalled Case Recovery Agent", "capability": "Detect and recover stalled cases"},
    "T1-12": {"name": "WIP Management Agent", "capability": "Prioritize and manage open work-in-progress"},
    "T1-13": {"name": "Approval Readiness Agent", "capability": "Improve completeness and readiness for approval"},
    "T1-14": {"name": "Application Intake and Capacity Agent", "capability": "Manage application intake and processing capacity"},
    "T1-15": {"name": "Straight-Through Processing Enablement Agent", "capability": "Increase eligible straight-through processing"},
}

# Tier-2 diagnostic capabilities. These IDs/names are aligned to kpi_core.py.
TIER2_AGENT_MAP: Dict[str, Dict[str, str]] = {
    "T2-01": {"name": "Waiting Hotspot Optimization Agent", "capability": "Target the highest waiting activity"},
    "T2-02": {"name": "Information Request Prevention Agent", "capability": "Reduce avoidable information requests"},
    "T2-03": {"name": "Information Completeness Agent", "capability": "Prevent repeated information-request rounds"},
    "T2-04": {"name": "RM Response Coordination Agent", "capability": "Prioritize and coordinate RM responses"},
    "T2-05": {"name": "Document Request Optimization Agent", "capability": "Reduce avoidable document requests"},
    "T2-06": {"name": "Customer Document Chase Agent", "capability": "Track and follow up outstanding customer documents"},
    "T2-07": {"name": "Credit Reconstruction Agent", "capability": "Reduce reconstruction effort in credit review"},
    "T2-08": {"name": "Communication Consolidation Agent", "capability": "Reduce fragmented email-based communication"},
    "T2-09": {"name": "Financial Spreading Automation Agent", "capability": "Automate repetitive financial spreading work"},
    "T2-10": {"name": "Financial Spreading Validation Agent", "capability": "Validate financial spreading and prevent rework"},
    "T2-11": {"name": "Credit Review Productivity Agent", "capability": "Reduce excessive credit-review effort per case"},
    "T2-12": {"name": "Approval Workflow Optimization Agent", "capability": "Optimize approval workflow cycle time"},
    "T2-13": {"name": "Approval Layer Simplification Agent", "capability": "Identify unnecessary approval layers"},
    "T2-13-GM": {"name": "GM Approval Queue Agent", "capability": "Prioritize and escalate waiting before GM approval"},
    "T2-13-CCO": {"name": "CCO Approval Queue Agent", "capability": "Prioritize and escalate waiting before CCO approval"},
    "T2-13-CEO": {"name": "CEO Approval Queue Agent", "capability": "Prioritize and escalate waiting before CEO approval"},
    "T2-13-CBO": {"name": "CBO Approval Queue Agent", "capability": "Prioritize and escalate waiting before CBO approval"},
    "T2-13-Board": {"name": "Board Approval Coordination Agent", "capability": "Prioritize and escalate waiting before Board approval"},
    "T2-14": {"name": "Conditional Approval Optimization Agent", "capability": "Reduce avoidable conditional approvals"},
    "T2-15": {"name": "Approval Conditions Tracking Agent", "capability": "Ensure approval conditions are tracked in-system"},
    "T2-16": {"name": "Conditional Return Prevention Agent", "capability": "Prevent conditional approvals returning for rework"},
    "T2-17": {"name": "Conditional Rework Reduction Agent", "capability": "Reduce effort lost to conditional rework"},
    "T2-18": {"name": "Committee Pack Validation Agent", "capability": "Detect and prevent committee-pack version issues"},
    "T2-19": {"name": "Collateral Data Standardization Agent", "capability": "Standardize free-text collateral records"},
    "T2-20": {"name": "Collateral Reconciliation Agent", "capability": "Detect and reconcile collateral discrepancies"},
    "T2-21": {"name": "Collateral Processing Automation Agent", "capability": "Automate manual collateral processing"},
    "T2-22": {"name": "Guarantor Duplicate Detection Agent", "capability": "Detect duplicate guarantor records"},
    "T2-23": {"name": "Existing Facility Update Agent", "capability": "Identify and coordinate required facility updates"},
    "T2-24": {"name": "Legal Review Routing Agent", "capability": "Route and prioritize legal review"},
    "T2-25": {"name": "Legal Queue Optimization Agent", "capability": "Reduce waiting for Legal"},
    "T2-26": {"name": "Contract Quality Validation Agent", "capability": "Detect contract issues before return"},
    "T2-27": {"name": "Operations Clarification Agent", "capability": "Reduce unclear operations recommendations"},
    "T2-28": {"name": "Booking Validation Agent", "capability": "Detect booking discrepancies before completion"},
    "T2-29": {"name": "Data Re-keying Automation Agent", "capability": "Reduce duplicate data entry and re-keying"},
    "T2-30": {"name": "Balance Limit Control Agent", "capability": "Detect and resolve balance-limit indicator reset failures"},
    "T2-31": {"name": "Post-Disbursement Update Agent", "capability": "Ensure required post-disbursement status updates"},
    "T2-32": {"name": "Paperless Processing Agent", "capability": "Reduce physical and paper-based processing"},
    "T2-33": {"name": "System Interaction Optimization Agent", "capability": "Reduce unnecessary systems touched per case"},
    "T2-34": {"name": "Control Exception Prevention Agent", "capability": "Detect and prevent control exceptions"},
    "T2-35": {"name": "Credit Workload Balancing Agent", "capability": "Balance workload across Credit Review makers"},
    "T2-36": {"name": "Case Assignment Optimization Agent", "capability": "Optimize case assignment and reassignment"},
    "T2-37": {"name": "Credit Queue Prioritization Agent", "capability": "Reduce queue time before credit analysis"},
    "T2-38": {"name": "Rework Loop Detection Agent", "capability": "Identify and intervene on recurring rework loops"},
    "T2-39": {"name": "Rework Loss Recovery Agent", "capability": "Reduce days lost to rework"},

    # Dynamic stage diagnostics generated by kpi_core.py
    "T2-STG-1": {"name": "Intake Cycle Optimization Agent", "capability": "Optimize Intake & Screening cycle time"},
    "T2-STG-2": {"name": "Credit Review Cycle Optimization Agent", "capability": "Optimize Credit Review & Analysis cycle time"},
    "T2-STG-3": {"name": "Monitoring and Approval Cycle Agent", "capability": "Optimize Monitoring & Approval cycle time"},
    "T2-STG-4": {"name": "Operations Cycle Optimization Agent", "capability": "Optimize Operations Execution cycle time"},
    "T2-STG-5": {"name": "Post-Disbursement Cycle Agent", "capability": "Optimize Post-Disbursement & Updates cycle time"},
}

# Gap-level capabilities. Only gaps that are referenced by the supplied KPI
# model are mapped here; unknown gaps fall back to a deterministic generic
# gap agent rather than being invented by the LLM.
GAP_AGENT_MAP: Dict[str, Dict[str, str]] = {
    "G01": {"name": "Information Completeness Agent", "capability": "Improve information completeness before downstream review"},
    "G05": {"name": "Customer Document Readiness Agent", "capability": "Improve customer document readiness"},
    "G06": {"name": "Information Validation Agent", "capability": "Validate information before requesting clarification"},
    "G07": {"name": "RM Information Coordination Agent", "capability": "Coordinate missing information and RM responses"},
    "G08": {"name": "Communication Consolidation Agent", "capability": "Consolidate fragmented communication"},
    "G09": {"name": "Data Reconstruction Automation Agent", "capability": "Reduce manual data reconstruction and re-keying"},
    "G10": {"name": "Financial Data Quality Agent", "capability": "Improve financial spreading data quality"},
    "G11": {"name": "Credit Review Effort Optimization Agent", "capability": "Reduce avoidable credit review effort"},
    "G12": {"name": "Operations Decision Support Agent", "capability": "Improve clarity of operations recommendations"},
    "G13": {"name": "Committee Pack Governance Agent", "capability": "Control committee pack versions"},
    "G14": {"name": "Credit Workload Balancing Agent", "capability": "Balance Credit Review workload"},
    "G15": {"name": "Approval Conditions Control Agent", "capability": "Track and control approval conditions"},
    "G16": {"name": "Approval Queue Management Agent", "capability": "Prioritize approval queues"},
    "G17": {"name": "Approval SLA Escalation Agent", "capability": "Escalate approval delays against SLA"},
    "G18": {"name": "Paperless Process Agent", "capability": "Remove physical/paper processing steps"},
    "G19": {"name": "Collateral Data Standardization Agent", "capability": "Standardize collateral master data"},
    "G20": {"name": "Collateral Reconciliation Agent", "capability": "Reconcile collateral discrepancies"},
    "G21": {"name": "Rework Loop Prevention Agent", "capability": "Prevent recurring rework loops and control exceptions"},
    "G22": {"name": "Cross-System Data Consistency Agent", "capability": "Improve consistency across systems"},
    "G23": {"name": "Document Request Optimization Agent", "capability": "Reduce avoidable document requests"},
    "G24": {"name": "Committee Pack Version Control Agent", "capability": "Prevent committee pack version errors"},
    "G25": {"name": "Legal Queue Management Agent", "capability": "Reduce Legal review waiting"},
    "G26": {"name": "Contract Quality Agent", "capability": "Prevent contract returns"},
    "G27": {"name": "Conditional Return Prevention Agent", "capability": "Prevent conditional approval returns"},
    "G28": {"name": "Guarantor Duplicate Detection Agent", "capability": "Detect duplicate guarantors"},
    "G29": {"name": "Guarantor Master Data Agent", "capability": "Improve guarantor master-data quality"},
    "G30": {"name": "Paperless Processing Agent", "capability": "Reduce physical processing"},
    "G31": {"name": "Booking Control Agent", "capability": "Prevent booking discrepancies"},
    "G32": {"name": "Balance Limit Control Agent", "capability": "Prevent balance-limit reset failures"},
    "G33": {"name": "Post-Disbursement Control Agent", "capability": "Prevent missed post-disbursement updates"},
}

TIER1_SHORT_NAME = {
    "T1-01": "Cycle Time",
    "T1-02": "SLA",
    "T1-03": "First-Time-Right",
    "T1-04": "Rework",
    "T1-05": "Handoff",
    "T1-06": "Waiting",
    "T1-07": "Manual Effort",
    "T1-08": "Exception",
    "T1-09": "Cost",
    "T1-10": "Disbursement TAT",
    "T1-11": "Stalled Case",
    "T1-12": "WIP",
    "T1-13": "Approval Rate",
    "T1-14": "Application Volume",
    "T1-15": "STP",
}


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _id_from(value: Any) -> Optional[str]:
    """Extract a KPI/gap id from a dict, string or nested value."""
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("kpi_id", "tier1_kpi_id", "id", "gap_reference", "code"):
            if value.get(key):
                return str(value[key]).strip()
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r"^(T1-[^\s]+|T2-[^\s]+|G\d+)", text)
    return match.group(1) if match else text.split()[0]


def _split_ids(values: Any) -> List[str]:
    """Flatten IDs such as ['G15;G27', {'kpi_id':'T2-16'}]."""
    result: List[str] = []
    if values is None:
        return result
    if not isinstance(values, list):
        values = [values]
    for value in values:
        if isinstance(value, dict):
            candidate = _id_from(value)
            if candidate:
                values2 = [candidate]
            else:
                values2 = []
        else:
            values2 = str(value).replace(",", ";").split(";")
        for item in values2:
            item = str(item).strip()
            if item and item not in result:
                result.append(item)
    return result


def _tier1_ids(context: dict) -> List[str]:
    value = context.get("tier1_kpi")
    if isinstance(value, dict):
        value = value.get("kpi_id") or value.get("tier_1_kpi_id") or value.get("id")
    if value:
        ids = _split_ids(value)
        return [x for x in ids if x in TIER1_AGENT_MAP]
    rc = context.get("root_cause") or {}
    rid = rc.get("tier1_kpi_id")
    return [rid] if rid in TIER1_AGENT_MAP else []


def _tier2_ids(context: dict) -> List[str]:
    ids: List[str] = []
    for item in context.get("tier2_kpis") or []:
        candidate = _id_from(item)
        if candidate and candidate not in ids:
            ids.append(candidate)
    return [x for x in ids if x in TIER2_AGENT_MAP]


def _gap_ids(context: dict) -> List[str]:
    rc = context.get("root_cause") or {}
    values: List[Any] = []
    values.extend(rc.get("gap_references") or [])
    values.extend(context.get("gaps") or [])
    return _split_ids(values)


def _combo_agent(t1_id: str, t2_id: str) -> Dict[str, str]:
    """Create a deterministic, combination-specific diagnostic agent."""
    t1 = TIER1_SHORT_NAME.get(t1_id, t1_id)
    t2 = TIER2_AGENT_MAP[t2_id]
    base_name = re.sub(r"\s+Agent$", "", t2["name"])
    return {
        "agent_id": f"{t1_id.lower()}__{t2_id.lower()}__diagnostic",
        "name": f"{t1} {base_name} Agent",
        "capability": f"Apply {t2['capability']} specifically to the {TIER1_AGENT_MAP[t1_id]['capability']} problem.",
        "source": "tier1_tier2",
        "tier1_kpi_id": t1_id,
        "tier2_kpi_id": t2_id,
    }


def _deterministic_agents(context: dict) -> Dict[str, Any]:
    """Return the exact agent set the LLM is allowed to describe."""
    t1_ids = _tier1_ids(context)
    t2_ids = _tier2_ids(context)
    gap_ids = _gap_ids(context)

    if not t1_ids:
        raise RecommendationError("No valid Tier 1 KPI id was supplied for deterministic agent selection.")

    selected: List[Dict[str, Any]] = []

    # 1. Combination-specific agent is always first. This is what makes
    #    T1 + T2 combinations deterministic and distinguishable.
    for t1_id in t1_ids:
        related_t2 = t2_ids or []
        for t2_id in related_t2:
            selected.append(_combo_agent(t1_id, t2_id))

    # If there is no Tier-2 diagnostic, use the Tier-1 capability directly.
    if not selected:
        for t1_id in t1_ids:
            base = TIER1_AGENT_MAP[t1_id]
            selected.append({
                "agent_id": t1_id.lower(),
                "name": base["name"],
                "capability": base["capability"],
                "source": "tier1",
                "tier1_kpi_id": t1_id,
            })

    # 2. Add one deterministic gap specialist. The first documented gap is
    #    used so the plan remains stable when several gaps are attached.
    for gap_id in gap_ids:
        if gap_id in GAP_AGENT_MAP:
            gap = GAP_AGENT_MAP[gap_id]
            selected.append({
                "agent_id": gap_id.lower(),
                "name": gap["name"],
                "capability": gap["capability"],
                "source": "gap",
                "gap_id": gap_id,
            })
            break

    # 3. Add the Tier-1 base capability only when it is not already represented
    #    by the combination-specific agent. This gives the LLM a useful
    #    orchestration/control agent without making every plan identical.
    if len(selected) < 2:
        base = TIER1_AGENT_MAP[t1_ids[0]]
        selected.append({
            "agent_id": t1_ids[0].lower(),
            "name": base["name"],
            "capability": base["capability"],
            "source": "tier1",
            "tier1_kpi_id": t1_ids[0],
        })

    # Deduplicate by deterministic agent_id while preserving order.
    unique: List[Dict[str, Any]] = []
    seen = set()
    for item in selected:
        if item["agent_id"] not in seen:
            unique.append(item)
            seen.add(item["agent_id"])

    # Keep within the existing product limit.
    unique = unique[: max(1, config.MAX_AGENTS)]

    routing_key = "|".join(t1_ids) + "|" + "+".join(t2_ids or ["NONE"]) + "|" + "+".join(gap_ids or ["NONE"])

    return {
        "routing_key": routing_key,
        "tier1_kpi_ids": t1_ids,
        "tier2_kpi_ids": t2_ids,
        "gap_ids": gap_ids,
        "agents": unique,
        "deterministic": True,
    }


# ============================================================================
# LLM PROMPT
# ============================================================================

SYSTEM_PROMPT = """CORPORATE LOAN - AI FIX RECOMMENDATION AGENT (Deloitte Kinetic)

ROLE
You design the implementation plan for one already-diagnosed root cause in corporate loan origination.
The KPI engine decided what is wrong. The root-cause logic decided why. A deterministic routing layer
has ALREADY decided which AI agents are permitted for this KPI + diagnostic KPI + gap combination.

IMPORTANT: YOU DO NOT SELECT, INVENT, RENAME, ADD OR REMOVE AGENTS.
The deterministic_agent_routing.agents list in the evidence pack is the authoritative agent list.
Return exactly those agents in exactly that order.

You are responsible only for describing HOW the selected agents work: actions, triggers, systems,
human-in-the-loop controls, sequencing, rationale and future-state flow.

ABSOLUTE RULES
1. Never invent a metric, case id, KPI value, activity or system. If a number is not in the evidence
   pack, do not state it.
2. Only name systems that appear in allowed_systems. If the right capability has no system in the data
   model, describe it functionally instead of naming a product.
3. Every deterministic agent must address THIS root cause and its KPI/Tier-2/gap evidence.
4. Do not add any agent that is not in deterministic_agent_routing.agents.
5. Do not rename deterministic agents. The application will enforce the deterministic names.
6. Do not claim quantified benefits unless the figure is already in the evidence pack.
7. Ground every agent in KPI -> diagnostic KPI -> root cause -> gap/evidence -> agent -> action.
8. A representative case may be absent. If it is absent, use aggregate impact, root-cause evidence,
   hotspot activities, manual activities, stage profile and documented gaps. Do not invent a case.

DESIGNING THE PLAN
For each deterministic agent provide:
  * what problem it addresses
  * concrete ordered actions
  * trigger
  * systems/data from allowed_systems
  * qualitative expected process impact
  * human approval requirements
  * sequence_order
  * why_this_agent tied to the actual KPI/Tier-2/gap/root-cause evidence

Use the deterministic agent list as the exact list of agents. Prefer the supplied list even when the
representative case is missing.

HUMAN IN THE LOOP
Anything that communicates with a customer, overrides a credit/compliance decision, or commits the
bank must have human_in_loop = true and specify who approves what. Classification, validation,
enrichment and prioritisation can run unattended.

FUTURE STATE
Return a simplified target-state flow as an ordered list using the existing response schema. Include
every deterministic agent and any required human approval point.

REPRESENTATIVE CASE
If a representative case exists, explain why that case represents this root cause using its timeline.
If no representative case exists, explicitly state that the recommendation is based on aggregate
root-cause evidence and do not fabricate a case id or timeline.

Write for a banking operations executive: concrete, calm and specific. No marketing language."""


def build_payload(context: dict) -> str:
    """Build compact evidence pack plus deterministic agent routing."""
    rc = context.get("root_cause") or {}
    case = context.get("representative_case") or {}
    proc = context.get("process_context") or {}
    routing = _deterministic_agents(context)

    return json.dumps({
        "tier_1_kpi": context.get("tier1_kpi"),
        "tier_2_kpis": context.get("tier2_kpis"),
        "documented_gaps": context.get("gaps"),
        "root_cause": {
            "root_cause_id": rc.get("root_cause_id"),
            "title": rc.get("title"),
            "description": rc.get("description"),
            "category": rc.get("category"),
            "confidence": rc.get("confidence"),
            "gap_references": rc.get("gap_references"),
            "stages": rc.get("stages"),
            "evidence_chain": (rc.get("detail") or {}).get("evidence_chain"),
            "impact_statistics": (rc.get("detail") or {}).get("impact_statistics"),
            "contributing_factors": (rc.get("detail") or {}).get("contributing_factors"),
        },
        "impact_metrics": context.get("impact"),
        "representative_case": {
            "available": bool(case),
            "case_id": case.get("case_id"),
            "process_stage": case.get("process_stage"),
            "current_status": case.get("current_status"),
            "cycle_time_days": case.get("cycle_time"),
            "sla_days": case.get("sla"),
            "sla_breach_days": case.get("sla_breach"),
            "rework_loops": case.get("rework_loops"),
            "handoffs": case.get("handoffs"),
            "key_events": case.get("key_events"),
            "evidence": case.get("evidence"),
        },
        "hotspot_activities": proc.get("hotspot_activities"),
        "manual_activities": proc.get("manual_activities"),
        "stage_profile": proc.get("stage_profile"),
        "systems_in_scope": proc.get("systems_in_scope"),
        "allowed_systems": proc.get("allowed_systems"),
        "max_agents": config.MAX_AGENTS,
        "deterministic_agent_routing": routing,
    }, separators=(",", ":"), default=str)


def _invented_systems(plan: RecommendationLLM, allowed: List[str]) -> List[str]:
    """Systems the model named that do not exist in the data model."""
    if not allowed:
        return []
    lookup = {a.strip().lower() for a in allowed}
    offenders = []
    for agent in plan.agents:
        for system in agent.systems:
            if system.strip().lower() not in lookup and system not in offenders:
                offenders.append(system)
    return offenders


def _strip_invented(plan: RecommendationLLM, allowed: List[str]) -> None:
    lookup = {a.strip().lower() for a in allowed}
    for agent in plan.agents:
        agent.systems = [s for s in agent.systems if s.strip().lower() in lookup]


def _agent_name_field(agent: Any) -> Optional[str]:
    """Find the agent-name field without depending on the exact schema spelling."""
    fields = getattr(agent.__class__, "model_fields", {}) or {}
    for candidate in ("agent_name", "name", "title"):
        if candidate in fields:
            return candidate
    return None


def _force_deterministic_agent_names(plan: RecommendationLLM, routing: Dict[str, Any]) -> None:
    """Enforce deterministic names after Pydantic validation.

    The LLM can fill the implementation details, but it cannot change the
    deterministic agent identity.
    """
    expected = routing["agents"]
    if len(plan.agents) != len(expected):
        raise ValueError(
            f"model returned {len(plan.agents)} agents but deterministic routing requires "
            f"{len(expected)} agents"
        )

    for agent, selected in zip(plan.agents, expected):
        field = _agent_name_field(agent)
        if not field:
            raise ValueError(
                "Recommendation agent schema does not expose an agent name field "
                "(expected agent_name, name or title)."
            )
        setattr(agent, field, selected["name"])


def _call_model(client, payload: str, correction: Optional[str]) -> str:
    """One Responses API round trip with a strict output schema."""
    user_input = payload if not correction else (
        f"{payload}\n\nYour previous answer was rejected: {correction}\n"
        f"Return a corrected answer that satisfies the schema and the deterministic routing rules."
    )
    kwargs = {
        "model": config.MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": [{"role": "user", "content": user_input}],
        "text": {"format": LLM_RESPONSE_FORMAT},
        "max_output_tokens": config.MAX_OUTPUT_TOKENS,
    }
    if config.EFFORT and config.EFFORT != "default":
        kwargs["reasoning"] = {"effort": config.EFFORT}

    response = client.responses.create(**kwargs)

    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
        raise RecommendationError(f"The model returned an incomplete response ({reason}).")
    for item in getattr(response, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal":
                raise RecommendationError(f"The model declined the request: {block.refusal}")
    return (getattr(response, "output_text", "") or "").strip()


def generate(context: dict, client=None) -> Tuple[RecommendationLLM, str]:
    """Produce a validated plan using deterministic KPI/Tier-2/Gap agent selection."""
    client = client or config.llm_client()
    routing = _deterministic_agents(context)
    payload = build_payload(context)
    allowed = (context.get("process_context") or {}).get("allowed_systems") or []
    correction, last_error = None, None

    for attempt in (1, 2):
        try:
            raw = _call_model(client, payload, correction)
            if not raw:
                raise ValueError("the model returned no text")

            plan = RecommendationLLM.model_validate_json(raw)

            if len(plan.agents) > config.MAX_AGENTS:
                plan.agents = plan.agents[:config.MAX_AGENTS]

            # The deterministic routing layer is authoritative.
            _force_deterministic_agent_names(plan, routing)

            offenders = _invented_systems(plan, allowed)
            if offenders:
                if attempt == 1:
                    raise ValueError(
                        f"these systems are not in allowed_systems: {', '.join(offenders)}. "
                        f"Use only systems from allowed_systems, or describe the capability "
                        f"without naming a product."
                    )
                _strip_invented(plan, allowed)

            return plan, config.model_label()

        except RecommendationError:
            raise
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            correction = last_error[:1500]

    raise RecommendationError(f"The recommendation could not be validated: {last_error}")
