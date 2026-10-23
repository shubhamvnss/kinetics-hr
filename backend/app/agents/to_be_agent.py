from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.core.azure_openai import generate_json
from app.core.config import settings
from app.schemas.to_be_transformation import (
    NO_SYSTEM_IN_LOG,
    TO_BE_RESPONSE_FORMAT,
    ToBeDesignLLM,
)


class ToBeError(RuntimeError):
    """The To-Be design could not be produced or validated."""


SYSTEM_PROMPT = """CORPORATE LOAN - TO-BE TRANSFORMATION DESIGNER

Redesign the supplied representative case's actual activity sequence using only the agents in
recommended_agents. Do not select, invent, rename, add, or remove agents.

ABSOLUTE RULES
1. Return exactly one activity per current-state row. current_sequence must match that row's
   sequence, exactly once. Do not add or omit activities.
2. responsible_agent must exactly match an allowed recommended_agents name and may be set only
   when change_type is AI Agent. Otherwise it must be null.
3. change_type must be Retained, Automated (Deterministic), AI Agent, Combined, Eliminated, or
   Human. Combined rows share to_be_sequence and to_be_activity. Explain eliminated work.
4. human_in_the_loop must be true for customer communication, credit/compliance overrides, bank
   commitments, and formal approvals.
5. system_used may contain only values in case_systems, or the exact literal
   "Client API / integration dependency - system not present in event log" when genuinely needed.
6. Never invent metrics, case ids, activities, or systems. Ground explanations in the supplied KPI,
   gap, root-cause, and event evidence.
7. Preserve historical current-state order; only the designed to-be sequence may consolidate work.

Write concise, specific banking-operations language. Return only the required structured JSON."""


def build_payload(context: dict[str, Any]) -> str:
    return json.dumps(
        {
            "case_id": context.get("case_id"),
            "stages": context.get("stages"),
            "current_state": context.get("current_state"),
            "tier_1_kpi": context.get("tier1_kpi"),
            "tier_2_kpis": context.get("tier2_kpis"),
            "documented_gaps": context.get("gaps"),
            "root_cause": context.get("root_cause_summary"),
            "recommended_agents": context.get("recommended_agents"),
            "case_systems": context.get("case_systems"),
        },
        separators=(",", ":"),
        default=str,
    )


def _validate_coverage(plan: ToBeDesignLLM, current_state: list[dict[str, Any]]) -> str | None:
    expected = [row["sequence"] for row in current_state]
    actual = [activity.current_sequence for activity in plan.activities]
    if len(actual) != len(set(actual)):
        return "The response contains duplicate current_sequence values."
    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)
    if missing:
        return f"The response omitted current-state sequences: {sorted(missing)}."
    if extra:
        return f"The response contains unknown current-state sequences: {sorted(extra)}."
    if len(actual) != len(expected):
        return "The response must contain exactly one item for every current-state activity."
    return None


def _invented_agents(plan: ToBeDesignLLM, allowed_names: list[str]) -> list[str]:
    lookup = {name.strip().casefold() for name in allowed_names if name.strip()}
    return list(dict.fromkeys(
        activity.responsible_agent
        for activity in plan.activities
        if activity.responsible_agent
        and activity.responsible_agent.strip().casefold() not in lookup
    ))


def _enforce_agent_consistency(plan: ToBeDesignLLM) -> None:
    for activity in plan.activities:
        if activity.change_type != "AI Agent":
            activity.responsible_agent = None


def _invented_systems(plan: ToBeDesignLLM, allowed_systems: list[str]) -> list[str]:
    lookup = {system.strip().casefold() for system in allowed_systems if system.strip()}
    return list(dict.fromkeys(
        system
        for activity in plan.activities
        for system in activity.system_used
        if system != NO_SYSTEM_IN_LOG and system.strip().casefold() not in lookup
    ))


def _strip_invented_systems(plan: ToBeDesignLLM, allowed_systems: list[str]) -> None:
    lookup = {system.strip().casefold() for system in allowed_systems if system.strip()}
    for activity in plan.activities:
        activity.system_used = [
            system
            for system in activity.system_used
            if system == NO_SYSTEM_IN_LOG or system.strip().casefold() in lookup
        ]


