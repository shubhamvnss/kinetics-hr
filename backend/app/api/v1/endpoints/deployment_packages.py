"""Loan deployment package generation, persistence, and human approval workflow."""
import hashlib
import json
import secrets
import smtplib
import ssl
import time
import urllib.error
import urllib.request
import re
from urllib.parse import quote
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.azure_openai import generate_json
from app.core.config import settings
from app.core.database import get_connection

router = APIRouter()

PACKAGE_CACHE_VERSION = settings.CACHE_VERSION


class PackageContext(BaseModel):
    kpi: dict[str, Any] = Field(default_factory=dict)
    root_cause: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Any] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    recommendation: dict[str, Any] = Field(default_factory=dict)
    process_flow: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    token: str
    decision: str
    reviewer: str | None = None
    comment: str | None = None


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return [] if value is None else [value]


def _implementation_code(agent: dict[str, Any]) -> str:
    agent_id = str(agent.get("agent_id") or agent.get("id") or "loan_agent")
    purpose = str(agent.get("purpose") or agent.get("responsibility") or "Process loan evidence.")
    actions = _list(agent.get("action") or agent.get("actions"))
    action_lines = "\n".join(f"    # {action}" for action in actions) or "    # Implement the governed agent action."
    return (
        '"""Generated governed-agent scaffold for the corporate-loan process."""\n\n'
        f"AGENT_ID = {agent_id!r}\n"
        f"PURPOSE = {purpose!r}\n\n"
        "def handle(event: dict) -> dict:\n"
        '    """Return a reviewable recommendation; never commit a credit decision."""\n'
        f"{action_lines}\n"
        "    return {\n"
        "        \"agent_id\": AGENT_ID,\n"
        "        \"status\": \"ready_for_review\",\n"
        "        \"event\": event,\n"
        "    }\n"
    )


def _recommendation_agents(request: PackageContext) -> list[dict[str, Any]]:
    sources = [
        request.recommendation.get("recommendation_plan"),
        request.recommendation,
        request.context.get("recommendation_plan"),
        request.context.get("recommendation"),
        request.root_cause.get("recommendation_plan"),
        request.root_cause.get("recommended_agents"),
        request.root_cause.get("agent_recommendations"),
    ]
    for source in sources:
        if isinstance(source, dict):
            agents = _list(source.get("agents"))
        else:
            agents = _list(source)
        if agents:
            normalized = [agent for agent in agents if isinstance(agent, dict)]
            if normalized:
                return normalized

    # Accept the same recommendation object whether it arrives directly,
    # nested under a compatibility wrapper, or alongside the plan. This keeps
    # older cached frontend responses interoperable without fabricating agents.
    pending = [request.recommendation, request.context, request.root_cause]
    visited: set[int] = set()
    while pending:
        value = pending.pop()
        if not isinstance(value, dict) or id(value) in visited:
            continue
        visited.add(id(value))
        direct = value.get("agents")
        if isinstance(direct, list):
            normalized = [agent for agent in direct if isinstance(agent, dict)]
            if normalized:
                return normalized
        for key in ("recommendation", "recommendation_plan", "recommended_agents", "agent_recommendations"):
            nested = value.get(key)
            if isinstance(nested, dict):
                pending.append(nested)
            elif isinstance(nested, list):
                normalized = [agent for agent in nested if isinstance(agent, dict)]
                if normalized:
                    return normalized
    return []


def _process_systems(request: PackageContext, agents: list[dict[str, Any]], event_systems: list[str] | None = None) -> list[str]:
    # Always prefer systems inferred from the event log.
    systems = event_systems or _list(request.process_flow.get("systems"))
    if not systems:
        systems = [system for agent in agents for system in _list(agent.get("systems"))]
    seen: set[str] = set()
    return [
        str(system)
        for system in systems
        if str(system).strip() and not (str(system).lower() in seen or seen.add(str(system).lower()))
    ]


def _event_records(case: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("event_log", "events", "timeline"):
        if isinstance(case.get(key), list):
            return [event for event in case[key] if isinstance(event, dict)]
    return []


def _event_context(request: PackageContext) -> dict[str, Any]:
    cases = _list(request.context.get("impacted_cases"))
    events = [event for case in cases if isinstance(case, dict) for event in _event_records(case)]
    events.extend(event for event in _list(request.context.get("event_log")) if isinstance(event, dict))

    def _system_name(event: dict[str, Any]):
        for key in ("system_used", "system_name", "source_system", "sourceSystem"):
            value = event.get(key)
            if not value:
                continue
            name = str(value).strip()
            if not name:
                continue
            lower = name.lower()
            if "kinetic" in lower or "event log" in lower or lower in {"event_log", "eventlog"}:
                return None
            base = re.split(r"\s*\(", name, maxsplit=1)[0].strip()
            return base or name
        return None

    systems = sorted(
        {
            system
            for event in events
            for system in [_system_name(event)]
            if system
        }
    )
    return {
        "systems": systems,
        "event_count": len(events),
        "event_types": sorted({str(event["event_type"]).strip() for event in events if event.get("event_type")}),
        "stages": sorted({str(event["stage"]).strip() for event in events if event.get("stage")}),
        "activities": sorted(
            {
                str(
                    (event.get("event_name") or event.get("activity") or event.get("step_name"))
                ).strip()
                for event in events
                if event.get("event_name") or event.get("activity") or event.get("step_name")
            }
        ),
    }


def _slug(value: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "agent")).strip("-").lower() or "agent"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z0-9_.:/@+-]+", text) else json.dumps(text)


def _agent_yaml(agent: dict[str, Any], event_systems: list[str]) -> str:
    requested = {str(item).strip().lower() for item in _list(agent.get("systems")) if str(item).strip()}
    integrations = [system for system in event_systems if system.lower() in requested]
    lines = [
        "apiVersion: kinetic.deloitte.com/v1", "kind: AgentDeployment", "metadata:",
        f"  name: {_yaml_scalar(_slug(agent.get('agent_id') or agent.get('id')))}",
        f"  displayName: {_yaml_scalar(agent.get('name'))}", "spec:",
        f"  agentId: {_yaml_scalar(agent.get('agent_id') or agent.get('id'))}",
        f"  sequenceOrder: {_yaml_scalar(agent.get('sequence_order'))}",
        "  source: deterministic-recommendation-agent",
        f"  purpose: {_yaml_scalar(agent.get('purpose') or agent.get('responsibility'))}",
        f"  problemAddressed: {_yaml_scalar(agent.get('problem_addressed'))}",
        f"  expectedOutcome: {_yaml_scalar(agent.get('expected_outcome'))}",
        "  humanInLoop:", f"    required: {_yaml_scalar(agent.get('human_in_loop'))}",
        f"    detail: {_yaml_scalar(agent.get('human_in_loop_detail'))}", "  trigger:",
    ]
    lines.extend(f"    - {_yaml_scalar(item)}" for item in _list(agent.get("trigger") or agent.get("triggers")))
    lines.append("  actions:")
    lines.extend(f"    - {_yaml_scalar(item)}" for item in _list(agent.get("action") or agent.get("actions")))
    lines.append("  systems:")
    lines.extend(f"    - {_yaml_scalar(item)}" for item in integrations) if integrations else lines.append("    []")
    lines.extend([
        '  integrationRule: "Only systems present in event-log records may be integrated."',
        "  observability:", '    routingSource: "Tier 1 KPI + Tier 2 KPI + Gap"',
        "    representativeCaseOptional: true",
    ])
    return "\n".join(lines) + "\n"


