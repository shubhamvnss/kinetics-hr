from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.core.azure_openai import generate_json
from app.core.config import settings
from app.schemas.response import LLM_RESPONSE_FORMAT, RecommendationLLM
class RecommendationError(RuntimeError):
    """The recommendation could not be produced or validated."""
    

TIER1_AGENT_MAP = {
    'T1-01': {'name': 'Loan Cycle Orchestration Agent', 'capability': 'End-to-end cycle-time orchestration'},
    'T1-02': {'name': 'SLA Monitoring and Escalation Agent', 'capability': 'SLA monitoring, breach detection and escalation'},
    'T1-03': {'name': 'First-Time-Right Validation Agent', 'capability': 'Prevent errors and missing information before downstream processing'},
    'T1-04': {'name': 'Rework Prevention Agent', 'capability': 'Detect and prevent avoidable rework loops'},
    'T1-05': {'name': 'Workflow Handoff Optimization Agent', 'capability': 'Reduce unnecessary handoffs and improve routing'},
    'T1-06': {'name': 'Process Queue Optimization Agent', 'capability': 'Identify, prioritize and reduce process waiting'},
    'T1-07': {'name': 'Process Automation Agent', 'capability': 'Automate repetitive manual processing'},
    'T1-08': {'name': 'Exception Detection and Resolution Agent', 'capability': 'Detect, classify and resolve process exceptions'},
    'T1-09': {'name': 'Loan Cost Optimization Agent', 'capability': 'Identify avoidable processing effort and cost drivers'},
    'T1-10': {'name': 'Disbursement Acceleration Agent', 'capability': 'Remove delays between final approval and disbursement'},
    'T1-11': {'name': 'Stalled Case Recovery Agent', 'capability': 'Detect and recover stalled cases'},
    'T1-12': {'name': 'WIP Management Agent', 'capability': 'Prioritize and manage open work-in-progress'},
    'T1-13': {'name': 'Approval Readiness Agent', 'capability': 'Improve completeness and readiness for approval'},
    'T1-14': {'name': 'Application Intake and Capacity Agent', 'capability': 'Manage application intake and processing capacity'},
    'T1-15': {'name': 'Straight-Through Processing Enablement Agent', 'capability': 'Increase eligible straight-through processing'},
}