def _call_model(client: Any, payload: str, correction: str | None) -> dict[str, Any]:
    user_input = payload if not correction else (
        f"{payload}\n\nYour previous answer was rejected: {correction}\n"
        "Return a corrected answer that satisfies the schema and every rule."
    )
    if client is None:
        return generate_json(SYSTEM_PROMPT, user_input, TO_BE_RESPONSE_FORMAT)

    response = client.responses.create(
        model=settings.AZURE_OPENAI_DEPLOYMENT,
        instructions=SYSTEM_PROMPT,
        input=[{"role": "user", "content": user_input}],
        text={"format": TO_BE_RESPONSE_FORMAT},
        max_output_tokens=8000,
    )
    if getattr(response, "status", None) == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", "unknown")
        raise ToBeError(f"The model returned an incomplete response ({reason}).")
    for item in getattr(response, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal":
                raise ToBeError("The model declined the To-Be transformation request.")
    parsed = json.loads(getattr(response, "output_text", "") or "")
    if not isinstance(parsed, dict):
        raise ValueError("The model response must be a JSON object.")
    return parsed


def build_deterministic_fallback(context: dict[str, Any]) -> ToBeDesignLLM:
    """Preserve observed work and suggest reviewed agent support without model output."""
    agents = context.get("recommended_agents") or []
    primary_agent = agents[0].get("name") if agents and isinstance(agents[0], dict) else None
    allowed_systems = {
        system.strip().casefold()
        for system in context.get("case_systems") or []
        if str(system).strip()
    }
    activities = []

    for row in context.get("current_state") or []:
        sequence = int(row["sequence"])
        activity_name = str(row.get("activity_name") or row.get("step_id") or f"Observed activity {sequence}")
        needs_agent = any(
            bool(row.get(flag))
            for flag in ("manual_touch", "rework", "exception", "handoff")
        )
        change_type = "AI Agent" if needs_agent and primary_agent else "Retained"
        raw_systems = row.get("system_used") or []
        if not isinstance(raw_systems, (list, tuple, set)):
            raw_systems = [raw_systems]
        systems = [
            str(system).strip()
            for system in raw_systems
            if str(system).strip() and str(system).strip().casefold() in allowed_systems
        ]
        activities.append({
            "current_sequence": sequence,
            "to_be_sequence": sequence,
            "to_be_activity": (
                f"Prepare reviewed agent recommendation for: {activity_name}"
                if change_type == "AI Agent"
                else activity_name
            ),
            "change_type": change_type,
            "responsible_agent": primary_agent if change_type == "AI Agent" else None,
            "deterministic_automation": False,
            "human_in_the_loop": change_type == "AI Agent" or bool(row.get("approval_step")),
            "system_used": systems,
            "trigger": "When this observed activity is reached in the process.",
            "action": (
                "Review the observed event evidence and prepare a recommendation; do not execute a change."
                if change_type == "AI Agent"
                else "Continue the observed process activity."
            ),
            "expected_outcome": (
                "A human-reviewed recommendation grounded in the observed event evidence."
                if change_type == "AI Agent"
                else "The observed activity remains in the process."
            ),
            "reason_for_change": (
                "Conservative fallback preserves observed process work and requires human review."
                if change_type == "AI Agent"
                else "Conservative fallback preserves the observed current-state activity."
            ),
            "kpi_gap_addressed": str(
                (context.get("root_cause_summary") or {}).get("title")
                or (context.get("root_cause_summary") or {}).get("description")
                or "Supplied root-cause evidence"
            ),
        })

    return ToBeDesignLLM.model_validate({
        "stage_summary": (
            "Deterministic fallback preserves every observed activity; flagged work is presented "
            "as a human-reviewed agent recommendation."
        ),
        "activities": activities,
    })


def generate(context: dict[str, Any], client: Any = None) -> tuple[ToBeDesignLLM, str]:
    """Generate a validated To-Be design covering every current activity exactly once."""
    current_state = context.get("current_state") or []
    if not current_state:
        raise ToBeError("No current-state activities were supplied for the To-Be design.")

    allowed_names = list(context.get("allowed_agent_names") or [])
    allowed_systems = list(context.get("case_systems") or [])
    payload = build_payload(context)
    correction = None
    last_error = None

    for attempt in (1, 2):
        try:
            plan = ToBeDesignLLM.model_validate(_call_model(client, payload, correction))
            coverage_error = _validate_coverage(plan, current_state)
            if coverage_error:
                raise ValueError(coverage_error)

            _enforce_agent_consistency(plan)
            offenders = _invented_agents(plan, allowed_names)
            if offenders:
                raise ValueError(f"Unapproved agents were returned: {', '.join(offenders)}.")

            system_offenders = _invented_systems(plan, allowed_systems)
            if system_offenders:
                if attempt == 1:
                    raise ValueError(f"Systems not present in the event log were returned: {', '.join(system_offenders)}.")
                _strip_invented_systems(plan, allowed_systems)

            return plan, settings.AZURE_OPENAI_DEPLOYMENT
        except ToBeError:
            raise
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            last_error = str(error)
            correction = last_error[:1500]

    raise ToBeError(f"The To-Be design could not be validated: {last_error}")