def _deployment_yaml_files(
    request: PackageContext,
    agents: list[dict[str, Any]],
    event: dict[str, Any],
    environment: str,
    feature_flag: str,
) -> list[dict[str, Any]]:
    """Deterministic deployment, integration, monitoring, and rollback YAML files."""
    systems = event.get("systems") or []
    kpi = request.kpi or {}
    kpi_name = str(kpi.get("kpi_name") or "Tier 1 KPI")
    kpi_id = kpi.get("kpi_id")
    agent_ids = [str(agent.get("agent_id") or agent.get("id")) for agent in agents if isinstance(agent, dict)]
    agent_ids = [value for value in agent_ids if value]

    deployment_yaml_lines = [
        "apiVersion: apps/v1",
        "kind: Deployment",
        "metadata:",
        "  name: corporate-loan-agents",
        "  labels:",
        "    app: corporate-loan-agents",
        f"    kinetic.deloitte.com/feature: {_yaml_scalar(feature_flag or 'corporate_loan_agents')}",
        "spec:",
        "  replicas: 1",
        "  selector:",
        "    matchLabels:",
        "      app: corporate-loan-agents",
        "  template:",
        "    metadata:",
        "      labels:",
        "        app: corporate-loan-agents",
        f"        kinetic.deloitte.com/feature: {_yaml_scalar(feature_flag or 'corporate_loan_agents')}",
        "    spec:",
        "      containers:",
        "        - name: loan-agents",
        "          image: ${AI_AGENT_IMAGE}",
        "          env:",
        "            - name: ENVIRONMENT",
        f"              value: {_yaml_scalar(environment or 'staged')}",
        "            - name: FEATURE_FLAG",
        f"              value: {_yaml_scalar(feature_flag or 'corporate_loan_agents')}",
        "            - name: AGENT_IDS",
        f"              value: {_yaml_scalar(', '.join(agent_ids) or 'to-be-confirmed')}",
        "          # Runtime resources and limits to be confirmed during technical design.",
        "  # Deployment footprint and scaling to be confirmed during technical design.",
    ]

    integration_yaml_lines = [
        "apiVersion: kinetic.deloitte.com/v1",
        "kind: IntegrationConfig",
        "metadata:",
        "  name: corporate-loan-agents-integrations",
        "spec:",
        "  # Systems present in the event log; integration/API details to be confirmed with the client.",
        "  systems:",
    ]
    if systems:
        for system in systems:
            integration_yaml_lines.extend(
                [
                    f"    - name: {_yaml_scalar(system)}",
                    "      mode: read",
                    "      purpose: Read process evidence and publish governed outputs.",
                ]
            )
    else:
        integration_yaml_lines.append("    []")
    integration_yaml_lines.extend(
        [
            "  observability:",
            '    routingSource: "Tier 1 KPI + Tier 2 KPI + gap context"',
            "    representativeCaseOptional: true",
        ]
    )

    monitoring_yaml_lines = [
        "apiVersion: kinetic.deloitte.com/v1",
        "kind: MonitoringConfig",
        "metadata:",
        "  name: corporate-loan-agents-monitoring",
        "spec:",
        "  kpis:",
        "    - name: " + _yaml_scalar(kpi_name),
    ]
    if kpi_id:
        monitoring_yaml_lines.append("      id: " + _yaml_scalar(kpi_id))
    monitoring_yaml_lines.extend(
        [
            "  metrics:",
            "    - name: kpi_variance",
            "      description: Monitor variance between current KPI value and target.",
            "    - name: exception_rate",
            "      description: Track rate of routed exceptions and human escalations.",
            "  # Alert thresholds and SLOs to be confirmed during technical design.",
        ]
    )

    rollback_yaml_lines = [
        "apiVersion: kinetic.deloitte.com/v1",
        "kind: RollbackPlan",
        "metadata:",
        "  name: corporate-loan-agents-rollback",
        "spec:",
        "  steps:",
        f"    - description: Disable feature flag {_yaml_scalar(feature_flag or 'corporate_loan_agents')} for governed agents.",
        "    - description: Route new loan cases back to the baseline manual workflow.",
        "    - description: Remove or pause orchestrator deployment after approval.",
    ]

    return [
        {
            "path": "deployment.yaml",
            "kind": "deployment",
            "purpose": "Deployment descriptor for the governed agent service.",
            "content": "\n".join(deployment_yaml_lines) + "\n",
        },
        {
            "path": "integration.yaml",
            "kind": "integration",
            "purpose": "Logical integration mapping for event-log systems and governed agents.",
            "content": "\n".join(integration_yaml_lines) + "\n",
        },
        {
            "path": "monitoring.yaml",
            "kind": "monitoring",
            "purpose": "Monitoring expectations for KPI variance, exception rate and audit coverage.",
            "content": "\n".join(monitoring_yaml_lines) + "\n",
        },
        {
            "path": "rollback.yaml",
            "kind": "rollback",
            "purpose": "Rollback playbook for disabling the governed agent feature flag and reverting to manual.",
            "content": "\n".join(rollback_yaml_lines) + "\n",
        },
    ]


def _markdown_package(request: PackageContext, content: dict[str, Any], agents: list[dict[str, Any]],
                      event: dict[str, Any]) -> str:
    cause = request.root_cause
    kpi = request.kpi
    tier2_lines = [f"- {item}" for item in _list(request.context.get("tier2_kpis"))] or ["- Not specified in source context"]
    gap_lines = [f"- {item}" for item in _list(cause.get("gap_references") or request.context.get("gaps"))] or ["- Not specified in source context"]
    system_lines = [f"- {item}" for item in event["systems"]] or ["- No source systems identified in supplied event-log records."]
    lines = [
        "# Transformation Deployment Package",
        "",
        "## SECTION 1: Executive summary",
        "",
        f"**Objective:** {request.context.get('objective') or cause.get('description') or cause.get('root_cause_summary') or cause.get('root_cause') or 'Address the diagnosed KPI/root-cause condition.'}",
        f"**Tier 1 KPI:** {kpi.get('kpi_id', '')} - {kpi.get('kpi_name', '')}",
        f"**Current value:** {kpi.get('current_value', 'Not specified')}",
        f"**Target:** {kpi.get('target', 'Not specified')}",
        f"**Health:** {kpi.get('health', 'Not specified')}",
        f"**Recommended agents:** {len(agents)}",
        "",
        "### Tier 2 KPIs",
        *tier2_lines,
        "",
        "### Gap references",
        *gap_lines,
        "",
        "## SECTION 2: Transformation overview",
        "",
        f"**Goals:** {request.context.get('goals') or cause.get('description') or cause.get('root_cause_summary') or 'Address the diagnosed transformation opportunity.'}",
        f"**Scope:** {request.context.get('scope') or 'Bounded by supplied KPI, evidence, recommended agents, and integrations.'}",
        f"**Constraints:** {request.context.get('constraints') or 'No unsupported agents or systems are introduced.'}",
        "",
        "## SECTION 3: AI Agents",
    ]
    for index, agent in enumerate(agents, 1):
        lines.extend(["", f"### {index}. {agent.get('name')}", f"- **Agent ID:** {agent.get('agent_id') or agent.get('id')}",
                      f"- **Purpose:** {agent.get('purpose') or agent.get('responsibility')}",
                      f"- **Problem addressed:** {agent.get('problem_addressed')}",
                      f"- **Impact:** {agent.get('impact')}", f"- **Why this agent:** {agent.get('why_this_agent')}",
                      f"- **Expected outcome:** {agent.get('expected_outcome')}",
                      f"- **Sequence order:** {agent.get('sequence_order')}"])
    lines.extend(["", "## SECTION 4: Business rules"])
    for index, agent in enumerate(agents, 1):
        lines.extend(["", f"### BR-{index:02d} — {agent.get('name')}",
                      f"- Trigger: {_list(agent.get('trigger') or agent.get('triggers'))}",
                      f"- Actions: {_list(agent.get('action') or agent.get('actions'))}",
                      f"- Human-in-the-loop: {agent.get('human_in_loop')} — {agent.get('human_in_loop_detail')}",
                      f"- Expected outcome: {agent.get('expected_outcome')}",
                      f"- Rationale: {agent.get('why_this_agent')}"])
    lines.extend(["", "## SECTION 5: Integration", "",
                  "**Systems present in event log:**",
    *system_lines,
                  f"- Event records inspected: {event['event_count']}",
                  f"- Event types: {event['event_types'] or ['Not specified']}",
                  f"- Stages: {event['stages'] or ['Not specified']}",
                  "",
                  "## SECTION 6: Technical architecture",
                  f"- Systems integrated: {event['systems'] or ['None identified']}",
                  f"- Recommended agents: {[agent.get('agent_id') or agent.get('id') for agent in agents]}",
                  "- Runtime flow: event evidence → KPI/gap context → deterministic agents → supported integrations → outcome",
                  "",
                  "## SECTION 7: Agent code base",
                  *[f"- `agents/{_slug(agent.get('agent_id') or agent.get('id'))}/agent.py`" for agent in agents],
                  *[f"- `agents/{_slug(agent.get('agent_id') or agent.get('id'))}/config.yaml`" for agent in agents],
                  *[f"- `tests/{_slug(agent.get('agent_id') or agent.get('id'))}/test_agent.py`" for agent in agents],
                  "",
                  "## SECTION 8: Deployment YAML files",
                  *[f"- `agents/{_slug(agent.get('agent_id') or agent.get('id'))}.yaml` — {agent.get('name')}" for agent in agents],
                  "",
                  "## Approval",
                  "- Approval is required before deployment.",
                  "- Approval can be reset and requested again.",
                  ])
    return "\n".join(str(line) for line in lines)


def _artifacts(request: PackageContext, content: dict[str, Any]) -> dict[str, Any]:
    agents = _recommendation_agents(request)
    if not agents:
        raise HTTPException(422, "No recommended agents supplied; deployment package generation will not invent agents.")
    event = _event_context(request)

    deployment_config = content.get("deployment_config") or {}
    yaml_files: dict[str, str] = {}

    files = deployment_config.get("files") if isinstance(deployment_config, dict) else None
    if isinstance(files, list):
        for file in files:
            if not isinstance(file, dict):
                continue
            path = str(file.get("path") or "").strip()
            text = file.get("content") or file.get("yaml") or file.get("value")
            if not path or not isinstance(text, str):
                continue
            if not path.endswith(".yaml"):
                continue
            yaml_files[path] = text

    if not yaml_files:
        yaml_files = {
            _slug(agent.get("agent_id") or agent.get("id")) + ".yaml": _agent_yaml(agent, event["systems"])
            for agent in agents
        }

    return {
        "markdown": _markdown_package(request, content, agents, event),
        "yaml_files": yaml_files,
        "deployment_manifest": {
            "recommended_agents": [
                {"agent_id": agent.get("agent_id") or agent.get("id"), "name": agent.get("name")} for agent in agents
            ],
            "event_log_systems": event["systems"],
            "yaml_files": sorted(yaml_files),
        },
    }


DEPLOYMENT_SUMMARY_RESPONSE_FORMAT = {
    "name": "deployment_package_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "executive_summary_text": {"type": "string"},
            "transformation_overview_text": {"type": "string"},
        },
        "required": ["executive_summary_text", "transformation_overview_text"],
        "additionalProperties": False,
    },
}