TIER2_AGENT_MAP = {
    'T2-01': {'name': 'Waiting Hotspot Optimization Agent', 'capability': 'Target the highest waiting activity'},
    'T2-02': {'name': 'Information Request Prevention Agent', 'capability': 'Reduce avoidable information requests'},
    'T2-03': {'name': 'Information Completeness Agent', 'capability': 'Prevent repeated information-request rounds'},
    'T2-04': {'name': 'RM Response Coordination Agent', 'capability': 'Prioritize and coordinate RM responses'},
    'T2-05': {'name': 'Document Request Optimization Agent', 'capability': 'Reduce avoidable document requests'},
    'T2-06': {'name': 'Customer Document Chase Agent', 'capability': 'Track outstanding customer documents'},
    'T2-07': {'name': 'Credit Reconstruction Agent', 'capability': 'Reduce reconstruction effort in credit review'},
    'T2-08': {'name': 'Communication Consolidation Agent', 'capability': 'Reduce fragmented email-based communication'},
    'T2-09': {'name': 'Financial Spreading Automation Agent', 'capability': 'Automate repetitive financial spreading work'},
    'T2-10': {'name': 'Financial Spreading Validation Agent', 'capability': 'Validate financial spreading and prevent rework'},
    'T2-11': {'name': 'Credit Review Productivity Agent', 'capability': 'Reduce excessive credit-review effort per case'},
    'T2-12': {'name': 'Approval Workflow Optimization Agent', 'capability': 'Optimize approval workflow cycle time'},
    'T2-13': {'name': 'Approval Layer Simplification Agent', 'capability': 'Identify unnecessary approval layers'},
    'T2-13-GM': {'name': 'GM Approval Queue Agent', 'capability': 'Prioritize and escalate waiting before GM approval'},
    'T2-13-CCO': {'name': 'CCO Approval Queue Agent', 'capability': 'Prioritize and escalate waiting before CCO approval'},
    'T2-13-CEO': {'name': 'CEO Approval Queue Agent', 'capability': 'Prioritize and escalate waiting before CEO approval'},
    'T2-13-CBO': {'name': 'CBO Approval Queue Agent', 'capability': 'Prioritize and escalate waiting before CBO approval'},
    'T2-13-Board': {'name': 'Board Approval Coordination Agent', 'capability': 'Prioritize and escalate waiting before Board approval'},
    'T2-14': {'name': 'Conditional Approval Optimization Agent', 'capability': 'Reduce avoidable conditional approvals'},
    'T2-15': {'name': 'Approval Conditions Tracking Agent', 'capability': 'Ensure approval conditions are tracked in-system'},
    'T2-16': {'name': 'Conditional Return Prevention Agent', 'capability': 'Prevent conditional approvals returning for rework'},
    'T2-17': {'name': 'Conditional Rework Reduction Agent', 'capability': 'Reduce effort lost to conditional rework'},
    'T2-18': {'name': 'Committee Pack Validation Agent', 'capability': 'Detect committee-pack version issues'},
    'T2-19': {'name': 'Collateral Data Standardization Agent', 'capability': 'Standardize free-text collateral records'},
    'T2-20': {'name': 'Collateral Reconciliation Agent', 'capability': 'Detect and reconcile collateral discrepancies'},
    'T2-21': {'name': 'Collateral Processing Automation Agent', 'capability': 'Automate manual collateral processing'},
    'T2-22': {'name': 'Guarantor Duplicate Detection Agent', 'capability': 'Detect duplicate guarantor records'},
    'T2-23': {'name': 'Existing Facility Update Agent', 'capability': 'Coordinate required facility updates'},
    'T2-24': {'name': 'Legal Review Routing Agent', 'capability': 'Route and prioritize legal review'},
    'T2-25': {'name': 'Legal Queue Optimization Agent', 'capability': 'Reduce waiting for Legal'},
    'T2-26': {'name': 'Contract Quality Validation Agent', 'capability': 'Detect contract issues before return'},
    'T2-27': {'name': 'Operations Clarification Agent', 'capability': 'Reduce unclear operations recommendations'},
    'T2-28': {'name': 'Booking Validation Agent', 'capability': 'Detect booking discrepancies before completion'},
    'T2-29': {'name': 'Data Re-keying Automation Agent', 'capability': 'Reduce duplicate data entry and re-keying'},
    'T2-30': {'name': 'Balance Limit Control Agent', 'capability': 'Resolve balance-limit indicator reset failures'},
    'T2-31': {'name': 'Post-Disbursement Update Agent', 'capability': 'Ensure required post-disbursement status updates'},
    'T2-32': {'name': 'Paperless Processing Agent', 'capability': 'Reduce physical and paper-based processing'},
    'T2-33': {'name': 'System Interaction Optimization Agent', 'capability': 'Reduce unnecessary systems touched per case'},
    'T2-34': {'name': 'Control Exception Prevention Agent', 'capability': 'Detect and prevent control exceptions'},
    'T2-35': {'name': 'Credit Workload Balancing Agent', 'capability': 'Balance workload across Credit Review makers'},
    'T2-36': {'name': 'Case Assignment Optimization Agent', 'capability': 'Optimize case assignment and reassignment'},
    'T2-37': {'name': 'Credit Queue Prioritization Agent', 'capability': 'Reduce queue time before credit analysis'},
    'T2-38': {'name': 'Rework Loop Detection Agent', 'capability': 'Identify and intervene on recurring rework loops'},
    'T2-39': {'name': 'Rework Loss Recovery Agent', 'capability': 'Reduce days lost to rework'},
    'T2-STG-1': {'name': 'Intake Cycle Optimization Agent', 'capability': 'Optimize Intake & Screening cycle time'},
    'T2-STG-2': {'name': 'Credit Review Cycle Optimization Agent', 'capability': 'Optimize Credit Review & Analysis cycle time'},
    'T2-STG-3': {'name': 'Monitoring and Approval Cycle Agent', 'capability': 'Optimize Monitoring & Approval cycle time'},
    'T2-STG-4': {'name': 'Operations Cycle Optimization Agent', 'capability': 'Optimize Operations Execution cycle time'},
    'T2-STG-5': {'name': 'Post-Disbursement Cycle Agent', 'capability': 'Optimize Post-Disbursement & Updates cycle time'},
}