DEPLOYMENT_SUMMARY_PROMPT = """You are a senior transformation lead writing for a corporate loan \"Fix with AI\" deployment package.

Use ONLY the provided JSON context (KPI, root_cause, recommendation, future_state, agents, event_context, runtime, executive_summary and transformation_overview) so that wording stays consistent with the Fix with AI and deployment package screens.

Write:
1) executive_summary_text – one paragraph under 100 words focused on business problem, KPI variance, diagnosed bottleneck stage, governed agents and expected business impact.
2) transformation_overview_text – a different paragraph under 100 words focused on process scope, current vs future state, how the agents operate across systems and stages, and governance.

The two paragraphs MUST NOT be identical or near duplicates."""


def _content(request: PackageContext) -> dict[str, Any]:
    kpi, cause = request.kpi, request.root_cause
    issue = str(cause.get("root_cause") or "inconsistent loan-processing controls")
    summary = str(cause.get("root_cause_summary") or "Evidence indicates a repeatable process gap.")
    recommendation = request.recommendation
    agents = _recommendation_agents(request)
    event = _event_context(request)
    systems = _process_systems(request, agents, event.get("systems") or [])
    future_state = recommendation.get("future_state") or {}
    environment = "staged"
    feature_flag = "corporate_loan_agents"
    deployment_files = _deployment_yaml_files(request, agents, event, environment, feature_flag)

    generated_files: list[dict[str, Any]] = []
    agent_ids: list[str] = []
    for index, agent in enumerate(agents):
        agent_id = str(agent.get("agent_id") or agent.get("id") or f"loan-agent-{index + 1}")
        agent_ids.append(agent_id)
        slug = _slug(agent_id)
        purpose = agent.get("purpose") or agent.get("responsibility", "")

        # Core governed agent implementation stub.
        generated_files.append(
            {
                "path": f"agents/{agent_id}.py",
                "language": "python",
                "purpose": purpose or "Governed agent implementation stub.",
                "code": _implementation_code(agent),
            }
        )

        # Per-agent configuration file.
        config_lines = [
            "agent:",
            f"  id: {_yaml_scalar(agent_id)}",
            f"  name: {_yaml_scalar(agent.get('name'))}",
            f"  mode: {_yaml_scalar(agent.get('mode') or 'governed')}",
            "  systems:",
        ]
        for system in _list(agent.get("systems")):
            config_lines.append(f"    - {_yaml_scalar(system)}")
        config_code = "\n".join(config_lines) + "\n"
        generated_files.append(
            {
                "path": f"agents/{slug}/config.yaml",
                "language": "yaml",
                "purpose": "Runtime and system bindings for this governed agent.",
                "code": config_code,
            }
        )

        # Minimal pytest scaffold for this agent.
        test_code = (
            '"""Test scaffold for this governed agent."""\n\n'
            "def test_agent_sanity():\n"
            "    assert True\n"
        )
        generated_files.append(
            {
                "path": f"tests/{slug}/test_agent.py",
                "language": "python",
                "purpose": "Minimal pytest scaffold for this agent.",
                "code": test_code,
            }
        )

        # Deployment YAML for this agent.
        generated_files.append(
            {
                "path": f"deploy/agents/{slug}.yaml",
                "language": "yaml",
                "purpose": "Kinetic deployment descriptor for this governed agent.",
                "code": _agent_yaml(agent, event["systems"]),
            }
        )

    if agent_ids:
        orchestrator_code = (
            '"""Orchestrator entrypoint for governed loan agents."""\n\n'
            f"AGENT_IDS = {agent_ids!r}\n\n"
            "def orchestrate(event: dict) -> dict:\n"
            '    """Route events to the appropriate governed agents."""\n'
            "    # Implement deterministic routing based on KPI, gaps, and systems.\n"
            "    return {\"status\": \"routed\", \"event\": event}\n"
        )
        generated_files.append(
            {
                "path": "agents/orchestrator.py",
                "language": "python",
                "purpose": "Entry-point orchestrator for governed loan agents.",
                "code": orchestrator_code,
            }
        )

        service_code = (
            '"""FastAPI service exposing governed loan agents as a reviewable API."""\n\n'
            "from typing import Any\n\n"
            "from fastapi import FastAPI\n\n"
            "from agents.orchestrator import orchestrate\n\n"
            "app = FastAPI(title='Corporate Loan Agent Service')\n\n"
            "@app.post('/loan-agents/route')\n"
            "async def route_event(event: dict[str, Any]) -> dict[str, Any]:\n"
            '    """Route a process event into the governed orchestrator and return a recommendation."""\n'
            "    return orchestrate(event)\n"
        )
        generated_files.append(
            {
                "path": "app/main.py",
                "language": "python",
                "purpose": "FastAPI entrypoint exposing a governed loan-agent route.",
                "code": service_code,
            }
        )

    integrations = [
        {"system": system, "purpose": "Read process evidence and publish governed outputs.",
         "data_flow": "Process event → agent context → recommendation/audit record",
         "agents": [agent.get("name") for agent in agents if system in _list(agent.get("systems"))]}
        for system in systems
    ]

    business_rules = []
    for index, agent in enumerate(agents, 1):
        triggers = _list(agent.get("trigger") or agent.get("triggers"))
        actions = _list(agent.get("action") or agent.get("actions"))
        business_rules.append(
            {
                "rule_id": f"BR-{index:02d}",
                "agent_id": agent.get("agent_id") or agent.get("id"),
                "name": agent.get("name"),
                "if": triggers or ["Threshold to be configured based on business policy."],
                "then": actions or ["Prepare a governed recommendation for review."],
                "approval_required": agent.get("human_in_loop") or "Required",
                "human_in_loop_detail": agent.get("human_in_loop_detail"),
                "exception_handling": "Escalate policy breaches, missing data, or low-confidence recommendations to the configured human reviewers.",
            }
        )
    if not business_rules:
        business_rules.append(
            {
                "rule_id": "BR-01",
                "name": "Governed recommendation",
                "if": [
                    "A governed AI agent produces a recommendation that affects credit, disbursement, or customer communication."
                ],
                "then": [
                    "Prepare a recommendation for human review without committing any authoritative system changes."
                ],
                "approval_required": "Required",
                "exception_handling": "Escalate to human reviewers when required evidence is missing or business rules are violated.",
            }
        )

    kpi_name = str(kpi.get("kpi_name") or "Corporate loan process performance")
    current_value = kpi.get("current_value")
    target_value = kpi.get("target")
    variance_pct = kpi.get("variance_vs_target_pct")

    named_agents = [agent for agent in agents if agent.get("name")]
    agent_names = [agent.get("name") for agent in named_agents]

    if current_value is not None and target_value is not None:
        metrics_attention = (
            f"End-to-end {kpi_name} is {current_value} against a target of {target_value}"
        )
        if variance_pct is not None:
            metrics_attention += f", a variance of {variance_pct}% vs target."
        else:
            metrics_attention += "."
    else:
        metrics_attention = (
            f"{kpi_name} is {kpi.get('current_value', '—')} against a target of "
            f"{kpi.get('target', '—')}, variance "
            f"{kpi.get('variance_vs_target_pct', '—')}% vs target."
        )

    if agent_names:
        if len(agent_names) == 1:
            agents_overview_exec = (
                f"One governed agent is proposed: {agent_names[0]}. "
                "This agent is designed to stabilise the diagnosed bottleneck while keeping humans in control of credit decisions."
            )
        else:
            if len(agent_names) == 2:
                name_list = " and ".join(agent_names)
            else:
                name_list = ", ".join(agent_names[:-1]) + f" and {agent_names[-1]}"
            agents_overview_exec = (
                f"{len(agent_names)} governed agents are proposed: {name_list}. "
                "Together they orchestrate the bottleneck stages, reduce rework and maintain human approval gates."
            )
        agents_overview_transformation = "Agents governing this transformation: " + ", ".join(agent_names)
    else:
        agents_overview_exec = "Governed agents to be confirmed during detailed design."
        agents_overview_transformation = agents_overview_exec

    current_state_text = summary
    if current_value is not None and target_value is not None:
        narrative_suffix = (
            f" End-to-end {kpi_name} currently runs at {current_value} against a target of {target_value}"
        )
        if variance_pct is not None:
            narrative_suffix += f", a variance of {variance_pct}% vs target."
        else:
            narrative_suffix += "."
        current_state_text = f"{summary} {narrative_suffix}".strip()

    target_state_text = (
        future_state.get("narrative")
        or future_state.get("summary")
        or "Evidence-backed orchestration with governed agents, human-in-the-loop approvals and observable controls across critical stages."
    )

    content: dict[str, Any] = {
        "executive_summary": {
            "business_problem": issue,
            "kpi": kpi_name,
            "metrics": {
                "current": current_value if current_value is not None else kpi.get("current_value", "—"),
                "target": target_value if target_value is not None else kpi.get("target", "—"),
                "variance": variance_pct if variance_pct is not None else kpi.get("variance_vs_target_pct", "—"),
            },
            "metrics_attention": metrics_attention,
            "agents_overview": agents_overview_exec,
            "recommendation": (
                f"Deploy {len(agent_names) or 'governed'} agent(s) to address {issue.lower()} "
                "under human-approved, deterministic controls."
            ),
            "expected_benefits": [
                "Reduced rework and manual handoffs",
                "Faster, more predictable loan turnaround time",
                "Audit-ready, explainable decisions at each control point",
            ],
        },
        "transformation_overview": {
            "process": "Corporate Loan Origination",
            "current_state": current_state_text,
            "target_state": target_state_text,
            "scope": "Application intake, credit review, exception handling, and disbursement readiness",
            "agents_overview": agents_overview_transformation,
        },
        "agents": agents,
        "business_rules": business_rules,
        "integrations": integrations,
        "architecture": {"pattern": "Event-driven orchestration", "components": systems},
        "codebase": {
            "repository": "kinetic-app",
            "entrypoint": "app/main.py",
            "deployment_unit": "Containerized FastAPI service",
            "generated_files": generated_files,
            "source_agents": [agent.get("agent_id") for agent in agents],
        },
        "deployment_config": {
            "environment": environment,
            "feature_flag": feature_flag,
            "files": deployment_files,
            "notes": [
                "Deployment YAML descriptors are templates for review; exact runtime parameters are to be confirmed during technical design.",
            ],
            "secrets": "Injected at runtime; never stored in this package.",
        },
        "approval_workflow": {"status": "DRAFT", "required_roles": ["Credit Risk", "Operations", "Technology"], "approval_required": True,
                              "approval_status": "NOT_REQUESTED", "re_request_supported": True},
        "runtime": _list(future_state.get("steps")) or ["Ingest KPI and evidence", "Generate recommendation", "Request human approval", "Record outcome"],
        "monitoring_testing_rollback": {"monitoring": ["KPI variance", "recommendation confidence", "exception rate"], "testing": ["Evidence completeness", "policy regression", "approval-gate tests"], "rollback": "Disable feature flag and return to manual workflow"},
    }

    try:
        llm_input = {
            "kpi": kpi,
            "root_cause": cause,
            "summary": summary,
            "issue": issue,
            "agents": agents,
            "future_state": future_state,
            "event_context": event,
            "runtime_steps": content["runtime"],
            "executive_summary": content["executive_summary"],
            "transformation_overview": content["transformation_overview"],
            "fix_with_ai_context": request.context,
            "deployment_package": content,
        }
        llm_result = generate_json(
            DEPLOYMENT_SUMMARY_PROMPT,
            json.dumps(llm_input, default=str),
            DEPLOYMENT_SUMMARY_RESPONSE_FORMAT,
        )
        exec_text = (llm_result or {}).get("executive_summary_text")
        overview_text = (llm_result or {}).get("transformation_overview_text")
        if isinstance(exec_text, str) and exec_text.strip():
            content["executive_summary"]["narrative"] = exec_text.strip()
        if isinstance(overview_text, str) and overview_text.strip():
            content["transformation_overview"]["narrative"] = overview_text.strip()
    except Exception:
        # If the LLM is unavailable or fails, fall back to deterministic text.
        pass

    return content


BUSINESS_RULES_PROMPT = """ROLE
You are a Business Rule Designer for an enterprise AI agent operating within a controlled business process.
Your task is to convert the provided Agent Trigger, Actions, Expected Outcome, and Available Systems into a clear, deterministic and implementation-ready set of Business Rules.
The business rules must explain when the agent should act, what conditions it should evaluate, what action it should perform, which system it should use, when it should stop, and when it should escalate to a human.
INPUT
You will receive the following context:
Agent Name
{agent_name}
Agent Purpose / Capability
{agent_capability}
Trigger
{trigger}
Actions
{actions}
Expected Outcome
{expected_outcome}
Available Systems
{systems}
Business Context
{business_context}
Human-in-the-Loop Requirement
{human_in_loop}
OBJECTIVE
Generate a set of business rules that operationalize the agent.
Each rule must follow this logic:
Trigger d Condition d Decision d Action d System d Expected Outcome d Escalation
Do not simply rewrite the provided actions. Convert them into explicit business conditions and decision rules that a business analyst, solution architect or developer could use to design the implementation.
BUSINESS RULE DESIGN PRINCIPLES
1. Trigger Rules
Define exactly when the agent should become active.
Examples:
When a new application enters the relevant process stage
When an SLA threshold is approaching
When required information is missing
When an exception is detected
When a case is returned for rework
When a document or response remains outstanding
Do not invent triggers that are not supported by the provided context.
2. Condition Rules
For each trigger, identify the conditions that must be evaluated.
Use deterministic conditions wherever possible.
Examples:
Required document present / absent
Information complete / incomplete
SLA within threshold / breached
Case status = pending
Number of rework loops > defined threshold
Required approval completed / pending
Data mismatch detected
Exception category identified
If an actual threshold is not provided, write:
"Threshold to be configured based on business policy."
Do not invent numerical thresholds.
3. AI Decision Rules
Identify where AI reasoning or classification is appropriate.
AI may be used for:
Document classification
Information extraction
Unstructured-text analysis
Root-cause classification
Prioritization
Recommendation generation
Semantic comparison
Exception categorization
AI must NOT independently override:
Credit policy
Regulatory controls
Authorization
Mandatory approval requirements
Financial limits
Compliance rules
Other deterministic business controls
When an AI recommendation affects a controlled business decision, route the decision to the appropriate human/system approval mechanism.
4. Action Rules
For every condition, specify the action the agent should take.
Actions may include:
Retrieve information
Validate information
Classify the case
Enrich the case
Create a task
Update a workflow status
Route the case
Prioritize the case
Send an internal notification
Prepare a recommendation
Request human approval
Update an approved system record
Do not assume the agent has write access to a system unless that capability is explicitly provided.
5. System Rules
Map every action to one of the provided systems.
Use only:
{systems}
If the required system capability is not available, state:
"System integration/API required."
Do not invent system names, APIs or integrations.
6. Human-in-the-Loop Rules
Clearly identify actions that require human approval.
Human approval should be required when the action:
Makes or changes a credit decision
Overrides a business or compliance rule
Communicates a consequential decision to a customer
Commits the organization financially
Changes an authoritative business record where approval is required
Resolves a policy exception
For each human approval point specify:
Who approves d What they review d What decision they make d What happens after approval/rejection
7. Exception Rules
Define what happens when the agent cannot complete the action.
Examples:
Required information unavailable
System unavailable
Data conflict detected
Confidence below configured threshold
Business rule violation
Human approval rejected
Multiple conflicting records found
The agent should not guess when required information is unavailable.
8. Stop Conditions
Define when the agent should stop processing.
Examples:
Required information is complete
Validation passes
Case successfully routed
Human approval is pending
Exception has been escalated
Target system update is completed
No further action is required
REQUIRED OUTPUT
Return the following structure.
Agent Business Rules
Rule ID	Trigger	Condition	Decision	Agent Action	System	Expected Outcome	Human Approval	Exception / Escalation
BR-01	...	...	...	...	...	...	...	...
BR-02	...	...	...	...	...	...	...	...
Decision Logic
Provide the business logic as a simple sequence:
Trigger


Evaluate Condition


Decision


Agent Action


System Interaction


Validation


Expected Outcome


Human Approval / Escalation if required


Complete / Continue / Escalate
Agent Guardrails
Provide 508 concise rules covering:
What the agent is allowed to do
What the agent cannot do
What requires deterministic validation
What requires human approval
What happens when information is missing
What happens when systems are unavailable
What happens when confidence is insufficient
What constitutes successful completion
IMPORTANT CONSTRAINTS
Do not invent business policies.
Do not invent numerical thresholds.
Do not invent systems.
Do not invent APIs.
Do not invent approval authorities.
Do not create actions that are outside the agent's stated capability.
Do not claim that an action was completed unless the required system capability exists.
Separate AI reasoning from deterministic business rules.
Treat business rules and authorization as authoritative over AI recommendations.
Use the provided Trigger, Actions and Expected Outcome as the primary source of truth.
Make the rules sufficiently precise for conversion into workflow logic, API orchestration or implementation requirements.
Keep the rules concise and business-oriented.
FINAL PRINCIPLE
The output should answer five questions for every agent:
When should it act?
What should it check?
What should it do?
Where should it do it?
When should it stop or ask a human?
"""