GAP_AGENT_MAP = {
    'G01': {'name': 'Information Completeness Agent', 'capability': 'Improve information completeness before downstream review'},
    'G05': {'name': 'Customer Document Readiness Agent', 'capability': 'Improve customer document readiness'},
    'G06': {'name': 'Information Validation Agent', 'capability': 'Validate information before requesting clarification'},
    'G07': {'name': 'RM Information Coordination Agent', 'capability': 'Coordinate missing information and RM responses'},
    'G08': {'name': 'Communication Consolidation Agent', 'capability': 'Consolidate fragmented communication'},
    'G09': {'name': 'Data Reconstruction Automation Agent', 'capability': 'Reduce manual data reconstruction and re-keying'},
    'G10': {'name': 'Financial Data Quality Agent', 'capability': 'Improve financial spreading data quality'},
    'G11': {'name': 'Credit Review Effort Optimization Agent', 'capability': 'Reduce avoidable credit review effort'},
    'G12': {'name': 'Operations Decision Support Agent', 'capability': 'Improve clarity of operations recommendations'},
    'G13': {'name': 'Committee Pack Governance Agent', 'capability': 'Control committee pack versions'},
    'G14': {'name': 'Credit Workload Balancing Agent', 'capability': 'Balance Credit Review workload'},
    'G15': {'name': 'Approval Conditions Control Agent', 'capability': 'Track and control approval conditions'},
    'G16': {'name': 'Approval Queue Management Agent', 'capability': 'Prioritize approval queues'},
    'G17': {'name': 'Approval SLA Escalation Agent', 'capability': 'Escalate approval delays against SLA'},
    'G18': {'name': 'Paperless Process Agent', 'capability': 'Remove physical/paper processing steps'},
    'G19': {'name': 'Collateral Data Standardization Agent', 'capability': 'Standardize collateral master data'},
    'G20': {'name': 'Collateral Reconciliation Agent', 'capability': 'Reconcile collateral discrepancies'},
    'G21': {'name': 'Rework Loop Prevention Agent', 'capability': 'Prevent recurring rework loops and control exceptions'},
    'G22': {'name': 'Cross-System Data Consistency Agent', 'capability': 'Improve consistency across systems'},
    'G23': {'name': 'Document Request Optimization Agent', 'capability': 'Reduce avoidable document requests'},
    'G24': {'name': 'Committee Pack Version Control Agent', 'capability': 'Prevent committee pack version errors'},
    'G25': {'name': 'Legal Queue Management Agent', 'capability': 'Reduce Legal review waiting'},
    'G26': {'name': 'Contract Quality Agent', 'capability': 'Prevent contract returns'},
    'G27': {'name': 'Conditional Return Prevention Agent', 'capability': 'Prevent conditional approval returns'},
    'G28': {'name': 'Guarantor Duplicate Detection Agent', 'capability': 'Detect duplicate guarantors'},
    'G29': {'name': 'Guarantor Master Data Agent', 'capability': 'Improve guarantor master-data quality'},
    'G30': {'name': 'Paperless Processing Agent', 'capability': 'Reduce physical processing'},
    'G31': {'name': 'Booking Control Agent', 'capability': 'Prevent booking discrepancies'},
    'G32': {'name': 'Balance Limit Control Agent', 'capability': 'Prevent balance-limit reset failures'},
    'G33': {'name': 'Post-Disbursement Control Agent', 'capability': 'Prevent missed post-disbursement updates'},
}


TIER1_SHORT_NAME = {
    'T1-01': 'Cycle Time', 'T1-02': 'SLA', 'T1-03': 'First-Time-Right',
    'T1-04': 'Rework', 'T1-05': 'Handoff', 'T1-06': 'Waiting',
    'T1-07': 'Manual Effort', 'T1-08': 'Exception', 'T1-09': 'Cost',
    'T1-10': 'Disbursement TAT', 'T1-11': 'Stalled Case', 'T1-12': 'WIP',
    'T1-13': 'Approval Rate', 'T1-14': 'Application Volume', 'T1-15': 'STP',
}
MAX_ROUTED_AGENTS = 3


def _id_from(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ('kpi_id', 'tier1_kpi_id', 'id', 'gap_reference', 'code'):
            if value.get(key):
                return str(value[key]).strip()
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.split()[0]


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, (list, tuple, set)):
            for nested in _list(list(item)):
                if nested not in result:
                    result.append(nested)
            continue
        candidate = _id_from(item)
        if not candidate:
            continue
        for piece in candidate.replace(',', ';').split(';'):
            piece = piece.strip()
            if piece and piece not in result:
                result.append(piece)
    return result