ARCHITECTURE_LLD_PROMPT = """ROLE
You are a Senior Solution Architect and AI/Agentic Application Architect.
Your task is to create an Architecture-Specific Low-Level Design (LLD) for an enterprise AI agent based strictly on the provided business context, business rules, trigger, actions, expected outcome, systems, and technical constraints.
The LLD must be detailed enough for:
Solution architects to validate the architecture
Developers to implement the solution
Integration teams to define APIs/interfaces
Data engineers to implement data flows
AI engineers to implement the agent/LLM components
Security teams to review access and data controls
QA teams to derive test scenarios
Do not produce a generic architecture document. Produce an implementation-oriented technical design for this specific agent.
INPUT CONTEXT
Agent Name
{agent_name}
Agent ID
{agent_id}
Agent Capability
{agent_capability}
Business Problem / Root Cause
{root_cause}
Tier 1 KPI
{tier1_kpi}
Tier 2 Diagnostic KPI
{tier2_kpi}
Gap Reference
{gap_reference}
Trigger
{trigger}
Business Rules
{business_rules}
Agent Actions
{actions}
Expected Outcome
{expected_outcome}
Human-in-the-Loop Requirement
{human_in_loop}
Systems Available to the Agent
{systems}
Existing APIs / Interfaces
{existing_apis}
Data Sources
{data_sources}
AI / LLM Model
{llm_model}
Technical Environment
{technical_environment}
Security / Compliance Constraints
{security_constraints}
Non-Functional Requirements
{nfrs}
PRIMARY OBJECTIVE
Translate the business rules into a technically implementable architecture.
The design must clearly answer:
How is the agent triggered?
How does the request enter the solution?
Which component orchestrates the agent?
Which data is retrieved?
Which systems/APIs are called?
Which steps use deterministic logic?
Which steps use AI/LLM reasoning?
Which tools does the agent have access to?
How are decisions validated?
Where is human approval required?
What data is written back?
How are failures handled?
How is the complete execution audited?
How is the solution secured?
How is the solution deployed and scaled?
IMPORTANT ARCHITECTURE PRINCIPLE
The AI/LLM must NOT be treated as the final authority for deterministic business decisions.
Separate the solution into:
Deterministic Layer
Responsible for:
Business rules
Validation
Authorization
Threshold checks
Policy enforcement
Workflow state transitions
System updates
Audit requirements
AI Layer
Responsible only for tasks appropriate for AI, such as:
Classification
Information extraction
Document understanding
Semantic analysis
Reasoning
Summarization
Recommendation
Prioritization
Natural-language generation
Human Layer
Responsible for decisions requiring human authorization, judgment or accountability.
Clearly identify these three boundaries throughout the architecture.
REQUIRED LLD STRUCTURE
1. Document Control
Provide:
Field	Value
Document Name 	
Agent Name 	
Agent ID 	
Version 	
Status 	
Author 	
Date 	
2. Purpose and Scope
Describe:
Purpose of the agent
Business problem addressed
Root cause addressed
Process stage
In-scope functionality
Out-of-scope functionality
Assumptions
Dependencies
Do not introduce capabilities that are not present in the input.
3. Agent Functional Overview
Describe the agent in one concise paragraph.
Then provide:
Attribute	Description
Trigger 	
Input 	
Processing 	
Decision 	
Action 	
Output 	
Expected Outcome 	
Human Approval 	
4. High-Level Architecture
Create an architecture representation containing the following logical components where applicable:
Channel / Event Source

API / Event Listener

Agent Orchestrator

Business Rule Engine

Data Retrieval / Context Layer

AI / LLM Layer

Validation Layer

Tool / System Integration Layer

Human Approval

Target System Update

Audit / Monitoring
Do not include components that are not required by the specific agent.
For every component explain:
Responsibility
Input
Output
Dependencies
Technology if provided
Interface
5. Component-Level Design
For each component provide:
Component	Responsibility	Input	Processing	Output	Technology	Dependencies
Describe the following components where relevant:
5.1 Trigger Handler
Explain:
Event/request received
Trigger validation
Authentication
Request validation
Idempotency handling
5.2 Agent Orchestrator
Explain:
How the agent execution is initiated
Sequence of operations
Agent state management
Tool invocation
Retry handling
Timeout handling
Completion criteria
5.3 Business Rule Engine
Explain:
Rules evaluated
Rule priority
Deterministic decisions
Threshold evaluation
Validation
Policy enforcement
5.4 Context/Data Retrieval Layer
Explain:
Data required
Data source
Retrieval mechanism
Filtering
Data validation
Context passed to the AI component
5.5 AI/LLM Component
Explain:
Model used
Prompt/input structure
Context supplied
Expected output
Structured output format
Guardrails
Confidence handling
Hallucination controls
The LLM must not directly modify authoritative business data.
5.6 Tool / Integration Layer
For every system interaction specify:
System
Purpose
API/tool
Method
Request
Response
Authentication
Timeout
Retry
Error handling
If an API is not currently available, explicitly state:
"Client API/integration dependency."
Do not invent an API.
6. Detailed Runtime Flow
Describe the complete execution flow step-by-step.
Use this format:
1. Trigger received
       
2. Authenticate request
       
3. Validate trigger conditions
       
4. Retrieve required data
       
5. Apply deterministic business rules
       
6. Invoke AI reasoning where required
       
7. Validate AI output
       
8. Determine next action
       
9. Execute system action / request human approval
       
10. Validate transaction result
       
11. Update process state
       
12. Write audit record
       
13. Return outcome
Adapt the sequence to the actual agent.
7. Business Rule to Technical Component Mapping
Map every business rule to its technical implementation.
Business Rule	Rule Type	Technical Component	Implementation	System	Outcome
BR-01	Deterministic	 			
BR-02	AI-assisted	 			
BR-03	Human approval	 			
Rule types must be one of:
Deterministic
AI-assisted
Human decision
System validation
8. AI vs Deterministic Decision Boundary
Explicitly identify every processing step.
Process Step	AI/LLM	Deterministic Rule	Human	Reason
Classification				
Validation				
Recommendation				
Credit decision				
System update				
The architecture must ensure that AI output cannot bypass deterministic controls.
9. Agent Tools
Define the tools available to the agent.
For every tool provide:
Tool	Purpose	Input	Output	Read/Write	System	Authorization
Clearly distinguish:
Read Tools
Tools that retrieve information.
Write Tools
Tools that modify systems.
Write tools require stronger validation and authorization controls.
10. API / Interface Design
For each interface define:
API Name
{api_name}
Purpose
{purpose}
Direction
Inbound / Outbound
Method
GET / POST / PUT / PATCH / Event
Request
{
}
Response
{
}
Error Response
{
}
Authentication
{authentication}
Authorization
{authorization}
Timeout
{timeout}
Retry
{retry}
Do not invent API fields if they are not available in the input.
Where the API specification is unavailable, identify it as:
API specification to be confirmed with client.
11. Data Design
Identify all data required by the agent.
Data Element	Source	Purpose	Type	Required	Sensitivity
Describe:
Input data
Context data
AI prompt/context data
Intermediate state
Output data
Audit data
Clearly identify authoritative data sources.
12. AI Prompt and Context Architecture
Describe how context is constructed for the LLM.
Include:
Business Context
      +
Case Data
      +
Retrieved Documents/Data
      +
Business Rules
      +
Agent Instructions
      
Prompt / Structured Input
      
LLM
      
Structured Output
      
Validation
      
Business Decision / Recommendation
Specify:
What data is included
What data is excluded
Prompt structure
Output schema
Structured output validation
Token/context considerations
Sensitive-data handling
Do not include unnecessary data in the LLM context.
13. Human-in-the-Loop Design
For every human approval point specify:
Approval Point	Trigger	Information Presented	Human Decision	System Action After Approval	Action After Rejection
Clearly identify:
AI recommendation 3 final business decision
unless the business process explicitly authorizes autonomous execution.
14. Error Handling and Exception Management
Define handling for:
Invalid input
Missing data
Data inconsistency
LLM failure
LLM timeout
Invalid LLM response
Low-confidence response
Business-rule failure
API failure
System timeout
Authentication failure
Authorization failure
Human rejection
Duplicate request
Partial transaction failure
For every exception specify:
Detection d Response d Retry d Escalation d Audit
15. Security Architecture
Describe:
Authentication
How users, systems and agents authenticate.
Authorization
What the agent is permitted to read/write.
Data Protection
Encryption in transit
Encryption at rest
Sensitive data handling
PII protection
AI Security
Prompt injection controls
Tool authorization
Output validation
Data leakage prevention
Model access control
Audit
Capture:
Agent invocation
User/request ID
Input context reference
Business rules executed
AI recommendation
Tool calls
Human approvals
System updates
Final outcome
16. Observability and Monitoring
Define monitoring for:
Business Metrics
Cases processed
Cases successfully resolved
Exceptions
SLA impact
Rework reduction
Processing time
Agent Metrics
Agent execution time
Tool calls
Success/failure rate
Human escalation rate
AI response validation failures
AI Metrics
Token usage
Latency
Model errors
Structured-output failures
Guardrail violations
Do not invent target values unless provided.
17. Performance and Scalability
Describe:
Expected transaction volume
Concurrent execution
Processing latency
API rate limits
LLM throughput
Queueing
Retry strategy
Horizontal scaling
Caching where applicable
If volume or SLA is not provided, explicitly identify it as:
"To be confirmed during technical design."
18. Deployment Architecture
Describe:
Runtime environment
Application services
AI/LLM service
Database
Integration layer
Queue/event infrastructure
Secrets management
Monitoring
Network boundaries
Provide a logical deployment view such as:
User / Business System
        |
        v
API Gateway
        |
        v
Agent Service
        |
   +----+----+
   |         |
   v         v
Rules      LLM Service
Engine        |
   |          |
   +----+-----+
        |
        v
Integration Layer
        |
   +----+----+
   |         |
   v         v
System A   System B
        |
        v
Audit / Monitoring
Only include components supported by the provided technical context.
19. Sequence Diagram
Create a detailed sequence showing:
Trigger d Agent d Rule Engine d Data Source d LLM d Validation d Human d Target System d Audit
For each interaction show:
Request
Response
Decision
Error path where relevant
20. Technology Mapping
Provide:
Architecture Component	Technology	Purpose	Environment
Agent API				
Orchestrator				
LLM				
Data Store				
Integration				
Monitoring				
Use only technologies explicitly provided.
Do not recommend a technology unless the input explicitly asks for technology recommendations.
21. Non-Functional Requirements
Address:
Security
Availability
Scalability
Performance
Reliability
Maintainability
Auditability
Observability
Data privacy
Explainability
Recoverability
For each requirement describe the architectural mechanism used to address it.
22. Risks and Dependencies
Provide:
Risk / Dependency	Impact	Mitigation	Owner
Explicitly identify:
Missing APIs
Client system dependencies
Data availability
Authentication dependencies
Model dependencies
Human approval dependencies
Infrastructure dependencies
23. Assumptions
Clearly separate assumptions from confirmed facts.
Never present an assumption as an existing capability.
24. Implementation Breakdown
Convert the LLD into implementation components.
Component	Development Activity	Dependency	Output
Agent service			
Rule engine			
LLM integration			
API integration			
Validation			
Audit			
Monitoring			
25. Traceability
Create an end-to-end traceability matrix:
Business Requirement	Business Rule	Agent Action	Technical Component	API/System	Test Scenario
Every business rule must map to at least one technical implementation component.
Every agent action must map to a technical component or system interaction.
Every write operation must map to an authorization and validation mechanism.
26. Test Scenarios
Generate implementation-oriented test scenarios covering:
Positive scenarios
Normal successful execution.
Negative scenarios
Invalid or incomplete data.
Boundary scenarios
Threshold/edge conditions.
Exception scenarios
System/API/LLM failures.
Security scenarios
Unauthorized access and unauthorized tool invocation.
AI scenarios
Incorrect classification, invalid structured output, hallucination and low-confidence responses.
Human approval scenarios
Approve, reject and timeout.
Use:
Test ID	Scenario	Input Condition	Expected Agent Behaviour	Expected System Outcome
DESIGN CONSTRAINTS
The following rules are mandatory:
Do not invent systems.
Do not invent APIs.
Do not invent database tables.
Do not invent business policies.
Do not invent numerical thresholds.
Do not invent authorization roles.
Do not assume write access.
Clearly distinguish existing capability from proposed capability.
Clearly distinguish AI processing from deterministic processing.
AI output must be validated before it influences a business process.
AI must not bypass authorization or business rules.
Every external system interaction must have an explicit interface.
Every write operation must have validation and authorization.
Every important decision must be auditable.
If required information is unavailable, explicitly mark it as TBD / Client Confirmation Required rather than inventing it.
FINAL OUTPUT QUALITY CHECK
Before producing the LLD, verify:
Business Alignment
Does every component address the stated business problem?
Does every business rule have a technical implementation?
Architecture
Is the complete end-to-end flow documented?
Are component responsibilities clear?
Are system boundaries clear?
AI
Is the AI responsibility clearly separated from deterministic rules?
Is LLM output validated?
Are AI/tool guardrails defined?
Integration
Are all system interactions identified?
Are missing APIs clearly marked as dependencies?
Human Control
Are consequential decisions routed through appropriate approval?
Security
Are authentication, authorization and audit addressed?
Implementation Readiness
Could a development team begin implementation using this document?
Are all unknowns explicitly identified instead of being invented?
Produce the final document in a professional enterprise architecture format with clear headings, tables, architecture diagrams in Mermaid where appropriate, sequence diagrams and implementation-level detail.
"""