def _tier1_ids(context: dict[str, Any]) -> list[str]:
    root_cause = context.get('root_cause') or {}
    tier1 = context.get('tier1_kpi') or {}
    value = tier1.get('kpi_id') if isinstance(tier1, dict) else tier1
    value = value or root_cause.get('tier1_kpi_id')
    return [item for item in _list(value) if item in TIER1_AGENT_MAP]


def _tier2_ids(context: dict[str, Any]) -> list[str]:
    root_cause = context.get('root_cause') or {}
    values = [context.get('tier2_kpis'), root_cause.get('tier2_kpi_ids')]
    return [item for item in _list(values) if item in TIER2_AGENT_MAP]


def _gap_ids(context: dict[str, Any]) -> list[str]:
    root_cause = context.get('root_cause') or {}
    values = [context.get('gaps'), root_cause.get('gap_references')]
    return [item for item in _list(values) if item in GAP_AGENT_MAP]


def _combo_agent(tier1_id: str, tier2_id: str) -> dict[str, str]:
    tier1_name = TIER1_SHORT_NAME.get(tier1_id, tier1_id)
    tier2 = TIER2_AGENT_MAP[tier2_id]
    base_name = tier2['name'].removesuffix(' Agent')
    return {
        'agent_id': f'{tier1_id.lower()}__{tier2_id.lower()}__diagnostic',
        'name': f'{tier1_name} {base_name} Agent',
        'purpose': f'Apply {tier2["capability"]} specifically to the {TIER1_AGENT_MAP[tier1_id]["capability"]} problem.',
        'source': 'tier1_tier2',
        'tier1_kpi_id': tier1_id,
        'tier2_kpi_id': tier2_id,
    }


def deterministic_agents(context: dict[str, Any]) -> dict[str, Any]:
    """Select a stable 2–3 agent plan from the exhaustive KPI catalog."""
    tier1_ids = _tier1_ids(context)
    tier2_ids = _tier2_ids(context)
    gap_ids = _gap_ids(context)
    if not tier1_ids:
        tier1_ids = ['T1-01']

    selected: list[dict[str, str]] = []
    primary_tier1 = tier1_ids[0]

    # The first diagnostic capability is made combination-specific so similar
    # root causes under different KPIs do not receive the same generic agent.
    if tier2_ids:
        selected.append(_combo_agent(primary_tier1, tier2_ids[0]))
    else:
        base = TIER1_AGENT_MAP[primary_tier1]
        selected.append({
            'agent_id': primary_tier1.lower().replace('-', '_'),
            'name': base['name'],
            'purpose': base['capability'],
            'source': 'tier1',
            'tier1_kpi_id': primary_tier1,
        })

    # A documented gap specialist is the second agent whenever available.
    if gap_ids:
        gap_id = gap_ids[0]
        gap = GAP_AGENT_MAP[gap_id]
        selected.append({
            'agent_id': gap_id.lower(),
            'name': gap['name'],
            'purpose': gap['capability'],
            'source': 'gap',
            'gap_id': gap_id,
        })

    # Add a second diagnostic capability when it adds distinct evidence.
    if len(selected) < MAX_ROUTED_AGENTS and len(tier2_ids) > 1:
        selected.append(_combo_agent(primary_tier1, tier2_ids[1]))

    if len(selected) < 2:
        selected.append({
            'agent_id': 'process_governance_agent',
            'name': 'Process Governance Agent',
            'purpose': 'Govern the remediation plan in a human-reviewable and auditable way.',
            'source': 'governance',
        })

    ordered: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in selected:
        if item['agent_id'] not in seen:
            ordered.append(item)
            seen.add(item['agent_id'])

    return {
        'routing_key': '|'.join(tier1_ids) + '|' + '+'.join(tier2_ids or ['NONE']) + '|' + '+'.join(gap_ids or ['NONE']),
        'tier1_kpi_ids': tier1_ids,
        'tier2_kpi_ids': tier2_ids,
        'gap_ids': gap_ids,
        'agents': ordered[:MAX_ROUTED_AGENTS],
        'deterministic': True,
    }