CODEBASE_PROMPT = """ROLE
You are a Senior Application Engineer and AI Agent Codebase Architect.
Your task is to translate the supplied agents, systems, runtime architecture and generated-files
into an implementation-ready Agent Codebase section for a governed AI transformation package.

INPUT
You will receive the following context:
- Agents (deterministic list)
- Event-log systems and process stages
- Generated code stubs and config files (generated_files)
- Deployment/runtime environment

OBJECTIVE
Produce a `codebase` JSON object that:
- Describes each governed agent implementation
- Describes shared code and configuration
- Describes runtime and deployment units
- References only the provided agents and systems
- Aligns with the governed, human-in-the-loop operating model

REQUIRED STRUCTURE
The `codebase` object must be structured as follows (fields may be null if not provided):
- repository: Authoritative repository name for the agent codebase.
- entrypoint: Primary orchestrator or service entry point (for example, "agents/orchestrator.py").
- deployment_unit: High-level deployment unit (for example, "Containerized FastAPI service").
- agents: Array with one entry per governed agent, where each entry contains:
  - agent_id: Stable identifier for the agent.
  - name: Human-readable name.
  - description: Purpose / capability in business language.
  - language: Implementation language (for example, "Python" only if confirmed).
  - framework: Application framework if explicitly provided; otherwise set to "To be confirmed".
  - entrypoint: File path within the codebase that serves as the agent entrypoint.
  - human_approval_required: true/false/null depending on the provided human-in-loop flag.
  - systems: Systems this agent integrates with (from the provided systems list only).
- shared_code: Optional section describing shared modules, with:
  - files: Array of shared file descriptors (path, purpose, language).
- generated_files: The authoritative list of generated files for this package.
  - Each entry must map 1:1 to the provided generated file descriptors
    (path, language, purpose, code). Do not invent additional files.
- testing: Optional section describing test scaffolds or test entrypoints.
- source_agents: Array of agent IDs present in the package.

DESIGN PRINCIPLES
- Do NOT add or remove agents.
- Do NOT invent new systems, languages, frameworks or files.
- Use the deterministic generated_files list as the ground truth for paths and filenames.
- When a detail is not provided, prefer:
  - "To be confirmed" for textual descriptions.
  - null for booleans.
- Align all descriptions with the governed, recommendation-only model: agents prepare
  recommendations and require human approval for consequential actions.

OUTPUT FORMAT
Return a JSON object with a top-level `codebase` field shaped as above.
Do not wrap the JSON in markdown fences.
"""

DEPLOYMENT_YAML_PROMPT = """ROLE
You are a Deployment Architect for governed AI agents.
Your task is to convert the provided runtime architecture, systems and agents into a set of
Deployment YAML descriptors that can be reviewed by architects and DevOps engineers.

INPUT
You will receive:
- Agents and their systems
- Event-log systems and stages
- Deterministic deployment context (environment, feature flag, runtime description)
- Any existing deployment YAML snippets

OBJECTIVE
Produce a `deployment_config` JSON object that:
- Groups YAML content into reviewable files
- Aligns with the governed, human-in-the-loop operating model
- Does NOT introduce new systems, clusters, or secrets

REQUIRED STRUCTURE
The `deployment_config` object should have:
- environment: Target environment name (for example, "staged" or "production").
- feature_flag: Name of the feature flag controlling activation, if provided.
- files: An array of deployment-file descriptors, each with:
  - path: File name (for example, "deployment.yaml", "integration.yaml").
  - kind: Short label such as "deployment", "integration", "monitoring", or "rollback".
  - purpose: Human-readable description of what this file configures.
  - content: The YAML content as a single string.
- notes: Optional textual notes explaining assumptions or TBD items.

YAML DESIGN GUIDANCE
- Use only the agents and systems supplied in the context.
- Do not invent new clusters, namespaces, or external systems.
- Do not include secrets, keys, or passwords.
- Treat all images, hostnames and sensitive values as placeholders
  (for example, ${AI_AGENT_IMAGE}, ${ENV_VAR_NAME}).
- If a required deployment dimension is unknown, indicate it as
  "To be confirmed during technical design" inside the notes or comments.

OUTPUT FORMAT
Return a JSON object with a top-level `deployment_config` field shaped as above.
The YAML strings must be valid YAML fragments but do not need to be complete manifests.
Do not wrap the JSON in markdown fences.
"""

def _azure_content(request: PackageContext, fallback: dict[str, Any]) -> dict[str, Any]:
    """Use the configured Azure deployment in smaller batches, with a deterministic fallback."""
    if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_API_KEY:
        return fallback

    endpoint = (
        settings.AZURE_OPENAI_ENDPOINT.rstrip("/")
        + "/openai/deployments/"
        + quote(settings.AZURE_OPENAI_DEPLOYMENT, safe="")
        + "/chat/completions"
    )
    url = endpoint + "?api-version=" + settings.AZURE_OPENAI_API_VERSION

    context_payload = {
        "package_context": request.model_dump(),
        "fallback": fallback,
    }

    def _call_batch(keys: list[str], extra_instruction: str, max_tokens: int) -> dict[str, Any]:
        instruction = (
            "Create a concise JSON loan transformation deployment package fragment. "
            f"Return a compact JSON object with ONLY these top-level keys: {', '.join(keys)}. "
            "Do not wrap the object in markdown fences. "
            "Use the exact agents, process-flow systems, and future-state steps supplied in the context. "
            "Integrations must reference only those systems. Never include secrets and never "
            "recommend automatic credit approval. "
            + extra_instruction
            + " Context: "
            + _json(context_payload)
        )
        body = _json(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a governed corporate-loan architecture assistant.",
                    },
                    {"role": "user", "content": instruction},
                ],
                "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
        ).encode()

        # Keep Azure calls best-effort only. Short timeouts and limited retries ensure the
        # deployment package endpoint always returns promptly and never blocks on Azure.
        for attempt in range(3):
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "api-key": settings.AZURE_OPENAI_API_KEY},
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    result = json.loads(response.read().decode())
                content = result["choices"][0]["message"]["content"]
                # response_format=json_object should already be strict JSON; fall back to
                # brace-slicing only when necessary and never treat parse failures as fatal.
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    text = content[content.find("{") : content.rfind("}") + 1]
                    return json.loads(text)
            except urllib.error.HTTPError as exc:  # retry 408/429 with backoff; 5xx/504 fall back
                if exc.code in {500, 502, 503, 504}:
                    return {}
                retryable = exc.code in {408, 429}
                if not retryable or attempt == 2:
                    return {}
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(retry_after) if retry_after else 2**attempt
                except (TypeError, ValueError):
                    delay = 2**attempt
                time.sleep(min(max(delay, 1.0), 5.0))
            except (urllib.error.URLError, TimeoutError):
                # Network failure or timeout: fall back after a couple of quick retries.
                if attempt == 2:
                    return {}
                time.sleep(2.0)
            except (KeyError, ValueError, json.JSONDecodeError):
                # Malformed provider response – skip this batch and rely on deterministic content.
                if attempt == 2:
                    return {}
                time.sleep(1.0)

        return {}

    try:
        generated = {**fallback}

        # Batch 1: executive summary and transformation overview
        batch1 = _call_batch(
            ["executive_summary", "transformation_overview"],
            "Focus on KPI variance, root-cause narrative, and why the transformation is needed.",
            max_tokens=2000,
        )
        if isinstance(batch1, dict):
            generated.update(batch1)

        # Batch 2: agents, business rules, integrations, and architecture
        batch2_instruction = (
            "Use ONLY the agents and systems from the context. Do not add or remove agents. "
            "For the `business_rules` field, follow the detailed Business Rule Designer prompt below and "
            "encode its required output as a JSON object under `business_rules` (for example, with keys such as "
            "`agent_business_rules`, `decision_logic`, and `agent_guardrails`). "
            "For the `architecture` field, follow the Architecture-Specific Low-Level Design prompt below and "
            "encode its required output as a JSON object under `architecture`.\n\n"
            "BUSINESS RULES PROMPT:\n" + BUSINESS_RULES_PROMPT + "\n\nARCHITECTURE LLD PROMPT:\n" + ARCHITECTURE_LLD_PROMPT
        )
        batch2 = _call_batch(
            ["agents", "business_rules", "integrations", "architecture"],
            batch2_instruction,
            max_tokens=2500,
        )
        if isinstance(batch2, dict):
            generated.update(batch2)

        # Batch 3: codebase, deployment config, runtime, and monitoring
        batch3_instruction = (
            "Generate enriched but concise technical details for the `codebase`, `deployment_config`, "
            "`approval_workflow`, `runtime`, and `monitoring_testing_rollback` fields. "
            "For the `codebase` field, follow the detailed Agent Codebase prompt below and encode its "
            "required output as a JSON object under `codebase`. "
            "For the `deployment_config` field, follow the Deployment YAML prompt below and encode its "
            "required output as a JSON object under `deployment_config`.\n\n"
            "CODEBASE PROMPT:\n" + CODEBASE_PROMPT + "\n\nDEPLOYMENT YAML PROMPT:\n" + DEPLOYMENT_YAML_PROMPT
        )
        batch3 = _call_batch(
            [
                "codebase",
                "deployment_config",
                "approval_workflow",
                "runtime",
                "monitoring_testing_rollback",
            ],
            batch3_instruction,
            max_tokens=2500,
        )
        if isinstance(batch3, dict):
            generated.update(batch3)

        return generated
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError):
        # Any provider failure should fall back to deterministic content instead of
        # failing the deployment-package request.
        return fallback