def build_fallback_plan(context: dict[str, Any], root_cause: dict[str, Any] | None = None) -> dict[str, Any]:
    root_cause = root_cause or context.get('root_cause') or {}
    routing = deterministic_agents(context)
    issue = str(root_cause.get('title') or root_cause.get('root_cause') or 'Process issue')
    agents: list[dict[str, Any]] = []
    for seq, item in enumerate(routing['agents'], start=1):
        agents.append({
            'agent_id': item['agent_id'],
            'name': item['name'],
            'purpose': item['purpose'],
            'problem_addressed': issue,
            'impact': 'Reduces the recurring pattern described by the KPI, Tier 2 diagnostics, and documented gaps.',
            'why_this_agent': f'{issue} is linked to {item["source"]} evidence and the diagnosed KPI pattern.',
            'action': ['Review the affected process step', 'Validate the relevant evidence', 'Prepare a governed recommendation for review'],
            'trigger': ['Trigger occurs when the diagnosed pattern is detected'],
            'systems': ['Event Log', 'Kinetic'],
            'human_in_loop': True,
            'human_in_loop_detail': 'A reviewer verifies the recommendation before any operational action.',
            'expected_outcome': 'A controlled remediation path consistent with the diagnosed root cause.',
            'sequence_order': seq,
        })
    return {
        'summary': f'Address {issue} using the deterministic Tier 1 / Tier 2 / gap routing for this root cause.',
        'representative_case_explanation': 'The recommendation is grounded in the deterministic KPI, Tier-2, and gap evidence for this root cause.',
        'agents': agents,
        'future_state': {
            'steps': [
                {'id': 'S1', 'name': 'Detect pattern', 'type': 'existing', 'description': 'Use Kinetic evidence and event-log diagnostics.'},
                {'id': 'S2', 'name': 'Run deterministic agent', 'type': 'ai_agent', 'description': 'Apply the selected remediation agent for the diagnosed pattern.'},
                {'id': 'S3', 'name': 'Human review', 'type': 'human', 'description': 'Validate before operational action or exception handling.'},
            ]
        },
    }


SYSTEM_PROMPT = """CORPORATE LOAN - AI FIX RECOMMENDATION AGENT

The deterministic routing list is authoritative. Describe how those exact selected agents address
the supplied root cause. Do not select, invent, rename, add, or remove agents. Never invent a KPI,
metric, case identifier, activity, or system. Systems must be selected from allowed_systems.
Ground actions and expected outcomes in the supplied KPI, Tier 2, gap, and root-cause evidence.
Customer communications, credit/compliance overrides, bank commitments, and formal approvals
must remain human-in-the-loop. A representative case may be absent; do not invent one.
Return one JSON object with exactly these top-level keys: summary, representative_case_explanation,
agents, future_state. Do not echo deterministic_agent_routing or add routing_key at the top level.
Each agents item must contain agent_id, name, purpose, problem_addressed, impact, why_this_agent,
action (array of strings), trigger (array of strings), systems (array of allowed strings),
human_in_loop (boolean), human_in_loop_detail, expected_outcome, and sequence_order (integer).
Use exactly one agent for every selected routing entry, in the same order. future_state must be an
object with a steps array; each step has id, name, type (existing, ai_agent, human, or decision),
and description. Include at least two steps. Return only this JSON object. Use concise, specific
banking-operations language."""


def build_payload(context: dict[str, Any]) -> str:
    root_cause = context.get('root_cause') or {}
    case = context.get('representative_case') or {}
    process = context.get('process_context') or {}
    allowed_event_fields = (
        'sequence', 'step_id', 'activity', 'stage', 'duration_minutes',
        'waiting_minutes', 'is_rework', 'is_exception', 'is_manual',
        'is_handoff', 'matched_root_cause',
    )
    key_events = [
        {key: event.get(key) for key in allowed_event_fields if key in event}
        for event in (case.get('key_events') or [])[:12]
        if isinstance(event, dict)
    ]
    evidence = {
        'tier_1_kpi': context.get('tier1_kpi'),
        'tier_2_kpis': context.get('tier2_kpis'),
        'documented_gaps': context.get('gaps'),
        'root_cause': {
            key: root_cause.get(key)
            for key in (
                'title', 'description', 'category', 'confidence', 'gap_references',
                'stages', 'step_ids', 'rework_loop_types', 'detail',
            )
            if root_cause.get(key) is not None
        },
        'impact_metrics': context.get('impact'),
        'representative_case': {
            'available': bool(case),
            'process_stage': case.get('process_stage'),
            'current_status': case.get('current_status'),
            'cycle_time_days': case.get('cycle_time'),
            'sla_days': case.get('sla'),
            'sla_breach_days': case.get('sla_breach'),
            'rework_loops': case.get('rework_loops'),
            'handoffs': case.get('handoffs'),
            'manual_touches': case.get('manual_touches'),
            'key_events': key_events,
        },
        'hotspot_activities': (process.get('hotspot_activities') or [])[:10],
        'manual_activities': (process.get('manual_activities') or [])[:10],
        'stage_profile': (process.get('stage_profile') or [])[:10],
        'allowed_systems': process.get('allowed_systems') or [],
        'deterministic_agent_routing': deterministic_agents(context),
    }
    return json.dumps(evidence, separators=(',', ':'), default=str)


def _call_model(client: Any, payload: str, correction: str | None) -> dict[str, Any]:
    user_input = payload if not correction else (
        f'{payload}\n\nYour previous answer was rejected: {correction}\n'
        'Return a corrected answer that follows the schema and deterministic routing rules.'
    )
    if client is None:
        return generate_json(SYSTEM_PROMPT, user_input, LLM_RESPONSE_FORMAT)

    response = client.responses.create(
        model=settings.AZURE_OPENAI_DEPLOYMENT,
        instructions=SYSTEM_PROMPT,
        input=[{'role': 'user', 'content': user_input}],
        text={'format': LLM_RESPONSE_FORMAT},
        max_output_tokens=8000,
    )
    if getattr(response, 'status', None) == 'incomplete':
        raise RecommendationError('The model returned an incomplete response.')
    for item in getattr(response, 'output', None) or []:
        for block in getattr(item, 'content', None) or []:
            if getattr(block, 'type', None) == 'refusal':
                raise RecommendationError('The model declined the recommendation request.')
    output_text = getattr(response, 'output_text', '') or ''
    parsed = json.loads(output_text)
    if not isinstance(parsed, dict):
        raise ValueError('The model response must be a JSON object.')
    return parsed


def _validate_agent_names(plan: RecommendationLLM, routing: dict[str, Any]) -> None:
    expected = routing.get('agents') or []
    if len(plan.agents) != len(expected):
        raise ValueError('The model returned a different number of agents than deterministic routing.')
    for generated, selected in zip(plan.agents, expected):
        generated.agent_id = selected['agent_id']
        generated.name = selected['name']
        generated.sequence_order = expected.index(selected) + 1


def _invalid_systems(plan: RecommendationLLM, allowed_systems: list[str]) -> list[str]:
    allowed = {system.strip().casefold() for system in allowed_systems if system.strip()}
    return list(dict.fromkeys(
        system
        for agent in plan.agents
        for system in agent.systems
        if system.strip().casefold() not in allowed
    ))


def _strip_invalid_systems(plan: RecommendationLLM, allowed_systems: list[str]) -> None:
    allowed = {system.strip().casefold() for system in allowed_systems if system.strip()}
    for agent in plan.agents:
        agent.systems = [system for system in agent.systems if system.strip().casefold() in allowed]


def generate(context: dict[str, Any], client: Any = None) -> tuple[RecommendationLLM, str]:
    """Generate a validated plan for the exact deterministically selected agents."""
    routing = deterministic_agents(context)
    payload = build_payload(context)
    process = context.get('process_context') or {}
    allowed_systems = process.get('allowed_systems') or []
    correction = None
    last_error = None

    for attempt in (1, 2):
        try:
            plan = RecommendationLLM.model_validate(_call_model(client, payload, correction))
            _validate_agent_names(plan, routing)
            offenders = _invalid_systems(plan, allowed_systems)
            if offenders:
                if attempt == 1:
                    raise ValueError(f'Unobserved systems were returned: {", ".join(offenders)}.')
                _strip_invalid_systems(plan, allowed_systems)
            return plan, settings.AZURE_OPENAI_DEPLOYMENT
        except RecommendationError:
            raise
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            last_error = str(error)
            correction = last_error[:1500]

    raise RecommendationError(f'The recommendation could not be validated: {last_error}')