def _enforce_authoritative_sections(generated: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """Keep model narrative, but never allow it to drift from the deterministic contract."""
    # Agents and integrations remain fully deterministic – Azure cannot add or remove them.
    for key in ("agents", "integrations"):
        generated[key] = fallback[key]

    # Allow Azure to enrich the codebase, but guarantee a valid scaffold exists.
    codebase = generated.get("codebase") if isinstance(generated.get("codebase"), dict) else {}
    fallback_codebase = fallback.get("codebase", {})
    if not codebase:
        codebase = dict(fallback_codebase)
    if not isinstance(codebase.get("generated_files"), list):
        codebase["generated_files"] = fallback_codebase.get("generated_files", [])
    codebase.setdefault("repository", fallback_codebase.get("repository"))
    codebase.setdefault("entrypoint", fallback_codebase.get("entrypoint"))
    codebase.setdefault("deployment_unit", fallback_codebase.get("deployment_unit"))
    generated["codebase"] = codebase

    # Deployment configuration: always include deterministic environment, feature flag and files.
    fallback_deployment = fallback.get("deployment_config") or {}
    deployment = generated.get("deployment_config")
    if not isinstance(deployment, dict):
        deployment = dict(fallback_deployment) if isinstance(fallback_deployment, dict) else {}
    if isinstance(fallback_deployment, dict):
        for key in ("environment", "feature_flag"):
            if key in fallback_deployment and key not in deployment:
                deployment[key] = fallback_deployment[key]
        if not isinstance(deployment.get("files"), list) and isinstance(fallback_deployment.get("files"), list):
            deployment["files"] = fallback_deployment["files"]
    generated["deployment_config"] = deployment

    # Runtime sequence must always be present.
    if not generated.get("runtime"):
        generated["runtime"] = fallback["runtime"]

    generated["approval_workflow"] = {
        **fallback["approval_workflow"],
        **(generated.get("approval_workflow") if isinstance(generated.get("approval_workflow"), dict) else {}),
        "approval_status": fallback["approval_workflow"]["approval_status"],
        "re_request_supported": True,
    }
    return generated


def _db():
    conn = get_connection()
    with conn.cursor() as cur:
        schema = settings.DB_SCHEMA.replace('"', '""')
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        cur.execute(f'''CREATE TABLE IF NOT EXISTS "{schema}".deployment_packages (
            package_id TEXT PRIMARY KEY, version INTEGER NOT NULL, source_context JSONB NOT NULL,
            generated_content JSONB NOT NULL, readiness JSONB NOT NULL, approvals JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
        cur.execute(f'''CREATE TABLE IF NOT EXISTS "{schema}".approval_tokens (
            token_hash TEXT PRIMARY KEY, package_id TEXT NOT NULL REFERENCES "{schema}".deployment_packages(package_id) ON DELETE CASCADE,
            expires_at TIMESTAMPTZ NOT NULL, used_at TIMESTAMPTZ, decision TEXT, reviewer TEXT, comment TEXT)''')
    conn.commit()
    return conn


def _json(value): return json.dumps(value, default=str)
def _id(source): return "loan-pkg-" + hashlib.sha256(_json(source).encode()).hexdigest()[:16]
def _mail_configured() -> bool:
    """Expose only whether the minimum approval mail settings are present."""
    return bool(settings.SMTP_HOST and settings.APPROVAL_RECIPIENTS)


def _approval_items_from_env(approvals: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    approvals = approvals or {}
    items = approvals.get("items")
    if isinstance(items, list) and items:
        return items

    recipients = (settings.APPROVAL_RECIPIENTS or "").replace(";", ",")
    emails = [part.strip() for part in recipients.split(",") if part.strip()]
    parsed: list[dict[str, Any]] = []
    for index, raw in enumerate(emails, start=1):
        name: str | None = None
        email = raw
        match = re.match(r"^(?P<name>.+?)<(?P<email>[^>]+)>$", raw)
        if match:
            name = match.group("name").strip()
            email = match.group("email").strip()
        title = name or f"Approver {index}"
        parsed.append({"id": f"approver-{index}", "title": title, "email": email})

    approvals["items"] = parsed
    return parsed


def _package_response(row, package_id):
    content = row["generated_content"]
    approvals = row.get("approvals") or {}
    if isinstance(approvals, dict):
        _approval_items_from_env(approvals)
    return {
        "package": row,
        "package_id": package_id,
        "version": row["version"],
        "generated_content": content,
        "package_artifacts": content.get("artifacts") or {},
        "readiness": row["readiness"],
        "approvals": approvals,
        "mail_configured": _mail_configured(),
    }


def _approval_content(content: Any, status: str) -> dict[str, Any]:
    content = dict(content or {})
    workflow = dict(content.get("approval_workflow") or {})
    workflow["approval_status"] = status
    workflow["re_request_supported"] = True
    content["approval_workflow"] = workflow
    return content


def _row(row):
    if not row:
        raise HTTPException(404, "Deployment package was not found")
    return dict(row)


def _load_cached_package(package_id: str) -> dict[str, Any] | None:
    """Return a cached deployment package for this package_id if it can be reused."""
    try:
        conn = _db()
        schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT * FROM "{schema}".deployment_packages WHERE package_id=%s',
                (package_id,),
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        data = dict(row)
        content = data.get("generated_content")
        if not isinstance(content, dict):
            return None
        cache_version = content.get("cache_version")
        # Only reuse cache entries written with the current cache version. Older
        # cache entries are effectively ignored so previous Azure runs are
        # treated as invalidated.
        if cache_version != PACKAGE_CACHE_VERSION:
            return None
        return data
    except Exception:
        # Any cache read issue should fall back to regeneration rather than fail the request.
        return None


@router.post("/deployment-packages", status_code=201)
@router.post("/generate", status_code=201)
def generate_package(request: PackageContext):
    source = request.model_dump()
    package_id = _id(source)

    cached = _load_cached_package(package_id)
    if cached:
        return _package_response(cached, package_id)

    fallback = _content(request)
    generated = _enforce_authoritative_sections(_azure_content(request, fallback), fallback)
    generated["cache_version"] = PACKAGE_CACHE_VERSION
    generated["artifacts"] = _artifacts(request, generated)
    try:
        conn = _db()
        schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            # Upsert the deployment package so a new run refreshes cached content,
            # readiness, and approvals for this package_id.
            cur.execute(
                f'''INSERT INTO "{schema}".deployment_packages
                    (package_id, version, source_context, generated_content, readiness, approvals)
                    VALUES (%s, 1, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
                    ON CONFLICT (package_id) DO UPDATE SET
                      source_context = EXCLUDED.source_context,
                      generated_content = EXCLUDED.generated_content,
                      readiness = EXCLUDED.readiness,
                      approvals = EXCLUDED.approvals''',
                (
                    package_id,
                    _json(source),
                    _json(generated),
                    _json({"status": "READY_FOR_REVIEW", "blocking_items": []}),
                    _json({"status": "NOT_REQUESTED", "required": True, "items": _approval_items_from_env({"status": "NOT_REQUESTED", "required": True})}),
                ),
            )
            cur.execute(f'SELECT * FROM "{schema}".deployment_packages WHERE package_id=%s', (package_id,))
            row = cur.fetchone()
        conn.commit(); conn.close()
        return _package_response(row, package_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Database is unavailable: {exc}") from exc


@router.get("/deployment-packages/{package_id}")
def get_package(package_id: str):
    try:
        conn = _db(); schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            cur.execute(f'SELECT * FROM "{schema}".deployment_packages WHERE package_id=%s', (package_id,))
            row = cur.fetchone()
        conn.close(); row = _row(row)
        return _package_response(row, package_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Database is unavailable: {exc}") from exc


def _send_email(package_id: str, token: str):
    if not settings.SMTP_HOST or not settings.APPROVAL_RECIPIENTS:
        return False
    link = f"{settings.APPROVAL_BASE_URL.rstrip('/')}/approval/{token}"
    msg = EmailMessage(); msg["Subject"] = f"Loan deployment approval: {package_id}"
    msg["From"] = settings.SMTP_FROM; msg["To"] = settings.APPROVAL_RECIPIENTS
    conn = _db(); schema = settings.DB_SCHEMA.replace('"', '""')
    with conn.cursor() as cur:
        cur.execute(f'SELECT generated_content, readiness, approvals FROM "{schema}".deployment_packages WHERE package_id=%s', (package_id,))
        package = cur.fetchone()
    conn.close()
    content = package["generated_content"] if package else {}
    approval_status = (package.get("approvals") or {}).get("status", "PENDING") if package else "PENDING"
    def text(value):
        if isinstance(value, list):
            return "".join(f"<li>{text(item)}</li>" for item in value)
        if isinstance(value, dict):
            return "".join(f"<tr><th>{key.replace('_', ' ').title()}</th><td>{text(item)}</td></tr>" for key, item in value.items())
        return str(value if value is not None else "—")
    sections = "".join(
        f'<section><h2>{key.replace("_", " ").title()}</h2>'
        f'<table>{text(value)}</table></section>'
        for key, value in content.items()
    )
    html = f"""<!doctype html><html><body style="margin:0;background:#0d111b;color:#e7edf7;font-family:Arial,sans-serif">
    <div style="max-width:760px;margin:0 auto;padding:32px">
    <div style="border-bottom:1px solid #2a3548;padding-bottom:20px"><p style="color:#55a8ff;font-size:12px;text-transform:uppercase;letter-spacing:1px">Kinetic governance</p>
    <h1 style="margin:0;font-size:26px">Corporate Loan Deployment Package</h1><p style="color:#9aa8bc">{package_id}</p></div>
    <div style="margin:22px 0;padding:16px;border:1px solid #2a3548;border-radius:8px;background:#171f2d"><strong>Approval status: {approval_status}</strong><p>Review the complete deployment summary and record your decision. A new request can be sent after a reset.</p>
    <a href="{link}" style="display:inline-block;padding:11px 18px;background:#55a8ff;color:#07111f;text-decoration:none;border-radius:6px;font-weight:bold">Open approval screen</a></div>
    {sections}</div></body></html>"""
    msg.set_content(f"Review package {package_id} and approve or reject it here:\n{link}")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
        smtp.send_message(msg)
    return True


@router.post("/deployment-packages/{package_id}/request-approval")
def request_approval(package_id: str):
    token = secrets.token_urlsafe(32); token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        conn = _db(); schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            cur.execute(f'SELECT generated_content FROM "{schema}".deployment_packages WHERE package_id=%s', (package_id,))
            package = cur.fetchone()
            if not package: raise HTTPException(404, "Deployment package was not found")
            cur.execute(f'''INSERT INTO "{schema}".approval_tokens (token_hash, package_id, expires_at) VALUES (%s,%s,NOW())''', (token_hash, package_id))
            cur.execute(f'''UPDATE "{schema}".deployment_packages SET approvals=%s::jsonb, updated_at=NOW() WHERE package_id=%s''',
                        (_json({"status": "PENDING", "required": True, "items": _approval_items_from_env({"status": "PENDING", "required": True})}), package_id))
            cur.execute(f'''UPDATE "{schema}".deployment_packages SET generated_content=%s::jsonb WHERE package_id=%s''',
                        (_json(_approval_content(package["generated_content"], "PENDING")), package_id))
        conn.commit(); conn.close()
        emailed = _send_email(package_id, token)
        return {
            "package_id": package_id,
            "status": "PENDING",
            "mail_configured": _mail_configured(),
            "email_sent": emailed,
            "message": (
                "Approval email sent."
                if emailed
                else "Approval recorded, but email delivery is not configured."
            ),
        }
    except HTTPException: raise
    except Exception as exc: raise HTTPException(503, f"Approval workflow unavailable: {exc}") from exc


@router.post("/deployment-packages/{package_id}/reset-approval")
def reset_approval(package_id: str):
    """Invalidate old approval links and allow a fresh request after any prior decision."""
    try:
        conn = _db(); schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            cur.execute(f'SELECT package_id FROM "{schema}".deployment_packages WHERE package_id=%s', (package_id,))
            if not cur.fetchone():
                raise HTTPException(404, "Deployment package was not found")
            cur.execute(f'DELETE FROM "{schema}".approval_tokens WHERE package_id=%s', (package_id,))
            cur.execute(
                f'UPDATE "{schema}".deployment_packages SET approvals=%s::jsonb, updated_at=NOW() WHERE package_id=%s',
                (_json({"status": "NOT_REQUESTED", "required": True, "items": _approval_items_from_env({"status": "NOT_REQUESTED", "required": True}), "reset_at": datetime.now(timezone.utc).isoformat()}), package_id),
            )
            cur.execute(f'SELECT generated_content FROM "{schema}".deployment_packages WHERE package_id=%s', (package_id,))
            content = cur.fetchone()["generated_content"]
            cur.execute(f'UPDATE "{schema}".deployment_packages SET generated_content=%s::jsonb WHERE package_id=%s',
                        (_json(_approval_content(content, "NOT_REQUESTED")), package_id))
        conn.commit(); conn.close()
        return {"package_id": package_id, "status": "NOT_REQUESTED", "message": "Approval status reset. A new approval request can be submitted."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Approval workflow unavailable: {exc}") from exc


def _token_row(token: str):
    conn = _db(); schema = settings.DB_SCHEMA.replace('"', '""')
    with conn.cursor() as cur:
        cur.execute(f'''SELECT t.*, p.version, p.generated_content, p.readiness, p.approvals FROM "{schema}".approval_tokens t JOIN "{schema}".deployment_packages p USING(package_id)
                        WHERE t.token_hash=%s''', (hashlib.sha256(token.encode()).hexdigest(),))
        row = cur.fetchone()
    conn.close(); return row


@router.get("/approval-tokens/{token}")
@router.get("/approval/{token}")
@router.get("/deployment-packages/approval-token/{token}")
def lookup_approval(token: str):
    row = _token_row(token)
    if not row: raise HTTPException(404, "Approval token is invalid")
    return {"package_id": row["package_id"], "status": row["decision"] or "PENDING", "expires_at": row["expires_at"],
            "version": row["version"], "generated_content": row["generated_content"],
            "readiness": row["readiness"], "approvals": row["approvals"]}


@router.post("/approval-tokens/{token}/decision")
@router.post("/approval/{token}/decision")
def decide_approval(token: str, decision: ApprovalDecision, package_id: str | None = None):
    if decision.token != token: raise HTTPException(400, "Token in request does not match URL")
    if decision.decision.upper() not in {"APPROVED", "REJECTED"}: raise HTTPException(400, "Decision must be APPROVED or REJECTED")
    row = _token_row(token)
    if not row or (package_id and row["package_id"] != package_id): raise HTTPException(404, "Approval token is invalid")
    conn = _db(); schema = settings.DB_SCHEMA.replace('"', '""')
    approvals = {"status": decision.decision.upper(), "required": True, "reviewer": decision.reviewer, "comment": decision.comment}
    approvals["items"] = _approval_items_from_env({"status": approvals["status"], "required": True})
    with conn.cursor() as cur:
        cur.execute(f'UPDATE "{schema}".approval_tokens SET used_at=NOW(), decision=%s, reviewer=%s, comment=%s WHERE token_hash=%s', (decision.decision.upper(), decision.reviewer, decision.comment, hashlib.sha256(token.encode()).hexdigest()))
        cur.execute(f'UPDATE "{schema}".deployment_packages SET approvals=%s::jsonb, updated_at=NOW() WHERE package_id=%s', (_json(approvals), row["package_id"]))
        cur.execute(f'SELECT generated_content FROM "{schema}".deployment_packages WHERE package_id=%s', (row["package_id"],))
        content = cur.fetchone()["generated_content"]
        cur.execute(f'UPDATE "{schema}".deployment_packages SET generated_content=%s::jsonb WHERE package_id=%s',
                    (_json(_approval_content(content, decision.decision.upper())), row["package_id"]))
    conn.commit(); conn.close()
    return {"package_id": row["package_id"], **approvals}


@router.post("/deployment-packages/{package_id}/change-request")
@router.post("/deployment-packages/{package_id}/request-change")
def change_request(package_id: str, body: dict[str, Any] | None = None):
    body = body or {}
    package = get_package(package_id)
    try:
        conn = _db(); schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            cur.execute(f'UPDATE "{schema}".deployment_packages SET readiness=%s::jsonb, updated_at=NOW() WHERE package_id=%s',
                        (_json({"status": "CHANGES_REQUESTED", "change_request": body}), package_id))
        conn.commit(); conn.close()
    except Exception as exc: raise HTTPException(503, f"Database is unavailable: {exc}") from exc
    return {"package_id": package_id, "status": "CHANGES_REQUESTED", "change_request": body}
